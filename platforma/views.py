import datetime
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import openpyxl
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import ExtractYear
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ai, aviz_email, curs as curs_mod, forms as f, models as m, plati as plati_mod, sms, stergere, status_investitie as stinv
from .models import suma

User = get_user_model()
from .coduri import nr_inregistrare_nou, urmatorul_cod_locatie
from .hg import CAPITOLE, LINII_ECHIPAMENTE, SUBCAPITOLE, capitol_din, creeaza_linii
from .permisiuni import (e_admin, e_financiar, poate_aviza_rz, poate_bloca_buget, poate_crea_proiect, poate_edita,
                         poate_edita_financiar, poate_modifica_buget, poate_vedea, proiecte_vizibile, rol, vede_tot)


# Registrul activelor împărțit în 3: magazinele retail (linia Retail) separat; restul după status.
CATEGORII_ACTIVE = [
    ("functionale", "Active funcționale", "bi-check-circle"),
    ("nefunctionale", "Active nefuncționale", "bi-pause-circle"),
    ("teren", "Teren investiții", "bi-map"),
    ("retail", "Magazine retail", "bi-shop"),
]
def filtru_categorie(qs, categorie):
    """Categoriile din meniu = starea activului (setată de administrator, cu istoric)."""
    stare = {"functionale": "functional", "nefunctionale": "nefunctional", "teren": "teren", "retail": "retail"}.get(categorie)
    return qs.filter(stare_activ=stare) if stare else qs


def context_global(request):
    if not request.user.is_authenticated:
        return {}
    nr = len(plati_mod.plati_de_aprobat(request.user))
    if poate_aviza_rz(request.user):
        nr += m.Proiect.objects.filter(stare_rz="trimis").count()
    nr += len(stinv.cereri_de_aprobat(request.user))
    ctx = {"rol_curent": rol(request.user), "e_admin": e_admin(request.user), "e_financiar": e_financiar(request.user),
           "poate_crea_proiect": poate_crea_proiect(request.user), "nr_aprobari": nr}
    url = getattr(request.resolver_match, "url_name", "") or ""
    if "locati" in url:  # subcategoriile registrului de active din meniu, cu numărul de poziții
        toate = m.Locatie.objects.all()
        ctx["categorii_active"] = [(cod, et, ic, filtru_categorie(toate, cod).count()) for cod, et, ic in CATEGORII_ACTIVE]
        ctx["categorie_curenta"] = request.GET.get("categorie", "")
        ctx["total_active"] = toate.count()
    return ctx


def _proiect_vizibil(request, pk):
    p = get_object_or_404(m.Proiect, pk=pk)
    if not poate_vedea(request.user, p):
        raise PermissionDenied("Nu aveți acces la acest proiect.")
    return p


def _proiect_editabil(request, pk):
    p = _proiect_vizibil(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied("Nu aveți drept de editare pe acest proiect.")
    return p


# ------------------------------------------------------------------ dashboard
@login_required
def dashboard(request):
    proiecte = list(proiecte_vizibile(request.user).prefetch_related("linii_buget", "facturi", "contracte"))
    azi = timezone.localdate()
    in_30 = azi + datetime.timedelta(days=30)
    ids = [p.pk for p in proiecte]
    ctx = {
        "nr_locatii": m.Locatie.objects.count(),
        "nr_fara_cod": m.Locatie.objects.filter(cod__isnull=True).count(),
        "nr_proiecte": len(proiecte),
        "nr_active": sum(1 for p in proiecte if p.status not in ("Finalizat", "Anulat/Suspendat", "Anulat", "Inactiv")),
        "buget_total": sum((p.buget_actualizat_ron or p.buget_rz_ron for p in proiecte), Decimal(0)),
        "facturat_total": sum((p.facturat_ron for p in proiecte), Decimal(0)),
        "platit_total": sum((p.platit_ron for p in proiecte), Decimal(0)),
        "intarziate": [p for p in proiecte if p.intarziat],
        "avize_expira": m.Aviz.objects.filter(proiect_id__in=ids, valabil_pana__lte=in_30, status="obtinut").select_related("proiect"),
        "obligatii": m.Obligatie.objects.filter(proiect_id__in=ids, status="deschisa", termen__lte=in_30).select_related("proiect"),
        "rz_de_avizat": m.Proiect.objects.filter(stare_rz="trimis") if poate_aviza_rz(request.user) else [],
        "linii_depasite": [l for l in m.LinieBuget.objects.filter(proiect_id__in=ids).select_related("proiect") if l.depasit],
        "pe_faza": proiecte_vizibile(request.user).values("faza").annotate(n=Count("id")).order_by("-n"),
        **_suprafete_pe_linii(),
        **_valoare_piata_grup(),
        **_kpi_detinere(azi),
        "azi": azi,
    }
    return render(request, "platforma/dashboard.html", ctx)


def _kpi_detinere(azi):
    """KPI separate: active proprietate grup vs. active închiriate de la proprietari externi."""
    locatii = list(m.Locatie.objects.select_related("proprietar", "proprietar_extern"))
    proiecte = list(m.Proiect.objects.filter(locatie__isnull=False).prefetch_related("linii_buget__rectificari"))
    pe_locatie = {}
    for p in proiecte:
        pe_locatie.setdefault(p.locatie_id, []).append(p)
    curs = m.CursValutar.la_data()
    rez = {}
    for cheie, extern in (("grup", False), ("extern", True)):
        lst = [l for l in locatii if l.inchiriere_externa == extern]
        retail = [l for l in lst if l.stare_activ == "retail"]
        functionale = [l for l in lst if l.stare_activ == "functional"]
        terenuri = [l for l in lst if l.stare_activ == "teren"]
        pr = [p for l in lst for p in pe_locatie.get(l.pk, [])]
        r = {
            "n": len(lst), "functionale": len(functionale), "retail": len(retail), "terenuri": len(terenuri),
            "nefunctionale": len(lst) - len(functionale) - len(retail) - len(terenuri),
            "teren": sum((l.suprafata_teren or 0 for l in lst), Decimal(0)),
            "constructii": sum((l.suprafata_constructii or l.suprafata_construita_totala or 0 for l in lst), Decimal(0)),
            "proiecte": len(pr), "buget": sum((p.buget_actualizat_ron or p.buget_rz_ron for p in pr), Decimal(0)),
        }
        if extern:
            chirii = [l for l in lst if l.chirie_lunara]
            r["chirie_ron"] = sum((l.chirie_lunara_ron for l in chirii), Decimal(0))
            r["chirie_eur"] = (r["chirie_ron"] / curs).quantize(Decimal("1")) if curs else None
            r["fara_chirie"] = len(lst) - len(chirii)
            r["proprietari"] = len({l.proprietar_extern_id for l in lst if l.proprietar_extern_id})
            r["expira"] = sorted([l for l in lst if l.inchiriere_expira and (l.inchiriere_expira - azi).days <= 90],
                                 key=lambda l: l.inchiriere_expira)
            r["fara_termen"] = sum(1 for l in lst if not l.inchiriere_expira)
        rez[cheie] = r
    return {"kpi_grup": rez["grup"], "kpi_extern": rez["extern"]}


def _valoare_piata_grup():
    """Valoarea de piață consolidată a grupului: suma ultimelor rapoarte de evaluare ale activelor deținute de grup."""
    curs = m.CursValutar.la_data()
    tot = {"eur": Decimal(0), "evaluate": 0, "fara_evaluare": 0, "excluse": 0, "excluse_eur": Decimal(0)}
    linii, societati, fara, excluse = {}, {}, [], []
    qs = m.Locatie.objects.select_related("proprietar").prefetch_related("rapoarte_evaluare")
    for l in qs:
        inclus, motiv = l.regula_valoare_grup
        e = l.evaluare_curenta
        v = l.valoare_piata("EUR") if e else None
        if not inclus:
            if v:
                tot["excluse"] += 1
                tot["excluse_eur"] += v
                excluse.append({"loc": l, "eur": v, "motiv": motiv, "ev": e})
            continue
        if not v:
            tot["fara_evaluare"] += 1
            if l.cod:
                fara.append(l)
            continue
        tot["eur"] += v
        tot["evaluate"] += 1
        for d, cheie in ((linii, (l.linie or "").strip() or "Nespecificată"), (societati, str(l.proprietar))):
            r = d.setdefault(cheie.upper(), {"nume": cheie, "eur": Decimal(0), "n": 0, "vechi": 0})
            r["eur"] += v
            r["n"] += 1
            if e.data < timezone.localdate() - datetime.timedelta(days=3 * 365):
                r["vechi"] += 1
    tot["ron"] = tot["eur"] * curs
    def ordonat(d):
        lst = sorted(d.values(), key=lambda r: -r["eur"])
        for r in lst:
            r["ron"] = r["eur"] * curs
            r["p"] = float(r["eur"] / tot["eur"] * 100) if tot["eur"] else 0
        return lst
    return {"valoare_grup": tot, "valoare_linii": ordonat(linii), "valoare_societati": ordonat(societati),
            "valoare_fara": fara, "valoare_excluse": sorted(excluse, key=lambda r: -r["eur"]), "curs_curent": curs}


def _suprafete_pe_linii():
    """Suprafețe agregate (teren, construcții) pe linii de business și, în cadrul lor, pe divizii."""
    linii = {}
    def nou(eticheta):
        return {"linie": eticheta, "active": 0, "teren": Decimal(0), "constructii": Decimal(0), "cu_teren": 0, "cu_constr": 0}
    def adauga(r, l, c):
        r["active"] += 1
        if l.suprafata_teren:
            r["teren"] += l.suprafata_teren
            r["cu_teren"] += 1
        if c:
            r["constructii"] += c
            r["cu_constr"] += 1
    for l in m.Locatie.objects.filter(cod__isnull=False).only("linie", "divizie", "suprafata_teren", "suprafata_constructii", "suprafata_construita_totala"):
        cheie = (l.linie or "").strip() or "Nespecificată"
        r = linii.setdefault(cheie.upper(), {**nou(cheie), "divizii": {}})
        c = l.suprafata_constructii or l.suprafata_construita_totala
        adauga(r, l, c)
        if l.divizie:
            adauga(r["divizii"].setdefault(l.divizie, nou(l.divizie)), l, c)
    randuri = sorted(linii.values(), key=lambda r: (-r["constructii"], -r["active"]))
    tot = {k: sum((r[k] for r in randuri), 0) for k in ("active", "teren", "constructii", "cu_teren", "cu_constr")}
    max_c = max((r["constructii"] for r in randuri), default=0) or 1
    max_t = max((r["teren"] for r in randuri), default=0) or 1
    for r in randuri:
        r["pc"], r["pt"] = float(r["constructii"] / max_c * 100), float(r["teren"] / max_t * 100)
        r["lista_divizii"] = sorted(r["divizii"].values(), key=lambda d: -d["constructii"])
        for d in r["lista_divizii"]:
            d["pc"], d["pt"] = float(d["constructii"] / max_c * 100), float(d["teren"] / max_t * 100)
    return {"suprafete_linii": randuri, "suprafete_total": tot}


# ------------------------------------------------------------------ active
def _filtreaza_locatii(request):
    qs = m.Locatie.objects.select_related("proprietar", "proprietar_extern")
    g = request.GET
    qs = filtru_categorie(qs, g.get("categorie"))
    if g.get("detinut") == "extern":
        qs = qs.filter(inchiriere_externa=True)
    elif g.get("detinut") == "grup":
        qs = qs.filter(inchiriere_externa=False)
    if g.get("q"):
        q = g["q"]
        qs = qs.filter(Q(cod__icontains=q) | Q(denumire__icontains=q) | Q(adresa__icontains=q) | Q(utilizator__icontains=q) | Q(proprietar__denumire__icontains=q)
                       | Q(proprietar_extern__denumire__icontains=q)
                       | Q(carti_funciare__icontains=q) | Q(cod_original__icontains=q) | Q(obiectiv_functional__icontains=q))
    if g.get("tip"):
        qs = qs.filter(tip=g["tip"])
    if g.get("linie"):
        qs = qs.filter(linie__iexact=g["linie"])
    if g.get("divizie"):
        qs = qs.filter(divizie__iexact=g["divizie"])
    if g.get("status"):
        qs = qs.filter(status__iexact=g["status"])
    if g.get("proprietar"):
        qs = qs.filter(proprietar_id=g["proprietar"])
    if g.get("cod") == "fara":
        qs = qs.filter(cod__isnull=True)
    elif g.get("cod") == "cu":
        qs = qs.filter(cod__isnull=False)
    return qs


def _norm(t):
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


# Coloanele tabelului din registrul activelor: (parametru URL, eticheta, text căutabil)
GOL = "(gol)"
COLOANE_MULTI = {"f_stare", "f_utilizator", "f_proprietar", "f_linie", "f_divizie", "f_status", "f_judet"}
COLOANE_ACTIVE = [
    ("f_cod", "Cod", lambda l: f"{l.cod or 'fara cod'} {l.cod_original} {l.cod_alias}"),
    ("f_denumire", "Denumire", lambda l: l.denumire),
    ("f_stare", "Categorie", lambda l: l.get_stare_activ_display()),
    ("f_utilizator", "Utilizator", lambda l: l.utilizator),
    ("f_proprietar", "Proprietar", lambda l: l.proprietar_afisat or ""),
    ("f_linie", "Linie", lambda l: l.linie),
    ("f_divizie", "Divizie", lambda l: l.divizie),
    ("f_status", "Status", lambda l: l.status),
    ("f_judet", "Județ", lambda l: l.judet),
    ("f_teren", "Teren (mp)", lambda l: "" if l.suprafata_teren is None else f"{l.suprafata_teren:.0f}"),
    ("f_proiecte", "Proiecte", lambda l: l.nr_proiecte or ""),
]


def _etichete_filtre(query):
    """Filtrele active ca etichete, fiecare cu adresa paginii fără acel filtru (pentru „×”)."""
    nume_col = {p: et for p, et, _ in COLOANE_ACTIVE}
    fixe = {"q": "Căutare", "linie": "Linie", "divizie": "Divizie", "status": "Status"}
    valori_fixe = {("detinut", "grup"): "Doar proprietate grup", ("detinut", "extern"): "Doar închirieri externe",
                   ("cod", "cu"): "Doar cu cod", ("cod", "fara"): "Fără cod alocat"}
    etichete = []
    for k in query:
        if k in ("categorie", "focus", "p"):
            continue
        for v in query.getlist(k):
            if not v.strip():
                continue
            if k in fixe:
                et = f"{fixe[k]}: {v}"
            elif (k, v) in valori_fixe:
                et = valori_fixe[(k, v)]
            elif k.startswith("m_") and k[2:] in nume_col:
                et = f"{nume_col[k[2:]]}: {v}"
            elif k in nume_col:
                et = f"{nume_col[k]} conține „{v}”"
            else:
                continue
            q = query.copy()
            q.setlist(k, [x for x in query.getlist(k) if x != v])
            etichete.append((et, "?" + q.urlencode()))
    return etichete


@login_required
def locatii_lista(request):
    qs = _filtreaza_locatii(request).annotate(nr_proiecte=Count("proiecte")).order_by(F("cod").asc(nulls_last=True), "denumire")
    # filtre text pe coloane: fără diferență de majuscule și diacritice, aplicate pe server
    criterii = [(fn, _norm(request.GET.get(param))) for param, _, fn in COLOANE_ACTIVE if request.GET.get(param, "").strip()]
    # filtre multiple (listă cu bife) pe coloanele cu valori repetate: m_<coloană>=valoare1&m_<coloană>=valoare2
    baza = list(qs)
    val = lambda fn, l: str(fn(l) or "").strip() or GOL
    optiuni, multi = {}, []
    for param, _, fn in COLOANE_ACTIVE:
        if param not in COLOANE_MULTI:
            continue
        numar = {}
        for l in baza:
            numar[val(fn, l)] = numar.get(val(fn, l), 0) + 1
        alese = request.GET.getlist(f"m_{param}")
        optiuni[param] = [(v, n, v in alese) for v, n in sorted(numar.items(), key=lambda x: _norm(x[0]))]
        if alese:
            multi.append((fn, set(alese)))
    qs = baza
    if criterii or multi:
        qs = [l for l in qs if all(v in _norm(fn(l)) for fn, v in criterii) and all(val(fn, l) in alese for fn, alese in multi)]
    if request.GET.get("export"):
        return _export_xlsx("registru_active", [
            ("Cod", "cod"), ("Cod Excel", "cod_original"), ("Nr. înreg.", "nr_registru"), ("Denumire", "denumire"),
            ("Categorie", "get_stare_activ_display"), ("Proprietar", "proprietar_afisat"), ("Închiriere externă", "inchiriere_externa"),
            ("Firma din grup (proprietar / chiriaș)", "proprietar"), ("Contract închiriere", "contract_inchiriere"),
            ("Chirie lunară", "chirie_lunara"), ("Monedă chirie", "moneda_chirie"), ("Valabil până la", "inchiriere_expira"),
            ("Utilizator", "utilizator"), ("Grup", "grup"),
            ("Linie", "linie"), ("Divizie", "divizie"), ("Status", "status"), ("Județ", "judet"), ("Adresă", "adresa"), ("CF", "carti_funciare"),
            ("Deținere", "get_detinere_display"),
            ("Supr. teren (mp)", "suprafata_teren"), ("Supr. construcții (mp)", "suprafata_constructii"),
            ("Supr. construită totală (mp)", "suprafata_construita_totala"),
            ("Valoare garantare EUR", "valoare_garantare_eur"), ("Coordonate", "coordonate"),
        ], qs)
    pagina = list(qs)  # toate pozițiile pe o singură pagină
    ctx = {
        "pagina": pagina, "total": len(pagina),
        "linii": m.Locatie.objects.exclude(linie="").values_list("linie", flat=True).distinct().order_by("linie"),
        "statusuri": m.Locatie.objects.exclude(status="").values_list("status", flat=True).distinct().order_by("status"),
        "divizii": m.Locatie.objects.exclude(divizie="").values_list("divizie", flat=True).distinct().order_by("divizie"),
        "proprietari": m.Societate.objects.filter(locatii__isnull=False).distinct(),
        "coloane": [(param, et, request.GET.get(param, ""), optiuni.get(param), sum(1 for o in optiuni.get(param, []) if o[2]))
                    for param, et, _ in COLOANE_ACTIVE],
        "filtre_coloane": bool(criterii or multi),
        "are_filtre": any(v.strip() for k, v in request.GET.items() if k not in ("focus", "p", "categorie")),
        "categorie": dict((c, (et, ic)) for c, et, ic in CATEGORII_ACTIVE).get(request.GET.get("categorie")),
        "cod_categorie": request.GET.get("categorie", "") if request.GET.get("categorie") in dict((c, 1) for c, _, _ in CATEGORII_ACTIVE) else "",
        "query": request.GET.copy(),
    }
    ctx["query"].pop("p", None)
    ctx["query"].pop("focus", None)
    fara_coloane = ctx["query"].copy()
    for k in list(fara_coloane):
        if k.startswith(("f_", "m_f_")):
            fara_coloane.pop(k)
    ctx["url_fara_coloane"] = "?" + fara_coloane.urlencode()
    ctx["filtre_active"] = _etichete_filtre(ctx["query"])
    ctx["total_teren"] = sum((l.suprafata_teren or 0 for l in pagina), Decimal(0))
    return render(request, "platforma/locatii_lista.html", ctx)


@login_required
def locatii_harta(request):
    puncte = [
        {"id": l.pk, "cod": l.cod or "", "den": l.denumire, "lat": l.lat, "lng": l.lng, "tip": l.stare_activ, "categorie": l.get_stare_activ_display(), "status": l.status}
        for l in _filtreaza_locatii(request).exclude(lat__isnull=True)
    ]
    return render(request, "platforma/locatii_harta.html", {"puncte": puncte})


FAZE_INCHISE = ("Finalizat", "Anulat/Suspendat", "Anulat", "Inactiv")


def matrice_autorizatii(loc, autorizatii=None, tipuri=None):
    """Lista standard de autorizații pentru un obiectiv: atașată / expirată / lipsă / nu e necesară / neurmărit."""
    autorizatii = autorizatii if autorizatii is not None else list(loc.autorizatii.prefetch_related("documente"))
    tipuri = tipuri if tipuri is not None else list(m.TipAutorizatie.objects.filter(activ=True).select_related("responsabil"))
    cerinte = {c.tip_id: c for c in loc.cerinte_autorizatii.select_related("responsabil")}
    in_lista = loc.in_lista_autorizatii
    randuri = []
    for t in tipuri:
        ale_tipului = sorted([a for a in autorizatii if a.tip == t.cod], key=lambda a: a.data, reverse=True)
        a = next((x for x in ale_tipului if x.valabila), ale_tipului[0] if ale_tipului else None)
        c = cerinte.get(t.pk)
        necesara = c.necesara if c else True
        if a and a.valabila:
            stare = "atasata"
        elif a:
            stare = "expirata"
        elif c and not c.necesara:
            stare = "nu_e_necesara"
        elif in_lista or (c and c.necesara):
            stare = "lipsa"
        else:
            stare = "neurmarit"
        suport_cerut = t.lista_suport
        suport_atasat = {d.titlu.lower(): d for d in a.documente_suport} if a else {}
        suport = [{"titlu": x, "doc": suport_atasat.get(x.lower())} for x in suport_cerut]
        randuri.append({"tip": t, "aut": a, "cerinta": c, "necesara": necesara, "stare": stare,
                        "responsabil": (c.responsabil if c and c.responsabil else t.responsabil), "suport": suport,
                        "suport_lipsa": sum(1 for x in suport if not x["doc"]) if a else 0,
                        "alte_suport": [d for k, d in suport_atasat.items() if k not in {x.lower() for x in suport_cerut}]})
    return randuri


@login_required
@require_POST
def cerinta_autorizatie(request, pk, tip_cod):
    """Bifa „nu e necesară” (cu motiv) și responsabilul autorizației pe obiectiv – doar administratorul."""
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    tip = get_object_or_404(m.TipAutorizatie, cod=tip_cod)
    c, _ = m.CerintaAutorizatie.objects.get_or_create(locatie=loc, tip=tip)
    actiune = request.POST.get("actiune")
    if actiune == "nu_e_necesara":
        c.necesara, c.motiv = False, request.POST.get("motiv", "").strip()[:200]
    elif actiune == "necesara":
        c.necesara, c.motiv = True, ""
    elif actiune == "responsabil":
        c.responsabil = User.objects.filter(pk=request.POST.get("responsabil") or 0).first()
    c.save()
    return redirect(f"{loc.get_absolute_url()}?tab=autorizatii")


@login_required
@require_POST
def urmarire_autorizatii(request, pk):
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    loc.urmareste_autorizatii = {"da": True, "nu": False}.get(request.POST.get("valoare"))
    loc.save(update_fields=["urmareste_autorizatii"])
    return redirect(f"{loc.get_absolute_url()}?tab=autorizatii")


@login_required
@require_POST
def consolidare_valoare(request, pk):
    """Include / exclude manual valoarea de piață a activului din valoarea consolidată a grupului."""
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    loc.consolideaza_valoare = {"da": True, "nu": False}.get(request.POST.get("valoare"))
    loc.save(update_fields=["consolideaza_valoare"])
    return redirect(f"{loc.get_absolute_url()}?tab=evaluari")


@login_required
@require_POST
def suport_autorizatie(request, pk, aut_pk):
    """Documente suport ale unei autorizații (ex. studiile din spatele autorizației de mediu)."""
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    aut = get_object_or_404(m.AutorizatieFunctionare, pk=aut_pk, locatie=loc)
    titlu = (request.POST.get("titlu") or "").strip()
    fisiere = request.FILES.getlist("fisiere")
    for f_ in fisiere:
        t = (titlu if titlu and len(fisiere) == 1 else (titlu + " – " if titlu else "") + f_.name.rsplit(".", 1)[0].replace("_", " "))[:255]
        anterior = m.Document.objects.filter(autorizatie=aut, categorie="anexa_autoriz", arhivat=False, titlu__iexact=t).first()
        d = m.Document(locatie=loc, autorizatie=aut, categorie="anexa_autoriz", titlu=t, fisier=f_, incarcat_de=request.user)
        if anterior:
            d.inlocuieste = anterior
        d.save()
    if fisiere:
        messages.success(request, f"{len(fisiere)} document(e) suport atașat(e) la {aut}.")
    return redirect(f"{loc.get_absolute_url()}?tab=autorizatii")


@login_required
def raport_autorizatii(request):
    """Raport pe tip de autorizație (ex. toate autorizațiile DSV), pentru toate obiectivele, cu responsabil și stare."""
    tipuri = list(m.TipAutorizatie.objects.filter(activ=True).select_related("responsabil"))
    g = request.GET
    tip = next((t for t in tipuri if t.cod == g.get("tip")), None)
    locatii = (m.Locatie.objects.exclude(cod__isnull=True).select_related("proprietar")
               .prefetch_related("autorizatii__documente", "cerinte_autorizatii__responsabil"))
    if g.get("linie"):
        locatii = locatii.filter(linie__iexact=g["linie"])
    if g.get("divizie"):
        locatii = locatii.filter(divizie__iexact=g["divizie"])
    randuri, sumar = [], {}
    for loc in locatii:
        aut = list(loc.autorizatii.all())
        for r in matrice_autorizatii(loc, aut, [tip] if tip else tipuri):
            if r["stare"] == "neurmarit":
                continue
            s = sumar.setdefault(r["tip"].cod, {"tip": r["tip"], "atasata": 0, "lipsa": 0, "expirata": 0, "nu_e_necesara": 0})
            s[r["stare"]] += 1
            if tip:
                r["loc"] = loc
                randuri.append(r)
    if g.get("stare"):
        randuri = [r for r in randuri if r["stare"] == g["stare"]]
    if g.get("responsabil"):
        randuri = [r for r in randuri if r["responsabil"] and str(r["responsabil"].pk) == g["responsabil"]]
    if tip and g.get("export"):
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = tip.scurt[:30]
        ws.append(["Cod", "Obiectiv", "Proprietar", "Linie", "Divizie", "Județ", "Stare", "Nr. autorizație", "Data", "Valabilă până la",
                   "Responsabil", "Documente suport lipsă", "Motiv (nu e necesară)"])
        et = {"atasata": "Atașată", "lipsa": "LIPSĂ", "expirata": "Expirată", "nu_e_necesara": "Nu e necesară"}
        for r in randuri:
            a, l = r["aut"], r["loc"]
            ws.append([l.cod, l.denumire, str(l.proprietar or ""), l.linie, l.divizie, l.judet, et[r["stare"]], a.numar if a else "",
                       a.data if a else None, a.valabil_pana if a else None,
                       (r["responsabil"].get_full_name() or r["responsabil"].username) if r["responsabil"] else "",
                       r["suport_lipsa"] or "", r["cerinta"].motiv if r["cerinta"] and not r["necesara"] else ""])
        buf = io.BytesIO(); wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="raport_{tip.cod}_{timezone.localdate():%Y%m%d}.xlsx"'
        return resp
    return render(request, "platforma/raport_autorizatii.html", {
        "tipuri": tipuri, "tip": tip, "randuri": randuri, "sumar": [sumar.get(t.cod) for t in tipuri if sumar.get(t.cod)],
        "query": g, "utilizatori": User.objects.filter(is_active=True).order_by("first_name"),
        "linii": m.Nomenclator.optiuni("linie"),
    })
CATEGORII_ANEXE = ("cadastru", "plan_topo", "anexa")
EXTENSII_POZE = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".tif", ".tiff")


def _istoric_activ(loc):
    """Istoricul complet al activului: schimbările de stare + jurnalul de audit al activului și al înregistrărilor lui
    (cărți funciare, autorizații, rapoarte de evaluare, documente etc.), cu valoarea veche și cea nouă a fiecărui câmp."""
    filtre = Q(model=m.Locatie._meta.verbose_name, obiect_id=str(loc.pk))
    etichete = {m.Locatie._meta.verbose_name: {f.attname: f.verbose_name for f in m.Locatie._meta.concrete_fields}}
    for rel in m.Locatie._meta.related_objects:
        if not rel.one_to_many or rel.related_model.__name__ in ("Proiect", "JurnalAudit"):
            continue
        model = rel.related_model
        ids = [str(x) for x in model.objects.filter(**{rel.field.name: loc}).values_list("pk", flat=True)]
        if ids:
            vn = model._meta.verbose_name
            filtre |= Q(model=vn, obiect_id__in=ids)
            etichete[vn] = {f.attname: f.verbose_name for f in model._meta.concrete_fields}
    alegeri = {"stare_activ": dict(m.Locatie.STARE), "stare_veche": dict(m.Locatie.STARE), "stare_noua": dict(m.Locatie.STARE), "tip": dict(m.Locatie.TIP), "detinere": dict(m.Locatie.DETINERE)}
    intrari = []
    for j in m.JurnalAudit.objects.filter(filtre).select_related("user")[:400]:
        lab = etichete.get(j.model, {})
        campuri = []
        for camp, (vechi, nou) in (j.modificari or {}).items():
            if camp in ("id", "creat_la", "modificat_la", "sha256", "fisier") or (j.actiune == "creare" and camp.endswith("_id")):
                continue
            if camp in alegeri:
                vechi, nou = alegeri[camp].get(vechi, vechi), alegeri[camp].get(nou, nou)
            campuri.append({"camp": lab.get(camp, camp.replace("_", " ")), "vechi": vechi, "nou": nou})
        intrari.append({"j": j, "campuri": campuri})
    return {"istoric_stare": loc.istoric_stare.select_related("user"), "istoric_modificari": intrari}


@login_required
def locatie_detaliu(request, pk):
    loc = get_object_or_404(m.Locatie.objects.select_related("proprietar"), pk=pk)
    vizibile = set(proiecte_vizibile(request.user).filter(locatie=loc).values_list("pk", flat=True))
    proiecte = list(loc.proiecte.select_related("pm", "coordonator", "beneficiar").prefetch_related("linii_buget__rectificari", "facturi"))
    inchise = [p for p in proiecte if p.status in FAZE_INCHISE or p.faza == "Finalizat"]
    deschise = [p for p in proiecte if p not in inchise]
    azi = timezone.localdate()
    autorizatii = list(loc.autorizatii.prefetch_related("documente"))
    controale = list(loc.controale.prefetch_related("documente"))
    ctx = {
        "loc": loc, "vizibile": vizibile, "deschise": deschise, "inchise": inchise,
        "total_deschise": sum((p.buget_actualizat_ron or p.buget_rz_ron for p in deschise), Decimal(0)),
        "total_inchise": sum((p.facturat_ron for p in inchise), Decimal(0)),
        "evaluari": loc.rapoarte_evaluare.prefetch_related("documente"),
        "valoare_inclusa": loc.regula_valoare_grup[0], "valoare_motiv": loc.regula_valoare_grup[1],
        "autorizatii": autorizatii, "controale": controale,
        "aut_expira": [a for a in autorizatii if a.zile_ramase is not None and a.zile_ramase <= 60],
        "matrice_aut": (mat := matrice_autorizatii(loc, autorizatii)),
        "aut_lipsa": sum(1 for r in mat if r["stare"] in ("lipsa", "expirata")),
        "aut_alte": [a for a in autorizatii if a.tip not in {r["tip"].cod for r in mat}],
        "standarde": list(loc.standarde.prefetch_related("documente")),
        "utilizatori": User.objects.filter(is_active=True).order_by("first_name", "username"),
        "ctrl_deschise": [c for c in controale if c.status == "deschis"],
        "documente": loc.documente.filter(arhivat=False, raport_evaluare__isnull=True, autorizatie__isnull=True, control__isnull=True,
                                          carte_funciara__isnull=True).exclude(categorie__in=CATEGORII_ANEXE + ("foto", "cf")),
        "carti": loc.carti.prefetch_related("documente"),
        "randuri_cf": _randuri_cf(loc),
        "extrase_neatasate": loc.documente.filter(categorie="cf", carte_funciara__isnull=True, arhivat=False),
        "verif_cf": (vc := _verificare_cf(loc)),
        "verif_cf_randuri": [("Teren", vc["teren"]), ("Construcții", vc["constructii"])],
        "contracte_folosinta": sorted(loc.contracte_folosinta.prefetch_related("documente"), key=lambda k: (not k.activ, -k.data.toordinal())),
        "total_cf": suma(loc.carti.all(), "suprafata_mp"),
        "anexe": loc.documente.filter(arhivat=False, categorie__in=CATEGORII_ANEXE).select_related("incarcat_de"),
        "poze": list(loc.documente.filter(arhivat=False, categorie="foto").order_by("incarcat_la", "pk")),
        "categorii_anexe": [(k, v) for k, v in m.Document.CATEGORII if k in CATEGORII_ANEXE],
        "form_doc": f.DocumentLocatieForm() if e_admin(request.user) else None,
        "urmator": loc.urmatorul_cod_proiect(), "tab": request.GET.get("tab", "prezentare"), "azi": azi,
    }
    ctx.update(_istoric_activ(loc))
    return render(request, "platforma/locatie_detaliu.html", ctx)


@login_required
def locatie_obiect_form(request, pk, tip, obj_pk=None):
    """Rapoarte de evaluare, autorizații de funcționare, controale / procese-verbale – gestionate de administrator."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul actualizează documentele activului.")
    loc = get_object_or_404(m.Locatie, pk=pk)
    if tip not in f.FORMULARE_LOCATIE:
        raise PermissionDenied
    Form, eticheta, tab = f.FORMULARE_LOCATIE[tip]
    obj = get_object_or_404(Form._meta.model, pk=obj_pk, locatie=loc) if obj_pk else None
    extragere, campuri_ai = None, set()
    initial = {k: v for k, v in request.GET.items() if k in Form.base_fields} if not obj else {}
    if tip == "folosinta" and not obj:
        initial.setdefault("folosit_de", loc.utilizator or (str(loc.proprietar) if loc.proprietar else ""))
        initial.setdefault("tip", {"concesiune": "concesiune", "inchiriat": "inchiriere"}.get(loc.detinere, "proprietate"))
    ex_id = request.POST.get("extragere") or request.GET.get("extragere")
    if tip == "autorizatie" and ex_id and not obj:
        extragere = get_object_or_404(m.ExtragereAI, pk=ex_id, tip="autorizatie", document__locatie=loc)
        tip_ales = request.GET.get("tip")
        initial.update(ai.initial_autorizatie(extragere.rezultat))
        campuri_ai = set(initial)
        if tip_ales:  # tipul ales din lista standard are prioritate față de cel detectat
            initial["tip"] = tip_ales
    form = Form(request.POST or None, request.FILES or None, instance=obj, initial=initial)
    if extragere:
        form.fields.pop("scan", None)
    document_temp = None
    citit = _ai_in_formular(request, Form, {"instance": obj}, {"locatie": loc})
    if citit:
        form, campuri_ai, document_temp = citit
    elif request.POST.get("document_temp"):
        form.fields.pop("scan", None)
        document_temp = m.Document.objects.filter(pk=request.POST["document_temp"]).first()
    inapoi = f"{loc.get_absolute_url()}?tab={tab}"
    if request.method == "POST" and not citit:
        if request.POST.get("sterge") and obj:
            if obj.documente.exists():
                messages.error(request, "Înregistrarea are documente atașate; arhivați-le întâi.")
                return redirect(inapoi)
            obj.delete()
            messages.success(request, f"{eticheta} ștearsă (rămâne în jurnalul de audit).")
            return redirect(inapoi)
        if form.is_valid():
            o = form.save(commit=False)
            o.locatie = loc
            o.save()
            if extragere:
                d = extragere.document
                d.autorizatie, d.titlu = o, str(o)[:255]
                d.save()
                extragere.status, extragere.validat_de, extragere.validat_la = "validat", request.user, timezone.now()
                extragere.save()
            fisier = form.cleaned_data.get("scan")
            if fisier:
                camp = {"evaluare": "raport_evaluare", "autorizatie": "autorizatie", "control": "control", "cf": "carte_funciara",
                        "folosinta": "contract_folosinta"}[tip]
                categorie = {"evaluare": "evaluare", "autorizatie": "autorizatie_fct", "control": "control", "cf": "cf",
                             "folosinta": "folosinta"}[tip]
                anterior = m.Document.objects.filter(arhivat=False, **{camp: o}).first()
                m.Document.objects.create(locatie=loc, categorie=categorie, titlu=str(o)[:255], fisier=fisier,
                                          incarcat_de=request.user, inlocuieste=anterior, **{camp: o})
            _leaga_document_temp(request, o, {"evaluare": "raport_evaluare", "autorizatie": "autorizatie", "control": "control",
                                               "cf": "carte_funciara", "folosinta": "contract_folosinta"}[tip])
            if tip == "cf" and completeaza_din_cf(loc):
                messages.info(request, "Suprafețele activului au fost completate automat din extrasele CF.")
            if tip == "folosinta":
                _salveaza_anexe_drept(request, loc, o)
            messages.success(request, f"{eticheta} salvată.")
            return redirect(inapoi)
    sablon = "platforma/contract_form.html" if extragere else ("platforma/drept_form.html" if tip == "folosinta" else "platforma/form.html")
    return render(request, sablon, {
        "form": form, "titlu": f"{eticheta} – {loc.cod or loc.denumire}", "inapoi": inapoi, "multipart": True, "poate_sterge": bool(obj),
        "anexe_existente": obj.anexe if tip == "folosinta" and obj else None, "document_temp": document_temp,
        "extragere": extragere, "campuri_ai": campuri_ai, "conditii_ai": [],
    })


def _salveaza_anexe_drept(request, loc, drept):
    n = 0
    for f_ in request.FILES.getlist("anexe"):
        m.Document.objects.create(locatie=loc, categorie="anexa_drept", contract_folosinta=drept, fisier=f_, incarcat_de=request.user,
                                  titlu=f_.name.rsplit(".", 1)[0].replace("_", " ")[:255])
        n += 1
    if n:
        messages.success(request, f"{n} anex{'e' if n != 1 else 'ă'} adăugat{'e' if n != 1 else 'ă'} la {drept}.")


@login_required
@require_POST
def drept_anexe(request, pk, drept_pk):
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    drept = get_object_or_404(m.ContractFolosinta, pk=drept_pk, locatie=loc)
    _salveaza_anexe_drept(request, loc, drept)
    return redirect(f"{loc.get_absolute_url()}?tab=prezentare#folosinta")


@login_required
@require_POST
def locatie_incarcare(request, pk, zona):
    """Încărcare multiplă: anexe (cadastru, plan topografic, alte anexe) sau fotografii."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul încarcă documente pe activ.")
    loc = get_object_or_404(m.Locatie, pk=pk)
    fisiere = request.FILES.getlist("fisiere")
    if zona == "poze":
        categorie, tab = "foto", "poze"
        respinse = [f_.name for f_ in fisiere if not f_.name.lower().endswith(EXTENSII_POZE)]
        fisiere = [f_ for f_ in fisiere if f_.name.lower().endswith(EXTENSII_POZE)]
        if respinse:
            messages.warning(request, f"Nu sunt imagini și nu au fost încărcate: {', '.join(respinse)}")
    elif zona == "anexe":
        categorie, tab = request.POST.get("categorie", "anexa"), "anexe"
        if categorie not in CATEGORII_ANEXE:
            raise PermissionDenied
    else:
        raise PermissionDenied
    titlu = request.POST.get("titlu", "").strip()
    for f_ in fisiere:
        nume = f_.name.rsplit(".", 1)[0].replace("_", " ")
        m.Document.objects.create(locatie=loc, categorie=categorie, fisier=f_, incarcat_de=request.user,
                                  titlu=(titlu if titlu and len(fisiere) == 1 else nume)[:255])
    if fisiere:
        messages.success(request, f"{len(fisiere)} fișier{'e' if len(fisiere) != 1 else ''} încărcat{'e' if len(fisiere) != 1 else ''}.")
    return redirect(f"{loc.get_absolute_url()}?tab={tab}")


def _verificare_cf(loc):
    """Compară suprafețele din registru (dashboard) cu suma suprafețelor din extrasele CF.
    Contează doar cărțile funciare cu extras încărcat (documentul PDF atașat); cele fără extras nu intră în totaluri."""
    carti = [c for c in loc.carti.prefetch_related("documente") if any(not d.arhivat for d in c.documente.all())]
    def compara(registru, valori):
        n = len(valori)
        cf = sum(valori, Decimal(0)) if n else None
        if not n:
            stare = "lipsa_cf"
        elif not registru:
            stare = "lipsa_registru"
        else:
            stare = "ok" if abs(cf - registru) <= max(Decimal(1), registru * Decimal("0.01")) else "diferenta"
        return {"registru": registru, "cf": cf, "n": n, "diferenta": (cf - registru) if (n and registru) else None, "stare": stare}
    return {
        "teren": compara(loc.suprafata_teren, [c.suprafata_mp for c in carti if c.suprafata_mp]),
        "constructii": compara(loc.suprafata_constructii or loc.suprafata_construita_totala,
                               [c.suprafata_constructii_mp for c in carti if c.suprafata_constructii_mp]),
        "nr_cf": len(carti),
    }


def _numar_cf(v):
    numar = str(v or "").strip().upper()
    return re.sub(r"^(CF|C\.F\.)\s*(NR\.?)?\s*", "", numar).strip()


def _ataseaza_extras_cf(loc, d, numar, valori):
    """Înregistrează extrasul citit: cartea funciară a activului (creată sau completată – valorile existente nu se
    suprascriu) + documentul atașat ca versiune nouă. Întoarce (creată?, câmpuri completate)."""
    cf, creat = m.CarteFunciara.objects.get_or_create(locatie=loc, numar=numar)
    completate = []
    for camp, v in valori.items():
        if camp == "numar" or v in (None, ""):
            continue
        if getattr(cf, camp, None) in (None, ""):
            setattr(cf, camp, v)
            completate.append(camp)
    nota = "Date completate automat din extras (AI) – de verificat."
    if completate and nota not in (cf.observatii or ""):
        cf.observatii = ((cf.observatii or "") + "\n" + nota).strip()
    cf.save()
    anterior = m.Document.objects.filter(carte_funciara=cf, arhivat=False).exclude(pk=d.pk).first()
    d.locatie, d.carte_funciara, d.titlu = loc, cf, str(cf)[:255]
    if anterior:
        d.versiune = anterior.versiune + 1
        if not hasattr(anterior, "inlocuit_de"):
            d.inlocuieste = anterior
    d.save()
    if anterior:
        m.Document.objects.filter(pk=anterior.pk).update(arhivat=True)
    return creat, completate


def _json_valoare(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


@login_required
@require_POST
def cf_citire_definire(request):
    """Faza de definire a activului: extrasele CF se citesc cu AI înainte de salvare; datele se întorc formularului
    (care se completează automat), iar documentele se atașează activului la salvare."""
    if not e_admin(request.user):
        return JsonResponse({"eroare": "Doar administratorul definește activele."}, status=403)
    form_gol = f.CarteFunciaraForm()
    rezultate = []
    for fisier in request.FILES.getlist("extrase")[:20]:
        d = m.Document.objects.create(categorie="cf", titlu=f"Extras CF – în așteptarea salvării activului ({fisier.name})"[:255],
                                      fisier=fisier, incarcat_de=request.user)
        valori, mesaj = ai.extrage_campuri(d, form_gol, "CarteFunciara")
        valori = {k: _json_valoare(v) for k, v in valori.items()}
        valori["numar"] = _numar_cf(valori.get("numar"))
        m.ExtragereAI.objects.create(document=d, tip="cf", rezultat=valori, mesaj=mesaj, status="propus" if valori["numar"] else "eroare",
                                     model_ai=getattr(settings, "OLLAMA_MODEL", ""))
        rezultate.append({"document": d.pk, "fisier": fisier.name, "valori": valori, "mesaj": mesaj,
                          "url": reverse("document", args=[d.pk])})
    return JsonResponse({"rezultate": rezultate})


def _ataseaza_cf_din_definire(request, loc):
    """La salvarea activului: extrasele CF citite în formular devin cărțile funciare ale activului."""
    ids = [int(x) for x in request.POST.getlist("cf_document") if x.isdigit()]
    if not ids:
        return []
    rez = []
    for d in m.Document.objects.filter(pk__in=ids, categorie="cf", locatie__isnull=True, proiect__isnull=True, incarcat_de=request.user):
        ex = d.extrageri.filter(tip="cf").first()
        valori = dict(ex.rezultat) if ex else {}
        numar = _numar_cf(request.POST.get(f"cf_numar_{d.pk}") or valori.get("numar"))
        if not numar:
            d.locatie, d.titlu = loc, f"Extras CF nerecunoscut – {d.titlu}"[:255]
            d.save()
            rez.append(f"{d.titlu} (fără nr. CF – atașați-l manual)")
            continue
        valori = {k: v for k, v in valori.items() if k in f.CarteFunciaraForm.base_fields}
        valori = ai.normalizeaza_pentru_formular(valori, f.CarteFunciaraForm())
        creat, _ = _ataseaza_extras_cf(loc, d, numar, valori)
        if ex:
            ex.status, ex.validat_de, ex.validat_la = "validat", request.user, timezone.now()
            ex.save(update_fields=["status", "validat_de", "validat_la"])
        rez.append(f"CF {numar}")
    return rez


@login_required
@require_POST
def cf_extrase_multiple(request, pk):
    """Încarcă mai multe extrase CF deodată: fiecare e citit cu AI și completează (sau creează) cartea funciară corespunzătoare."""
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    fisiere = request.FILES.getlist("extrase")
    rezultate, nerecunoscute = [], []
    form_gol = f.CarteFunciaraForm()
    for fisier in fisiere:
        d = m.Document.objects.create(locatie=loc, categorie="cf", titlu="Extras CF în curs de înregistrare", fisier=fisier, incarcat_de=request.user)
        valori, mesaj = ai.extrage_campuri(d, form_gol, "CarteFunciara")
        numar = _numar_cf(valori.get("numar"))
        if not numar:
            d.titlu = f"Extras CF nerecunoscut – {fisier.name}"[:255]
            d.save()
            nerecunoscute.append(f"{fisier.name}{' (' + mesaj + ')' if mesaj else ''}")
            continue
        creat, completate = _ataseaza_extras_cf(loc, d, numar, valori)
        rezultate.append(f"CF {numar} ({'nouă' if creat else 'actualizată'}, {len(completate)} câmpuri)")
    if rezultate and completeaza_din_cf(loc):
        messages.info(request, "Suprafețele activului au fost completate automat din extrasele CF.")
    if rezultate:
        messages.success(request, f"{len(rezultate)} extrase citite și atașate: " + "; ".join(rezultate) + ". Verificați datele marcate „de verificat”.")
    if nerecunoscute:
        messages.warning(request, "Nu am putut identifica numărul CF în: " + "; ".join(nerecunoscute) +
                         ". Documentele sunt salvate; atașați-le manual din lista de cărți funciare.")
    return redirect(f"{loc.get_absolute_url()}?tab=prezentare#carti-funciare")


@login_required
@require_POST
def preia_din_cf(request, pk):
    """Preia în registrul activului valorile din extrasele CF și le propagă în toate zonele activului
    (dashboard, „Date activ”, registrul activelor, export, formularul de editare, totalurile din tabloul de bord)."""
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    v = _verificare_cf(loc)
    camp = request.POST.get("camp", "ambele")
    schimbari = []

    def seteaza(atribut, valoare, eticheta):
        vechi = getattr(loc, atribut)
        if valoare is not None and vechi != valoare:
            setattr(loc, atribut, valoare)
            schimbari.append(f"{eticheta}: {vechi if vechi is not None else '—'} → {valoare} mp")

    if camp in ("teren", "ambele") and v["teren"]["cf"]:
        seteaza("suprafata_teren", v["teren"]["cf"], "teren")
    if camp in ("constructii", "ambele") and v["constructii"]["cf"]:
        constr_veche = loc.suprafata_constructii
        seteaza("suprafata_constructii", v["constructii"]["cf"], "construcții")
        # suprafața construită totală urmează valoarea din CF dacă era goală sau egală cu vechea valoare a construcțiilor
        if loc.suprafata_construita_totala in (None, constr_veche):
            seteaza("suprafata_construita_totala", v["constructii"]["cf"], "construită totală")
    # lista de cărți funciare din registru: toate CF-urile înregistrate + cele deja menționate în text
    numere = [c.numar for c in loc.carti.all()]
    for n in _cf_din_text(loc.carti_funciare):
        if n not in numere:
            numere.append(n)
    text_cf = "CF " + ", ".join(numere) if numere else loc.carti_funciare
    if text_cf != loc.carti_funciare:
        loc.carti_funciare = text_cf
        schimbari.append(f"cărți funciare: {text_cf}")
    # nr. cadastrale din extrase, dacă registrul nu le are
    if schimbari:
        loc.save()
        messages.success(request, "Registrul activului a fost actualizat din extrasele CF – " + "; ".join(schimbari) +
                         ". Valorile noi apar în dashboard, „Date activ”, registrul activelor, export și în totalurile din tabloul de bord.")
    else:
        messages.info(request, "Registrul era deja actualizat cu valorile din extrasele CF.")
    return redirect(f"{loc.get_absolute_url()}?tab=prezentare#carti-funciare")


@login_required
@require_POST
def poze_sterge(request, pk):
    """Șterge DEFINITIV fotografiile bifate (doar administratorul): înregistrarea și fișierul de pe server.
    Operația rămâne în jurnalul de audit (cine, când, ce fișier)."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul poate șterge fotografii.")
    loc = get_object_or_404(m.Locatie, pk=pk)
    ids = [int(x) for x in request.POST.getlist("poze") if x.isdigit()]
    n = 0
    for d in m.Document.objects.filter(locatie=loc, categorie="foto", pk__in=ids):
        fisier = d.fisier.name
        m.Document.objects.filter(pk=d.pk).delete()  # ocolește blocajul general de ștergere al documentelor
        if fisier:
            d.fisier.storage.delete(fisier)
        n += 1
    if n:
        messages.success(request, f"{n} fotografi{'i' if n != 1 else 'e'} ștears{'e' if n != 1 else 'ă'} definitiv.")
    return redirect(f"{loc.get_absolute_url()}?tab=poze")


def _arhiveaza_si_sterge_documente(docs, loc, eticheta):
    """Șterge documentele din aplicație; fișierele NU se pierd: se mută în date/arhiva_stergeri/<cod activ>_<eticheta>_<data-ora>/
    (de unde pot fi recuperate). Ștergerea rămâne în jurnalul de audit. Întoarce numărul de documente șterse."""
    import shutil
    docs = list(docs)
    if not docs:
        return 0
    dest = Path(settings.MEDIA_ROOT).parent / "arhiva_stergeri" / f"{(loc.cod or f'activ{loc.pk}').replace('.', '_')}_{eticheta}_{timezone.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    ids = [d.pk for d in docs]
    m.Document.objects.filter(inlocuieste_id__in=ids).update(inlocuieste=None)  # lanțul de versiuni nu blochează ștergerea
    for d in docs:
        if d.fisier and d.fisier.storage.exists(d.fisier.name):
            sursa = d.fisier.path
            shutil.move(sursa, dest / f"{d.pk}_{d.fisier.name.rsplit('/', 1)[-1]}")
        m.Document.objects.filter(pk=d.pk).delete()  # ocolește blocajul general de ștergere al documentelor
    return len(docs)


def _scoate_cf_din_registru(loc, numar):
    """Elimină numărul CF din câmpul text „cărți funciare” al registrului (ex. „CF 76379, 76381” → „CF 76381”)."""
    text = loc.carti_funciare or ""
    sep = r"\s*(?:,|;|\bsi\b|\bși\b)\s*"
    nr = rf"(?<![\d-]){re.escape(numar)}(?![\d-])"
    nou = re.sub(sep + nr, "", text, count=1, flags=re.I)          # „…, 222222” – cu separatorul dinainte
    if nou == text:
        nou = re.sub(nr + f"(?:{sep})?", "", text, count=1, flags=re.I)  # „222222, …” – cu separatorul de după
    if nou == text:
        return False
    nou = re.sub(r"\s{2,}", " ", nou).strip(" ,;")
    if not re.search(r"\d", nou):
        nou = ""
    loc.carti_funciare = nou
    loc.save(update_fields=["carti_funciare", "modificat_la"])
    return True


@login_required
@require_POST
def cf_sterge(request, pk):
    """Ștergerea unui extras CF încărcat greșit (doar administratorul):
    - ce=extras: șterge extrasul curent al cărții funciare; versiunea anterioară (dacă există) redevine cea curentă;
    - ce=cf: șterge cartea funciară cu toate extrasele ei și numărul ei din registru;
    - ce=neatasat: șterge un extras care nu a putut fi citit (nu e legat de nicio carte funciară);
    - ce=registru: elimină din registru un număr CF identificat greșit (fără carte funciară înregistrată).
    Fișierele se mută în date/arhiva_stergeri/, iar operația rămâne în jurnalul de audit."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul poate șterge extrase CF.")
    loc = get_object_or_404(m.Locatie, pk=pk)
    ce = request.POST.get("ce")
    inapoi = redirect(f"{loc.get_absolute_url()}?tab=prezentare#carti-funciare")
    with transaction.atomic():
        if ce == "registru":
            numar = (request.POST.get("numar") or "").strip().upper()
            if not numar or numar not in _cf_din_text(loc.carti_funciare) or loc.carti.filter(numar=numar).exists():
                messages.error(request, f"CF {numar} nu poate fi eliminat din registru (nu există sau are deja carte funciară înregistrată).")
            elif _scoate_cf_din_registru(loc, numar):
                messages.success(request, f"CF {numar} a fost eliminat din registrul activului.")
            return inapoi
        if ce == "neatasat":
            d = get_object_or_404(m.Document, pk=request.POST.get("document"), locatie=loc, categorie="cf", carte_funciara__isnull=True)
            titlu = d.titlu
            _arhiveaza_si_sterge_documente([d], loc, "extras_cf")
            messages.success(request, f"Extrasul „{titlu}” a fost șters.")
            return inapoi
        cf = get_object_or_404(m.CarteFunciara, pk=request.POST.get("cf"), locatie=loc)
        if ce == "extras":
            curent = cf.documente.filter(arhivat=False).first()
            if not curent:
                messages.warning(request, f"{cf} nu are extras atașat.")
                return inapoi
            anterior = curent.inlocuieste
            _arhiveaza_si_sterge_documente([curent], loc, f"extras_cf_{cf.numar}")
            if anterior:
                m.Document.objects.filter(pk=anterior.pk).update(arhivat=False)
                messages.success(request, f"Extrasul {cf} a fost șters; versiunea anterioară (v{anterior.versiune}) a redevenit cea curentă.")
            else:
                messages.success(request, f"Extrasul {cf} a fost șters. Datele cărții funciare au rămas – verificați-le sau atașați extrasul corect.")
            return inapoi
        if ce == "cf":
            numar, eticheta = cf.numar, str(cf)
            _arhiveaza_si_sterge_documente(cf.documente.all(), loc, f"cf_{numar}")
            cf.delete()
            _scoate_cf_din_registru(loc, numar)
            messages.success(request, f"{eticheta} a fost ștearsă din fișa activului, împreună cu extrasele ei. "
                                      "Dacă suprafețele activului fuseseră preluate din ea, verificați-le în tabelul de mai jos.")
            return inapoi
    raise PermissionDenied


def _cf_din_text(text):
    """Numerele de carte funciară din textul importat (ex. „CF 76379, 76381 si 60847”), fără nr. cadastrale."""
    text = re.sub(r"\b(?:nc|nr\.?\s*cad(?:astral)?)\.?\s*\d+", " ", text or "", flags=re.I)
    text = re.sub(r"steren\s*=\s*[\d\s+.,]+|\d+\s*mp\b|\bc\d+\s*=\s*\d+", " ", text, flags=re.I)  # suprafețe din observații
    numere = []
    for n in re.findall(r"\b(\d{3,7}(?:-C\d+(?:-U\d+)?)?)\b", text, flags=re.I):
        n = n.upper()
        if n not in numere:
            numere.append(n)
    return numere


def _randuri_cf(loc):
    """Lista CF: cele înregistrate + cele identificate în registru dar încă neînregistrate (de adăugat)."""
    carti = list(loc.carti.prefetch_related("documente"))
    existente = {c.numar for c in carti}
    randuri = [{"cf": c, "numar": c.numar, "extras": c.extras} for c in carti]
    randuri += [{"cf": None, "numar": n, "extras": None} for n in _cf_din_text(loc.carti_funciare) if n not in existente]
    return randuri


def completeaza_din_cf(loc):
    """Completare automată: suprafețele goale din registru se preiau din extrasele CF (valorile existente nu se suprascriu)."""
    v = _verificare_cf(loc)
    campuri = []
    if not loc.suprafata_teren and v["teren"]["cf"]:
        loc.suprafata_teren = v["teren"]["cf"]
        campuri.append("suprafata_teren")
    if not loc.suprafata_constructii and not loc.suprafata_construita_totala and v["constructii"]["cf"]:
        loc.suprafata_constructii = loc.suprafata_construita_totala = v["constructii"]["cf"]
        campuri += ["suprafata_constructii", "suprafata_construita_totala"]
    numere = [c.numar for c in loc.carti.all()] + [n for n in _cf_din_text(loc.carti_funciare)]
    numere = list(dict.fromkeys(numere))
    if numere and _cf_din_text(loc.carti_funciare) != numere:
        loc.carti_funciare = "CF " + ", ".join(numere)
        campuri.append("carti_funciare")
    if campuri:
        loc.save(update_fields=campuri + ["modificat_la"])
    return campuri


@login_required
@require_POST
def cf_din_registru(request, pk):
    """Creează fișele CF din textul „Carte funciară” importat din Excel (ex. „CF 76379, 76381”)."""
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    numere = _cf_din_text(loc.carti_funciare)
    noi = 0
    for n in numere:
        _, creat = m.CarteFunciara.objects.get_or_create(locatie=loc, numar=n)
        noi += creat
    messages.success(request, f"{noi} cărți funciare create din registru ({len(numere)} găsite în text). Completați UAT-ul și atașați extrasele.")
    return redirect(f"{loc.get_absolute_url()}?tab=prezentare#carti-funciare")


@login_required
def locatie_din_scan(request, pk, tip):
    """Pasul 1 pentru autorizații: încărcarea scanului; AI-ul precompletează formularul (pasul 2)."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul actualizează documentele activului.")
    loc = get_object_or_404(m.Locatie, pk=pk)
    if tip != "autorizatie":
        raise PermissionDenied
    if request.method == "POST" and request.FILES.get("fisier"):
        d = m.Document.objects.create(locatie=loc, categorie="autorizatie_fct", titlu="Autorizație în curs de înregistrare",
                                      fisier=request.FILES["fisier"], incarcat_de=request.user)
        ex = ai.ruleaza_extragere(d, "autorizatie")
        if ex.status == "eroare":
            messages.warning(request, f"Autorizația nu a putut fi citită automat: {ex.mesaj} Completați datele manual.")
            ex.status, ex.rezultat = "propus", {"autorizatie": {}}
            ex.save()
        elif ex.mesaj:
            messages.warning(request, ex.mesaj)
        else:
            messages.success(request, "Autorizația a fost citită. Verificați datele propuse înainte de salvare.")
        return redirect(f"/active/{loc.pk}/autorizatie/nou/?extragere={ex.pk}" + (f"&tip={request.GET['tip']}" if request.GET.get("tip") else ""))
    return render(request, "platforma/contract_scan.html", {
        "titlu_pagina": f"Încarcă autorizație de funcționare – {loc.cod or loc.denumire}",
        "inapoi": f"{loc.get_absolute_url()}?tab=autorizatii", "url_manual": f"/active/{loc.pk}/autorizatie/nou/",
        "ce_se_extrage": "tipul autorizației (ISU, mediu, DSVSA, DSP, ape, ISCIR, funcționare), emitentul, numărul și data, "
                         "valabilitatea (calculată și când e dată ca durată, ex. „10 ani”), dacă necesită viză anuală și condițiile impuse titularului.",
        "eticheta_fisier": "Autorizația (PDF sau imagine)", "buton": "Citește autorizația",
        "stare_ai": ai.stare_ai(), "ocr": ai.ocr_disponibil(),
    })


@login_required
def locatie_form(request, pk=None):
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul modifică registrul activelor.")
    loc = get_object_or_404(m.Locatie, pk=pk) if pk else None
    stare_veche = loc.stare_activ if loc else ""
    form = f.LocatieForm(request.POST or None, instance=loc)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            o = form.save(commit=False)
            soc, prefix = form.cleaned_data["proprietar"], form.cleaned_data["prefix"]
            if not soc.prefix_cod:  # acronimul rămâne alocat firmei pentru toate locațiile ei
                soc.prefix_cod = prefix
                soc.save(update_fields=["prefix_cod"])
            if not o.cod:
                o.cod = urmatorul_cod_locatie(prefix)
            o.save()
            form.save_m2m()
            if o.stare_activ != stare_veche:
                m.IstoricStareActiv.objects.create(
                    locatie=o, stare_veche=stare_veche, stare_noua=o.stare_activ, user=request.user,
                    data=form.cleaned_data.get("data_schimbare_stare") or timezone.localdate(),
                    motiv=form.cleaned_data.get("motiv_schimbare_stare") or ("Activ înregistrat în registru." if not stare_veche else "Categorie schimbată din fișa activului (fără motiv precizat)."))
            cf_atasate = _ataseaza_cf_din_definire(request, o)
        messages.success(request, f"Locația {o.cod} a fost salvată.")
        if cf_atasate:
            messages.info(request, f"Extrase CF atașate: {', '.join(cf_atasate)}. Datele citite de AI sunt marcate „de verificat” în lista de cărți funciare.")
        return redirect(o)
    return render(request, "platforma/locatie_form.html", {
        "form": form, "titlu": "Editare locație" if loc else "Locație nouă", "inapoi": loc.get_absolute_url() if loc else "/active/",
        "form_societate": f.SocietateNouaForm(),
    })


@login_required
def cod_pentru_societate(request):
    """Pentru firma aleasă: acronimul ei (sau unul nou propus, unic) și următorul cod de locație liber."""
    from .coduri import propune_acronim
    soc = get_object_or_404(m.Societate, pk=request.GET.get("societate"))
    if soc.prefix_cod:
        prefix, nou = soc.prefix_cod.upper(), False
    else:
        prefix, nou = propune_acronim(soc.denumire, soc.pk), True
    return JsonResponse({"prefix": prefix, "nou": nou, "cod": urmatorul_cod_locatie(prefix),
                         "locatii": soc.locatii.exclude(cod__isnull=True).count()})


@login_required
def acronim_propus(request):
    from .coduri import propune_acronim, valideaza_prefix
    if request.GET.get("verifica"):
        e = valideaza_prefix(request.GET["verifica"])
        return JsonResponse({"ok": not e, "eroare": e or ""})
    return JsonResponse({"prefix": propune_acronim(request.GET.get("denumire", ""))})


@login_required
@require_POST
def proprietar_extern_nou(request):
    """Definirea unui proprietar extern nou (baza închirierilor externe) din formularul de locație – doar administratorul."""
    if not e_admin(request.user):
        return JsonResponse({"eroare": "Doar administratorul definește proprietari externi."}, status=403)
    form = f.ProprietarExternForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"erori": {k: " ".join(v) for k, v in form.errors.items()}}, status=400)
    pe = form.save()
    return JsonResponse({"pk": pe.pk, "denumire": pe.denumire})


@login_required
@require_POST
def societate_noua(request):
    """Definirea unei firme noi din formularul de locație (fereastră), cu acronim unic."""
    if not e_admin(request.user):
        return JsonResponse({"eroare": "Doar administratorul definește firme noi."}, status=403)
    form = f.SocietateNouaForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"erori": {k: " ".join(v) for k, v in form.errors.items()}}, status=400)
    soc = form.save()
    return JsonResponse({"pk": soc.pk, "denumire": soc.denumire, "prefix": soc.prefix_cod, "cod": urmatorul_cod_locatie(soc.prefix_cod)})


@login_required
@require_POST
def locatie_document(request, pk):
    if not e_admin(request.user):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    form = f.DocumentLocatieForm(request.POST, request.FILES)
    if form.is_valid():
        d = form.save(commit=False)
        d.locatie, d.incarcat_de = loc, request.user
        d.save()
        messages.success(request, "Document încărcat.")
    else:
        messages.error(request, f"Documentul nu a fost încărcat: {form.errors.as_text()}")
    return redirect(loc)


# ------------------------------------------------------------------ Raport Zero
@login_required
def rapoarte_zero(request):
    qs = m.Proiect.objects.select_related("locatie", "beneficiar").prefetch_related("revizii_rz").order_by("-nr_inregistrare")
    q = request.GET.get("q")
    if q:
        qs = qs.filter(Q(cod__icontains=q) | Q(denumire__icontains=q) | Q(beneficiar__denumire__icontains=q) | Q(nr_inregistrare__icontains=q))
    if request.GET.get("export"):
        return _export_xlsx("registru_rapoarte_zero", [
            ("Nr. înregistrare", "nr_inregistrare"), ("Data înregistrării", "data_inregistrare_rz"), ("Data avizării", "aprobat_la"), ("Cod proiect", "cod"), ("Denumire", "denumire"), ("Locație", "locatie"),
            ("Beneficiar", "beneficiar"), ("Valoare RZ", "valoare_rz"), ("Monedă", "moneda_rz"), ("Finanțare", "finantare"),
            ("Prioritate", "get_prioritate_display"), ("Demarare", "data_demarare"), ("Finalizare", "data_finalizare"),
            ("Stare RZ", "get_stare_rz_display"), ("Revizie", "revizie_rz"), ("Întocmit", "intocmit_de"), ("Avizat", "avizat_de"),
        ], qs)
    vizibile = set(proiecte_vizibile(request.user).values_list("pk", flat=True))
    # cronologic: cele mai noi înregistrări primele
    proiecte = sorted(qs, key=lambda p: (p.data_inregistrare_rz or datetime.date.min, p.nr_inregistrare or ""), reverse=True)
    return render(request, "platforma/rapoarte_zero.html", {"proiecte": proiecte, "vizibile": vizibile, "e_admin": e_admin(request.user)})


@login_required
@require_POST
def raport_zero_sterge(request, pk):
    """Ștergerea directă a unui Raport Zero (deci a proiectului) din registru – doar administratorul, fără cod SMS.
    Confirmare: se scrie codul proiectului. Totul se arhivează înainte (date + documente) și rămâne în jurnalul de audit."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul poate șterge Rapoarte Zero.")
    p = get_object_or_404(m.Proiect, pk=pk)
    if request.POST.get("confirmare_cod", "").strip().upper() != p.cod.upper():
        messages.error(request, f"Raportul Zero nu a fost șters: pentru confirmare trebuie scris exact codul proiectului ({p.cod}).")
        return redirect("rapoarte_zero")
    cod_p, den_p, pk_p, nr = p.cod, p.denumire, p.pk, p.nr_inregistrare
    arhiva = stergere.arhiveaza(p)
    stergere.sterge(p)
    m.JurnalAudit.objects.create(user=request.user, actiune="stergere", model="raport zero / proiect (din registru, admin)", obiect_id=str(pk_p),
                                 obiect=f"{cod_p} · {den_p} (nr. {nr})", proiect_id=pk_p,
                                 modificari={"arhiva": str(arhiva.relative_to(arhiva.parents[2]))})
    messages.success(request, f"Raportul Zero {cod_p} (nr. {nr}) a fost șters împreună cu proiectul. Arhiva completă (date și documente) a rămas pe server: {arhiva.name}.")
    return redirect("rapoarte_zero")


@login_required
def raport_zero(request, pk):
    p = get_object_or_404(m.Proiect.objects.select_related("locatie", "beneficiar"), pk=pk)
    capitole_buget, total_buget = _buget_rz(p)
    ctx_ech = _echipamente_rz_ctx(p)
    curs = p.curs_rz or p.curs
    if p.moneda_rz == "EUR" and curs:  # Raport Zero în EUR: toate sumele se afișează în EUR, la cursul RZ
        k = Decimal(str(curs))
        eur = lambda v: (Decimal(str(v)) / k).quantize(Decimal("0.01"))  # noqa: E731
        for c in capitole_buget:
            c["total"] = eur(c["total"])
            for l in c["linii"]:
                l["valoare"] = eur(l["valoare"])
        total_buget = eur(total_buget)
        for e in ctx_ech["echipamente"]:
            e.valoare_afisata = eur(e.valoare_estimata)
        ctx_ech["total_echipamente_rz"] = eur(ctx_ech["total_echipamente_rz"])
    else:
        for e in ctx_ech["echipamente"]:
            e.valoare_afisata = e.valoare_estimata
    return render(request, "platforma/raport_zero.html", {
        "p": p, "revizii": p.revizii_rz.all(), "poate_edita": poate_edita(request.user, p),
        "poate_aviza": poate_aviza_rz(request.user) and (not p.revizie_rz or e_admin(request.user)),
        "vizibil": poate_vedea(request.user, p), "capitole_buget": capitole_buget, "total_buget": total_buget,
        "curs_rz": curs, **ctx_ech,
        "oferte": list(p.oferte_rz.prefetch_related("documente")),
        "pdf_semnat": p.documente.filter(categorie="raport_zero", arhivat=False).first(),
        "emailuri_aviz": p.emailuri_aviz.all()[:5],
    })


def _numar_ro(text):
    """„7.500.000”, „350000,50”, „1 250 000” -> Decimal; ridică ValueError pentru valori invalide sau negative."""
    t = (text or "").strip().replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", t):  # „7.500.000” sau „400.000” = separator de mii
        t = t.replace(".", "")
    try:
        v = Decimal(t or "0")
    except Exception:  # noqa: BLE001
        raise ValueError(text)
    if v < 0:
        raise ValueError(text)
    return v


def _buget_rz(p, date_post=None):
    """Structura de buget HG pentru formularul / afișarea Raportului Zero (valori curente pe subcapitole)."""
    existente = {l.capitol: l for l in p.linii_buget.prefetch_related("rectificari")} if p and p.pk else {}
    coduri = [c for c, _ in SUBCAPITOLE] + [c for c in existente if c not in dict(SUBCAPITOLE)]
    den = dict(SUBCAPITOLE)
    capitole, total = [], Decimal(0)
    for cod_cap, den_cap in CAPITOLE.items():
        linii = []
        for c in coduri:
            if capitol_din(c) != cod_cap:
                continue
            l = existente.get(c)
            val = l.valoare_curenta if l else Decimal(0)
            if date_post is not None and f"buget_{c}" in date_post:
                val = date_post[f"buget_{c}"]
            linii.append({"cod": c, "denumire": l.denumire if l else den.get(c, ""), "valoare": val})
        sub = sum((Decimal(str(x["valoare"])) if not isinstance(x["valoare"], str) else Decimal(0) for x in linii), Decimal(0))
        total += sub
        capitole.append({"cod": cod_cap, "denumire": den_cap, "linii": linii, "total": sub})
    return capitole, total


def _salveaza_buget_rz(p, post):
    """Scrie valorile bugetate din Raportul Zero în liniile de buget; rectificările vechi rămân în istoric."""
    erori = []
    creeaza_linii(p)
    for l in p.linii_buget.prefetch_related("rectificari"):
        cheie = f"buget_{l.capitol}"
        if cheie not in post:
            continue
        try:
            v = _numar_ro(post[cheie])
        except ValueError:
            erori.append(f"{l.capitol}: „{post[cheie]}”")
            continue
        nou = v - suma(l.rectificari.all(), "diferenta")  # valoarea curentă devine exact cea introdusă
        if nou != l.valoare_aprobata:
            l.valoare_aprobata = nou
            l.save(update_fields=["valoare_aprobata"])
    return erori


def _seteaza_responsabil(p, user):
    """„Responsabil proiect” înlocuiește perechea coordonator / PM: un PM rămâne PM, cu coordonatorul lui din profil;
    un coordonator / administrator devine coordonatorul proiectului."""
    if not user:
        return
    profil = getattr(user, "profil", None)
    if profil and profil.rol in ("pm", "implementare"):  # responsabil de proiect cu acces doar la proiectele lui
        p.pm, p.coordonator = user, profil.coordonator or p.coordonator
    else:
        p.pm, p.coordonator = None, user


def _oferte_din_post(post):
    randuri = []
    for k in post.getlist("of_key"):
        furnizor = post.get(f"of_furnizor_{k}", "").strip()
        if not furnizor:
            continue
        val = post.get(f"of_valoare_{k}", "").strip()
        try:
            valoare = _numar_ro(val) if val else None
        except ValueError:
            valoare = None
        randuri.append({"key": k, "id": int(post.get(f"of_id_{k}")) if post.get(f"of_id_{k}", "").isdigit() else None,
                        "furnizor": furnizor[:200], "obiect": post.get(f"of_obiect_{k}", "").strip()[:300], "valoare": valoare,
                        "valoare_text": val, "moneda": "EUR" if post.get(f"of_moneda_{k}") == "EUR" else "RON",
                        "data_oferta": _data_sau_none(post.get(f"of_data_{k}")), "data_text": post.get(f"of_data_{k}", "")})
    return randuri


def _salveaza_oferte_rz(request, p):
    """Ofertele existente din Raportul Zero: actualizare, adăugare, eliminare; PDF-ul ofertei devine document al proiectului."""
    randuri = _oferte_din_post(request.POST)
    pastrate = []
    for r in randuri:
        o = p.oferte_rz.filter(pk=r["id"]).first() if r["id"] else None
        o = o or m.OfertaRZ(proiect=p)
        for c in ("furnizor", "obiect", "valoare", "moneda", "data_oferta"):
            setattr(o, c, r[c])
        o.save()
        pastrate.append(o.pk)
        fisier = request.FILES.get(f"of_fisier_{r['key']}")
        if fisier:
            anterior = o.documente.filter(arhivat=False).first()
            m.Document.objects.create(proiect=p, oferta=o, categorie="oferta", titlu=f"Ofertă {o.furnizor}"[:255], fisier=fisier,
                                      incarcat_de=request.user,
                                      inlocuieste=anterior if anterior and not hasattr(anterior, "inlocuit_de") else None)
    for o in p.oferte_rz.exclude(pk__in=pastrate):
        m.Document.objects.filter(oferta=o).update(oferta=None)  # documentul rămâne în dosarul proiectului
        o.delete()


def _oferte_rz_ctx(request, p):
    if request.method == "POST":
        return _oferte_din_post(request.POST)
    if not p:
        return []
    return [{"key": f"e{o.pk}", "id": o.pk, "furnizor": o.furnizor, "obiect": o.obiect, "valoare_text": f"{o.valoare:.2f}".replace(".", ",") if o.valoare is not None else "",
             "moneda": o.moneda, "data_text": o.data_oferta.isoformat() if o.data_oferta else "", "document": o.document} for o in p.oferte_rz.all()]


def _echipamente_din_post(post):
    """Lista de echipamente trimisă din formularul RZ: (rânduri, erori). Rândurile goale se ignoră."""
    campuri = ("ech_id", "ech_denumire", "ech_specificatii", "ech_cantitate", "ech_um", "ech_linie", "ech_valoare")
    coloane = [post.getlist(c) for c in campuri]
    randuri, erori = [], []
    for k, (eid, den, spec, cant, um, linie, val) in enumerate(zip(*coloane), 1):
        den, spec = den.strip(), spec.strip()
        if not den and not val.strip():
            continue
        r = {"id": int(eid) if eid.isdigit() else None, "denumire": den, "specificatii": spec, "um": (um.strip() or "buc")[:10],
             "linie": linie, "cantitate_text": cant, "valoare_text": val}
        try:
            r["cantitate"] = _numar_ro(cant or "1") or Decimal(1)
            r["valoare"] = _numar_ro(val)
        except ValueError:
            erori.append(f"echipamentul {k} („{den or '?'}”): cantitate sau valoare invalidă")
            continue
        if not den:
            erori.append(f"echipamentul {k}: lipsește denumirea")
        elif linie not in LINII_ECHIPAMENTE:
            erori.append(f"„{den}”: alegeți linia de buget (4.3, 4.4 sau 4.5)")
        elif not r["valoare"]:
            erori.append(f"„{den}”: lipsește valoarea estimată")
        else:
            randuri.append(r)
    return randuri, erori


def _salveaza_echipamente_rz(p, randuri):
    """Sincronizează lista de echipamente a proiectului cu cea din RZ. Echipamentele deja contractate nu se șterg."""
    linii = {l.capitol: l for l in p.linii_buget.all()}
    existente = {e.pk: e for e in p.echipamente.all()}
    pastrate, avert = set(), []
    for r in randuri:
        e = existente.get(r["id"]) or m.Echipament(proiect=p)
        e.denumire, e.specificatii, e.um = r["denumire"][:255], r["specificatii"][:255], r["um"]
        e.cantitate, e.valoare_estimata, e.linie_buget = r["cantitate"], r["valoare"], linii.get(r["linie"])
        e.save()
        pastrate.add(e.pk)
    for pk, e in existente.items():
        if pk in pastrate:
            continue
        if e.contract_id:
            avert.append(f"„{e.denumire}” nu a fost scos din listă: este deja contractat ({e.contract.numar}).")
        else:
            e.delete()
    return avert


def _lei(v):
    """1234567.5 -> „1.234.568” (format românesc, fără zecimale)."""
    return f"{Decimal(v or 0):,.0f}".replace(",", ".")


def _verifica_echipamente_rz(p):
    """Liniile 4.3 / 4.4 / 4.5 cu bani trebuie justificate de lista de echipamente (aceeași sumă)."""
    probleme = []
    linii = {l.capitol: l for l in p.linii_buget.prefetch_related("rectificari", "echipamente")}
    for cod in LINII_ECHIPAMENTE:
        l = linii.get(cod)
        if not l:
            continue
        buget, lista = l.valoare_curenta, sum((e.valoare_estimata for e in l.echipamente.all()), Decimal(0))
        if buget and not lista:
            probleme.append(f"linia {cod} are {_lei(buget)} RON, dar nu are lista de echipamente")
        elif abs(buget - lista) > 1:
            probleme.append(f"linia {cod}: buget {_lei(buget)} RON ≠ lista de echipamente {_lei(lista)} RON")
    return probleme


def _echipamente_rz_ctx(p):
    ech = list(p.echipamente.select_related("linie_buget").order_by("linie_buget__capitol", "pk"))
    return {"echipamente": ech, "total_echipamente_rz": sum((e.valoare_estimata for e in ech), Decimal(0)),
            "probleme_echipamente": _verifica_echipamente_rz(p)}


def _valoare_rz_din_buget(p):
    """Valoarea ofertă / buget din RZ = totalul bugetului HG (RON), convertit în moneda RZ la cursul RZ."""
    tot = sum((l.valoare_curenta for l in p.linii_buget.prefetch_related("rectificari")), Decimal(0))
    if not tot:
        return
    if p.moneda_rz == "EUR":
        curs = p.curs_rz or p.curs
        tot = (Decimal(tot) / Decimal(curs)).quantize(Decimal("0.01")) if curs else p.valoare_rz
    if tot != p.valoare_rz:
        p.valoare_rz = tot
        p.save(update_fields=["valoare_rz"])


@login_required
def raport_zero_form(request, pk=None):
    p = get_object_or_404(m.Proiect, pk=pk) if pk else None
    if p:
        if not poate_edita(request.user, p):
            raise PermissionDenied
        if p.stare_rz in ("trimis", "aprobat"):
            messages.warning(request, "Raportul Zero avizat nu se modifică direct. Deschideți o revizie nouă.")
            return redirect("raport_zero", pk=p.pk)
    elif not poate_crea_proiect(request.user):
        raise PermissionDenied
    initial = {}
    if not p:
        initial = {"moneda_rz": "RON"}
        if request.GET.get("locatie"):
            loc = m.Locatie.objects.filter(pk=request.GET["locatie"]).first()
            if loc:
                initial.update(locatie=loc, beneficiar=loc.proprietar, linie=loc.linie_din_registru, divizie=loc.divizie if loc.linie_din_registru else "")
        if rol(request.user) in ("pm", "coordonator", "admin"):
            initial["responsabil"] = request.user
    post = request.POST
    # Cursul Raportului Zero: dacă RZ e în EUR, toate sumele din formular (buget, echipamente, finanțare) se introduc în EUR
    # și se stochează în RON la acest curs (contractele, facturile și plățile rămân în RON).
    curs_form = Decimal(str((p.curs_rz if p and p.curs_rz else None) or m.CursValutar.la_data()))
    ech_randuri, ech_erori = _echipamente_din_post(post) if request.method == "POST" else ([], [])
    if request.method == "POST":
        post = post.copy()
        if post.get("moneda_rz") == "EUR":
            for r in ech_randuri:
                r["valoare"] = (r["valoare"] * curs_form).quantize(Decimal("0.01"))
            for k in [k for k in post if k.startswith("buget_")]:
                try:
                    post[k] = str((_numar_ro(post[k]) * curs_form).quantize(Decimal("0.01"))) if post[k].strip() else ""
                except ValueError:
                    pass  # rămâne textul invalid, raportat la salvare
        # liniile 4.3 / 4.4 / 4.5 care au listă de echipamente = suma valorilor estimate din listă
        for cod in LINII_ECHIPAMENTE:  # se completează DOAR din lista de echipamente, niciodată manual
            post[f"buget_{cod}"] = str(sum((r["valoare"] for r in ech_randuri if r["linie"] == cod), Decimal(0)))
        if post.get("tip_investitie") == "echipamente":  # achiziție echipamente: fără buget pe structura HG
            for cod, _ in SUBCAPITOLE:
                if cod not in LINII_ECHIPAMENTE:
                    post[f"buget_{cod}"] = "0"
    form = f.RaportZeroForm(post if request.method == "POST" else None, instance=p, initial=initial)
    if request.method == "POST" and ech_erori:
        form.add_error(None, "Lista de echipamente: " + "; ".join(ech_erori))
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            _seteaza_responsabil(obj, form.cleaned_data["responsabil"])
            if not obj.pk:
                obj.intocmit_de = (request.user.get_full_name() or request.user.username)[:120]
                loc = m.Locatie.objects.select_for_update().get(pk=obj.locatie_id)
                obj.cod, obj.nr_ordine = loc.urmatorul_cod_proiect()
                obj.nr_inregistrare = nr_inregistrare_nou(loc.prefix)
            if obj.moneda_rz == "EUR" and not obj.curs_rz:
                obj.curs_rz = curs_form
            if obj.valoare_rz is None:
                obj.valoare_rz = Decimal(0)  # se calculează imediat din bugetul HG
            obj.save()
            erori = _salveaza_buget_rz(obj, post)
            avert_ech = _salveaza_echipamente_rz(obj, ech_randuri)
            _salveaza_oferte_rz(request, obj)
            _valoare_rz_din_buget(obj)
            obj.refresh_from_db()
            avert_fin = obj.actualizeaza_structura_finantare()
        if avert_fin:
            messages.warning(request, avert_fin)
        for a in avert_ech:
            messages.warning(request, a)
        for problema in _verifica_echipamente_rz(obj):
            messages.warning(request, f"Lista de echipamente: {problema}. Completați-o înainte de trimiterea spre avizare.")
        if erori:
            messages.error(request, "Valori de buget invalide (nesalvate): " + "; ".join(erori))
        messages.success(request, f"Raport Zero {obj.cod} salvat (nr. înregistrare {obj.nr_inregistrare}).")
        return redirect("raport_zero", pk=obj.pk)
    capitole_buget, total_buget = _buget_rz(p)
    curs_rz = curs_form
    in_eur = (form["moneda_rz"].value() or "RON") == "EUR"
    impartitor = curs_form if in_eur else Decimal(1)  # afișare în moneda Raportului Zero
    if in_eur:
        for c in capitole_buget:
            for l in c["linii"]:
                if not isinstance(l["valoare"], str):
                    l["valoare"] = (Decimal(str(l["valoare"])) / impartitor).quantize(Decimal("0.01"))
    if request.method == "POST":
        echipamente_rz = ech_randuri
    else:
        echipamente_rz = [{"id": e.pk, "denumire": e.denumire, "specificatii": e.specificatii, "um": e.um,
                           "linie": e.linie_buget.capitol if e.linie_buget_id else "", "cantitate_text": f"{e.cantitate:g}",
                           "valoare_text": f"{e.valoare_estimata / impartitor:.2f}".replace(".", ","), "contract": e.contract}
                          for e in (p.echipamente.select_related("linie_buget", "contract").order_by("linie_buget__capitol", "pk") if p else [])]
    return render(request, "platforma/raport_zero_form.html", {
        "capitole_buget": capitole_buget, "total_buget": total_buget, "curs_rz": curs_rz,
        "echipamente_rz": echipamente_rz, "linii_echipamente": [(c, dict(SUBCAPITOLE)[c]) for c in LINII_ECHIPAMENTE],
        "form": form, "titlu": f"Raport Zero {p.cod}" if p else "Raport Zero nou",
        "campuri_in_bloc": f.RaportZeroForm.CAMPURI_IN_BLOC, "multipart": True,
        "linii_ech_coduri": list(LINII_ECHIPAMENTE),
        "oferte_rz": _oferte_rz_ctx(request, p),
        "subtitlu": "Codul proiectului și numărul de înregistrare se generează automat la salvare." if not p else "",
        "inapoi": f"/rapoarte-zero/{p.pk}/" if p else "/rapoarte-zero/",
    })


@login_required
@require_POST
def raport_zero_actiune(request, pk, actiune):
    p = get_object_or_404(m.Proiect, pk=pk)
    if actiune == "trimite" and poate_edita(request.user, p) and p.stare_rz in ("ciorna", "respins"):
        probleme = _verifica_echipamente_rz(p)
        if probleme:
            messages.error(request, "Raportul Zero nu poate fi trimis: " + "; ".join(probleme)
                           + ". Banii de pe liniile 4.3, 4.4 și 4.5 trebuie justificați de lista de echipamente.")
            return redirect("raport_zero", pk=pk)
        p.stare_rz = "trimis"
        p.save()
        ok, mesaj = aviz_email.trimite(p, request.user)
        messages.success(request, "Raport Zero trimis spre avizare.")
        (messages.info if ok else messages.warning)(request, mesaj)
        return redirect("raport_zero", pk=pk)
    if actiune == "email" and p.stare_rz == "trimis" and (poate_edita(request.user, p) or poate_aviza_rz(request.user)):
        ok, mesaj = aviz_email.trimite(p, request.user)
        (messages.success if ok else messages.warning)(request, mesaj)
        return redirect("raport_zero", pk=pk)
    if actiune in ("aproba", "respinge") and poate_aviza_rz(request.user) and p.stare_rz == "trimis":
        if p.revizie_rz and not e_admin(request.user):
            raise PermissionDenied("Reviziile Raportului Zero (inclusiv modificările de buget) se aprobă de administrator.")
        _avizeaza_rz(p, request.user, actiune, request.POST.get("motiv", ""))
    elif actiune == "revizie" and poate_edita(request.user, p) and p.stare_rz == "aprobat":
        motiv = request.POST.get("motiv", "").strip()
        if not motiv:
            messages.error(request, "Motivul reviziei este obligatoriu.")
            return redirect("raport_zero", pk=pk)
        p.stare_rz, p.revizie_rz = "ciorna", p.revizie_rz + 1
        p.buget_blocat = False  # bugetul se poate modifica din nou, doar în Raportul Zero, până la noua aprobare
        p.observatii = (p.observatii + f"\nRevizia {p.revizie_rz} deschisă {timezone.localdate():%d.%m.%Y}: {motiv}").strip()
        p.save()
    else:
        raise PermissionDenied("Acțiune nepermisă în starea curentă.")
    messages.success(request, f"Raport Zero: {p.get_stare_rz_display()}.")
    return redirect("raport_zero", pk=pk)


def _avizeaza_rz(p, user, actiune, motiv="", sursa=""):
    """Aprobă / respinge un Raport Zero trimis (din aplicație sau din email)."""
    if actiune == "aproba":
        p.stare_rz, p.aprobat_de_user, p.aprobat_la = "aprobat", user, timezone.now()
        if not p.status:  # la inițiere și avizare, investiția intră în execuție
            p.status = stinv.IN_EXECUTIE
        p.buget_blocat, p.buget_blocat_de, p.buget_blocat_la = True, user, timezone.now()
        if not p.avizat_de:
            p.avizat_de = user.get_full_name() or user.username
        if not motiv and p.revizie_rz:  # motivul dat la deschiderea reviziei
            gasit = re.findall(rf"Revizia {p.revizie_rz} deschisă [^:]*: (.*)", p.observatii or "")
            motiv = gasit[-1] if gasit else ""
        m.RevizieRaportZero.objects.update_or_create(proiect=p, revizie=p.revizie_rz, defaults={
            "date": p.snapshot_rz(), "motiv": (motiv or ("Raport Zero inițial" if not p.revizie_rz else "")) + sursa,
            "aprobat_de": user, "aprobat_la": timezone.now()})
    else:
        p.stare_rz = "respins"
        if motiv or sursa:
            p.observatii = (p.observatii + f"\nRespins {timezone.localdate():%d.%m.%Y} de {user.get_full_name() or user.username}"
                            f"{sursa}: {motiv}").strip()
    p.save()


def aviz_din_email(request, token):
    """Pagina deschisă din emailul de avizare: confirmarea se face prin butonul din pagină (POST), nu prin simpla deschidere."""
    e, eroare = aviz_email.citeste_token(token)
    p = e.proiect if e else None
    ctx = {"e": e, "p": p, "eroare": eroare, "actiune": request.GET.get("actiune", "aproba")}
    if p and not eroare:
        ctx["capitole_buget"], ctx["total_buget"] = _buget_rz(p)
        apr = e.aprobator
        if not apr or not apr.is_active:
            ctx["eroare"] = "Aprobatorul nu are cont activ în aplicație."
        elif p.revizie_rz and not e_admin(apr):
            ctx["eroare"] = "Reviziile Raportului Zero se aprobă de administrator; avizați din aplicație."
        elif request.method == "POST":
            actiune = request.POST.get("actiune")
            motiv = request.POST.get("motiv", "").strip()
            if actiune not in ("aproba", "respinge"):
                ctx["eroare"] = "Acțiune necunoscută."
            elif actiune == "respinge" and not motiv:
                ctx["actiune"], ctx["lipsa_motiv"] = "respinge", True
            else:
                from . import audit
                ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip() or None
                audit._local.user = apr  # jurnalul de audit înregistrează aprobatorul
                with transaction.atomic():
                    _avizeaza_rz(p, apr, actiune, motiv, sursa=f" (din email, {timezone.localtime():%d.%m.%Y %H:%M})")
                    e.raspuns, e.raspuns_la, e.raspuns_ip, e.motiv = actiune, timezone.now(), ip, motiv
                    e.save(update_fields=["raspuns", "raspuns_la", "raspuns_ip", "motiv"])
                ctx["gata"] = actiune
    return render(request, "platforma/aviz_email.html", ctx)


# ------------------------------------------------------------------ registrul investițiilor
@login_required
def investitii(request):
    qs = proiecte_vizibile(request.user).prefetch_related("linii_buget__rectificari", "facturi", "contracte__acte_aditionale")
    g = request.GET
    if g.get("q"):
        qs = qs.filter(Q(cod__icontains=g["q"]) | Q(denumire__icontains=g["q"]) | Q(beneficiar__denumire__icontains=g["q"]))
    for camp in ("faza", "status", "linie", "divizie"):
        if g.get(camp):
            qs = qs.filter(**{camp: g[camp]})
    if g.get("finantare"):  # proiectele cu finanțare mixtă apar la fiecare formă bifată
        fin = g["finantare"].lower()
        cod = "nerambursabil" if "nerambursabil" in fin else "bancara" if "banc" in fin else "proprii" if "propri" in fin else None
        qs = qs.filter(**{f"fin_{cod}": True}) if cod else qs.filter(finantare=g["finantare"])
    if g.get("pm"):
        qs = qs.filter(pm_id=g["pm"])
    if g.get("beneficiar"):
        qs = qs.filter(beneficiar_id=g["beneficiar"])
    # cele 3 stadii ale investiției (proiectele încă neavizate – fără status – intră la „active”)
    stadii = {"active": Q(status=stinv.IN_EXECUTIE) | Q(status=""), "anulate": Q(status=stinv.ANULAT), "finalizate": Q(status=stinv.FINALIZAT)}
    numar_stadii = {k: qs.filter(q).count() for k, q in stadii.items()}
    numar_stadii["toate"] = qs.count()
    stadiu = g.get("stadiu", "") if g.get("stadiu") in stadii else ""
    if stadiu:
        qs = qs.filter(stadii[stadiu])
    if g.get("export"):
        return _export_xlsx("registru_investitii", [
            ("Cod", "cod"), ("Denumire", "denumire"), ("Locație", "locatie"), ("Beneficiar", "beneficiar"), ("Linie", "linie"),
            ("Coordonator", "coordonator"), ("PM", "pm"), ("Finanțare", "finantare"), ("Fază", "faza"), ("Status", "status"),
            ("Buget RZ (RON)", "buget_rz_ron"), ("Buget actualizat (RON)", "buget_actualizat_ron"), ("Angajat (RON)", "angajat_ron"),
            ("Facturat (RON)", "facturat_ron"), ("Plătit (RON)", "platit_ron"), ("Execuție %", "procent_executie"),
            ("Progres fizic %", "progres_fizic"), ("Demarare", "data_demarare"), ("Finalizare", "data_finalizare"),
        ], qs)
    nom = {t: m.Nomenclator.optiuni(t) for t in ("faza_proiect", "status_proiect", "linie", "tip_finantare")}
    beneficiari = m.Societate.objects.filter(proiecte__in=proiecte_vizibile(request.user)).distinct().order_by("denumire")
    divizii = proiecte_vizibile(request.user).exclude(divizie="").values_list("divizie", flat=True).distinct().order_by("divizie")
    filtre_fara_stadiu = g.copy()
    filtre_fara_stadiu.pop("stadiu", None)
    return render(request, "platforma/investitii.html", {"proiecte": qs, "nom": nom, "query": g, "beneficiari": beneficiari, "divizii": divizii,
                                                          "stadiu": stadiu, "filtre_fara_stadiu": filtre_fara_stadiu.urlencode(),
                                                          "stadii_nav": [(c, et, ic, numar_stadii[c]) for c, et, ic in (
                                                              ("toate", "Toate proiectele", "bi-list-ul"), ("active", "Proiecte active", "bi-play-circle"),
                                                              ("anulate", "Anulate / suspendate", "bi-pause-circle"), ("finalizate", "Proiecte finalizate", "bi-check-circle"))]})


# ------------------------------------------------------------------ fișa proiectului
@login_required
def proiect_detaliu(request, pk):
    p = _proiect_vizibil(request, pk)
    u = request.user
    linii = list(p.linii_buget.select_related("proiect").prefetch_related("rectificari", "alocari_contracte__contract__acte_aditionale", "acte_aditionale", "alocari__factura"))
    # Corelația buget ↔ contracte / facturi: ce linie e depășită și cu cât (peste toleranța proiectului)
    depasiri = []
    for l in linii:
        cur, ang, fac = l.valoare_curenta, l.angajat, l.facturat
        limita = cur * (1 + p.toleranta_depasire / 100)
        l.v_curent, l.v_angajat, l.v_facturat = cur, ang, fac
        l.grad = round(float(max(ang, fac)) / float(cur) * 100) if cur else (100 if (ang or fac) else 0)
        l.peste_contract = max(ang - cur, Decimal(0)) if ang > limita else Decimal(0)
        l.peste_factura = max(fac - cur, Decimal(0)) if fac > limita else Decimal(0)
        l.este_depasita = bool(l.peste_contract or l.peste_factura)
        if l.este_depasita:
            depasiri.append(l)
    capitole = []
    for cod, den in CAPITOLE.items():
        sub = [l for l in linii if capitol_din(l.capitol) == cod]
        if sub:
            capitole.append({
                "cod": cod, "denumire": den, "linii": sub, "depasit": any(l.este_depasita for l in sub),
                "aprobat": sum((l.valoare_aprobata for l in sub), Decimal(0)),
                "curent": sum((l.valoare_curenta for l in sub), Decimal(0)),
                "angajat": sum((l.angajat for l in sub), Decimal(0)),
                "facturat": sum((l.facturat for l in sub), Decimal(0)),
                "platit": sum((l.platit for l in sub), Decimal(0)),
            })
    curs = p.curs
    facturi = list(p.facturi.select_related("contract", "furnizor_societate", "lot_export").prefetch_related("plati", "documente", "alocari__linie_buget"))
    plati = list(m.Plata.objects.filter(factura__proiect=p).select_related(
        "factura", "propusa_de", "aprobat_tehnic_de", "aprobat_financiar_de", "aprobat_suplimentar_de", "lot_export").order_by("-data"))
    for pl in plati:
        pl.actiuni = plati_mod.actiuni_posibile(u, pl)
    ani = _ani_proiect(p)
    plan = {x.an: x.valoare_estimata for x in p.plan_anual.all()}
    ctx = {
        "p": p, "editabil": poate_edita(u, p), "editabil_fin": poate_edita_financiar(u, p),
        "modifica_buget": poate_modifica_buget(u, p), "poate_bloca": poate_bloca_buget(u, p),
        "capitole": capitole, "curs": curs, "facturi": facturi, "plati": plati, "depasiri": depasiri,
        "depasire_totala": sum((max(l.peste_contract, l.peste_factura) for l in depasiri), Decimal(0)),
        "contracte": p.contracte.prefetch_related("acte_aditionale", "conditii", "facturi", "documente", "alocari__linie_buget"),
        "certificate": p.certificate_urbanism.prefetch_related("documente"),
        "avize": p.avize.select_related("responsabil", "certificat").prefetch_related("documente"),
        "finantari": p.finantari.prefetch_related("obligatii", "documente"),
        "rapoarte_progres": p.rapoarte_progres.select_related("finantare").prefetch_related("documente"),
        "procese_verbale": list(p.procese_verbale.all()),
        "cereri_tragere": list(p.cereri_tragere.prefetch_related("pozitii")),
        "avize_expira": sum(1 for a in p.avize.all() if a.stare_valabilitate in ("expirat", "expira") and a.status == "obtinut"),
        "obligatii": p.obligatii.select_related("finantare", "contract", "responsabil"),
        "activitati": p.activitati.all(),
        "documente": p.documente.filter(arhivat=False).select_related("incarcat_de"),
        "documente_arhivate": p.documente.filter(arhivat=True).count(),
        "extrageri": m.ExtragereAI.objects.filter(document__proiect=p).select_related("document")[:10],
        "accese": p.accese.select_related("user"),
        "jurnal": p.jurnal.select_related("user")[:100],
        "form_urmarire": f.UrmarireProiectForm(instance=p),
        "statusuri_investitie": [s for s, _ in m.Proiect.STATUS_INVESTITIE],
        "cereri_status": p.cereri_status.select_related("cerut_de", "decis_de")[:10],
        "poate_aproba_status": stinv.poate_aproba_status(u, p),
        "form_setari": f.SetariProiectForm(instance=p, blocat=p.buget_blocat and not e_admin(u)),
        "curs_info": _curs_info(u, p),
        "plan_ani": [(an, plan.get(an, Decimal(0)), _realizat_an(p, an)) for an in ani],
        "form_doc": f.DocumentForm(proiect=p),
        "form_gantt": f.ImportGanttForm(),
        "tab": {"financiar": "contracte"}.get(request.GET.get("tab"), request.GET.get("tab", "prezentare")), "azi": timezone.localdate(),
        "stare_ai": ai.stare_ai(),
        "avize_stat": {e: p.avize.filter(status=s).count() for s, e in m.Aviz.STATUS},
        "facturi_nealocate": sum(1 for x in facturi if x.nealocat_ron != 0),
        **_echipamente_context(p),
        "facturi_fara_contract": [x for x in facturi if not x.contract_id],
        "total_fara_contract": sum((x.valoare_ron for x in facturi if not x.contract_id), Decimal(0)),
        "total_facturat_contracte": sum((x.valoare_ron for x in facturi if x.contract_id), Decimal(0)),
    }
    ctx["buget_eur"] = p.in_eur(p.buget_actualizat_ron)
    return render(request, "platforma/proiect.html", ctx)


@login_required
def contract_echipamente(request, pk, cpk):
    """Bifarea echipamentelor din lista RZ care se achiziționează prin acest contract + prețul contractat.
    Valoarea echipamentelor bifate se descarcă în execuția bugetului (alocarea contractului pe liniile 4.3 / 4.4 / 4.5)."""
    p = _proiect_vizibil(request, pk)
    c = get_object_or_404(m.Contract, pk=cpk, proiect=p)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    ech = list(p.echipamente.select_related("linie_buget", "contract").order_by("linie_buget__capitol", "pk"))
    if request.method == "POST":
        bifate = set(request.POST.getlist("bifat"))
        erori, linii_atinse = [], set()
        with transaction.atomic():
            for e in ech:
                if e.contract_id and e.contract_id != c.pk:
                    continue  # aparține altui contract
                if str(e.pk) in bifate:
                    try:
                        pret = _numar_ro(request.POST.get(f"pret_{e.pk}", ""))
                    except ValueError:
                        erori.append(f"„{e.denumire}”: preț invalid")
                        continue
                    if not pret:
                        erori.append(f"„{e.denumire}”: completați prețul unitar din contract")
                        continue
                    e.contract, e.valoare_unitara, e.moneda = c, pret, c.moneda
                    e.curs = c.curs if c.moneda == "EUR" else None
                    if e.status == "planificat":
                        e.status = "contractat"
                    e.save()
                    linii_atinse.add(e.linie_buget_id)
                elif e.contract_id == c.pk:
                    e.contract, e.valoare_unitara, e.curs = None, None, None
                    if e.status == "contractat":
                        e.status = "planificat"
                    e.save()
                    linii_atinse.add(e.linie_buget_id)
            if request.POST.get("aloca") and not erori:
                for lid in filter(None, linii_atinse | set(c.alocari.filter(linie_buget__capitol__in=LINII_ECHIPAMENTE).values_list("linie_buget_id", flat=True))):
                    total = sum((x.valoare_ron for x in c.echipamente.filter(linie_buget_id=lid)), Decimal(0))
                    if total:
                        m.ContractAlocare.objects.update_or_create(contract=c, linie_buget_id=lid, defaults={"valoare_ron": total})
                    elif lid in linii_atinse:
                        m.ContractAlocare.objects.filter(contract=c, linie_buget_id=lid).delete()
            if erori:
                transaction.set_rollback(True)
        if erori:
            messages.error(request, "Nesalvat: " + "; ".join(erori))
            return redirect("contract_echipamente", pk=p.pk, cpk=c.pk)
        n = c.echipamente.count()
        messages.success(request, f"Contractul {c.numar}: {n} echipament{'e' if n != 1 else ''} bifat{'e' if n != 1 else ''}."
                         + (" Alocarea pe liniile de buget a fost actualizată." if request.POST.get("aloca") else ""))
        if c.nealocat_ron > 1:
            messages.warning(request, f"Din contract au rămas nealocați {_lei(c.nealocat_ron)} RON (ex. montaj, transport, servicii) – alocați-i pe linia potrivită.")
        return redirect(f"{p.get_absolute_url()}?tab=echipamente")
    for e in ech:
        e.alt = bool(e.contract_id and e.contract_id != c.pk)  # contractat prin alt contract
    return render(request, "platforma/contract_echipamente.html", {
        "p": p, "c": c, "echipamente": ech, "alocari": c.alocari.select_related("linie_buget"),
    })


def _echipamente_context(p):
    """Lista echipamentelor + legătura cu contractele de achiziție și cu bugetul."""
    ech = list(p.echipamente.select_related("contract", "linie_buget"))
    pe_contract, pe_linie = {}, {}
    for e in ech:
        if e.contract_id:
            r = pe_contract.setdefault(e.contract_id, {"contract": e.contract, "n": 0, "total": Decimal(0)})
            r["n"] += 1
            r["total"] += e.valoare_ron
        if e.linie_buget_id:
            r = pe_linie.setdefault(e.linie_buget_id, {"linie": e.linie_buget, "n": 0, "total": Decimal(0)})
            r["n"] += 1
            r["total"] += e.valoare_ron
    for r in pe_contract.values():
        r["valoare_contract"] = r["contract"].valoare_totala_ron
        r["diferenta"] = r["total"] - r["valoare_contract"]
    # Corelarea listă RZ ↔ contracte ↔ buget pe liniile 4.3 / 4.4 / 4.5
    corelare = []
    for l in p.linii_buget.filter(capitol__in=LINII_ECHIPAMENTE).prefetch_related("rectificari", "alocari_contracte__contract__acte_aditionale", "acte_aditionale"):
        lst = [e for e in ech if e.linie_buget_id == l.pk]
        if not lst and not l.valoare_curenta and not l.angajat:
            continue
        estimat = sum((e.valoare_estimata for e in lst), Decimal(0))
        contractate = [e for e in lst if e.contract_id]
        r = {"linie": l, "buget": l.valoare_curenta, "estimat": estimat, "n": len(lst), "n_contractate": len(contractate),
             "estimat_contractate": sum((e.valoare_estimata for e in contractate), Decimal(0)),
             "contractat": sum((e.valoare_ron for e in contractate), Decimal(0)),
             "rest_estimat": sum((e.valoare_estimata for e in lst if not e.contract_id), Decimal(0)), "angajat": l.angajat}
        r["diferenta"] = r["contractat"] - r["estimat_contractate"]
        r["prognoza"] = r["angajat"] + r["rest_estimat"]  # ce va costa linia dacă restul se contractează la estimare
        r["depasire"] = r["prognoza"] - r["buget"]
        r["fara_lista"] = bool(r["buget"]) and not lst
        corelare.append(r)
    return {
        "echipamente": ech, "total_echipamente": sum((e.valoare_ron for e in ech), Decimal(0)),
        "total_estimat_ech": sum((e.valoare_estimata for e in ech), Decimal(0)),
        "ech_fara_contract": sum(1 for e in ech if not e.contract_id),
        "ech_pe_contract": sorted(pe_contract.values(), key=lambda r: -r["total"]),
        "ech_corelare": corelare,
    }


def _ani_proiect(p):
    start = (p.data_demarare or timezone.localdate()).year
    sfarsit = (p.data_finalizare or p.data_demarare or timezone.localdate()).year
    ani = set(range(start, sfarsit + 1)) | set(p.plan_anual.values_list("an", flat=True))
    ani |= {d.year for d in p.facturi.values_list("data", flat=True)}
    return sorted(ani)


def _realizat_an(p, an):
    return {
        "facturat": suma(p.facturi.filter(data__year=an), "valoare_ron"),
        "platit": suma(m.Plata.objects.filter(factura__proiect=p, stare="platita", data__year=an), "suma_ron"),
    }


@login_required
@require_POST
def proiect_urmarire(request, pk):
    p = _proiect_editabil(request, pk)
    form = f.UrmarireProiectForm(request.POST, instance=p)
    if form.is_valid():
        form.save()
        messages.success(request, "Datele de urmărire au fost actualizate.")
    else:
        messages.error(request, form.errors.as_text())
    return redirect(p)


@login_required
@require_POST
def proiect_status(request, pk):
    """Cerere de schimbare a statusului investiției (se aplică doar după aprobarea administratorului)."""
    p = _proiect_editabil(request, pk)
    try:
        c, aplicat = stinv.solicita(p, request.POST.get("status_nou", ""), request.user, request.POST.get("motiv", ""))
    except ValueError as e:
        messages.error(request, str(e))
    else:
        if aplicat:
            messages.success(request, f"Statusul investiției a devenit „{c.status_nou}”.")
        else:
            apr = p.aprobator_status
            messages.success(request, f"Cererea de schimbare a statusului în „{c.status_nou}” a fost trimisă spre aprobare "
                                      f"{'administratorului ' + (apr.get_full_name() or apr.username) if apr else 'administratorului'}.")
    return redirect(p)


@login_required
@require_POST
def proiect_status_decizie(request, pk, cpk, decizie):
    c = get_object_or_404(m.CerereStatus, pk=cpk, proiect_id=pk)
    try:
        stinv.decide(c, request.user, decizie == "aproba", request.POST.get("motiv", ""))
    except PermissionError as e:
        raise PermissionDenied(str(e))
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"{c.proiect.cod}: schimbarea statusului în „{c.status_nou}” a fost {'aprobată' if decizie == 'aproba' else 'respinsă'}.")
    return redirect(request.POST.get("inapoi") or c.proiect.get_absolute_url())


@login_required
@require_POST
def proiect_setari(request, pk):
    p = _proiect_editabil(request, pk)
    form = f.SetariProiectForm(request.POST, instance=p, blocat=p.buget_blocat and not e_admin(request.user))
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect(f"{p.get_absolute_url()}?tab=setari")
    with transaction.atomic():
        form.save()
        if poate_modifica_buget(request.user, p):
            for an in _ani_proiect(p) + [int(x) for x in [request.POST.get("an_nou")] if x and x.isdigit()]:
                val = request.POST.get(f"plan_{an}") if f"plan_{an}" in request.POST else request.POST.get("plan_nou")
                if val in (None, ""):
                    continue
                val = val.replace(" ", "")
                if "," in val:
                    val = val.replace(".", "").replace(",", ".")
                elif val.count(".") > 1:
                    val = val.replace(".", "")
                try:
                    v = Decimal(val)
                except Exception:  # noqa: BLE001
                    messages.error(request, f"Valoare invalidă pentru {an}: {val}")
                    continue
                m.PlanAnual.objects.update_or_create(proiect=p, an=an, defaults={"valoare_estimata": v})
    messages.success(request, "Setările proiectului au fost salvate.")
    return redirect(f"{p.get_absolute_url()}?tab=setari")


@login_required
@require_POST
def buget_hg(request, pk):
    p = _proiect_editabil(request, pk)
    if not poate_modifica_buget(request.user, p):
        raise PermissionDenied
    creeaza_linii(p)
    messages.success(request, "Structura de buget HG 907/2016 a fost generată.")
    return redirect(f"{p.get_absolute_url()}?tab=buget")


@login_required
@require_POST
def buget_blocare(request, pk):
    p = _proiect_vizibil(request, pk)
    if request.POST.get("actiune") == "deblocheaza":
        if not e_admin(request.user):
            raise PermissionDenied("Doar administratorul deblochează bugetul.")
        p.buget_blocat = False
    else:
        if not poate_bloca_buget(request.user, p):
            raise PermissionDenied
        p.buget_blocat, p.buget_blocat_de, p.buget_blocat_la = True, request.user, timezone.now()
    p.save()
    messages.success(request, "Bugetul a fost blocat. Modificările se fac doar prin rectificări ale administratorului."
                     if p.buget_blocat else "Bugetul a fost deblocat.")
    return redirect(f"{p.get_absolute_url()}?tab=buget")


TAB_PENTRU = {
    "linie_buget": "buget", "rectificare": "buget", "contract": "contracte", "act_aditional": "contracte", "conditie": "contracte",
    "factura": "contracte", "plata": "contracte", "alocare": "contracte", "alocare_contract": "contracte", "echipament": "echipamente", "certificat": "avize", "aviz": "avize",
    "finantare": "finantare", "obligatie": "finantare", "raport_progres": "finantare", "activitate": "gantt", "acces": "acces", "document": "documente",
}
STERGERE_PERMISA = {"linie_buget", "conditie", "aviz", "obligatie", "raport_progres", "activitate", "acces", "certificat", "alocare", "alocare_contract"}
TIPURI_FINANCIARE = {"factura", "plata", "alocare"}


def _verifica_drept(user, p, tip, obj):
    if tip in TIPURI_FINANCIARE:
        ok = poate_edita_financiar(user, p)
    elif tip == "linie_buget":
        ok = poate_edita(user, p) and p.stare_rz in ("ciorna", "respins")
    elif tip == "rectificare":
        ok = e_admin(user)
    elif tip == "acces":
        ok = e_admin(user) or user.pk == p.coordonator_id
    else:
        ok = poate_edita(user, p)
    if not ok:
        raise PermissionDenied("Nu aveți drept pentru această operație." +
                               (" Bugetul este blocat: rectificările le face administratorul." if tip in ("linie_buget", "rectificare") else ""))
    if obj is not None and not e_admin(user):
        if isinstance(obj, m.Plata) and obj.stare != "propusa":
            raise PermissionDenied("Plata a intrat în circuitul de aprobare și nu mai poate fi modificată.")
        if isinstance(obj, m.Factura) and obj.lot_export_id:
            raise PermissionDenied("Factura a fost exportată în ERP și nu mai poate fi modificată.")
        if isinstance(obj, m.FacturaAlocare) and obj.factura.lot_export_id:
            raise PermissionDenied("Factura a fost exportată în ERP; alocarea nu se mai modifică.")


@login_required
def obiect_form(request, pk, tip, obj_pk=None):
    p = _proiect_vizibil(request, pk)
    if tip not in f.FORMULARE:
        raise PermissionDenied
    if tip == "echipament" and not obj_pk:
        messages.info(request, "Echipamentele se adaugă în lista din Raportul Zero (liniile 4.3, 4.4, 4.5).")
        return redirect(reverse("raport_zero_editare", args=[p.pk]) + "#echipamente-rz" if p.stare_rz in ("ciorna", "respins")
                        else reverse("raport_zero", args=[p.pk]))
    Form, eticheta = f.FORMULARE[tip]
    model = Form._meta.model
    obj = None
    if obj_pk:
        obj = get_object_or_404(model, pk=obj_pk)
        if _proiect_al(obj) != p.pk:
            raise PermissionDenied
    _verifica_drept(request.user, p, tip, obj)
    initial = {k: v for k, v in request.GET.items() if k in Form.base_fields}
    extragere, campuri_ai = None, set()
    ex_id = request.POST.get("extragere") or request.GET.get("extragere")
    if tip == "contract" and ex_id and not obj:
        extragere = get_object_or_404(m.ExtragereAI, pk=ex_id, tip="contract", document__proiect=p)
        propus = ai.initial_contract(extragere.rezultat)
        campuri_ai = set(propus)
        initial.update(propus)
    form = Form(request.POST or None, request.FILES or None, instance=obj, proiect=p, initial=initial)
    if extragere:
        form.fields.pop("scan", None)  # documentul a fost deja încărcat la pasul de citire
    document_temp = None
    citit = _ai_in_formular(request, Form, {"instance": obj, "proiect": p}, {"proiect": p}) if tip in ("contract", "act_aditional") else None
    if citit:
        form, campuri_ai, document_temp = citit
    elif request.POST.get("document_temp"):
        form.fields.pop("scan", None)
        document_temp = m.Document.objects.filter(pk=request.POST["document_temp"]).first()
    if request.method == "POST" and not citit:
        if request.POST.get("sterge") and obj and tip in STERGERE_PERMISA:
            obj.delete()
            messages.success(request, f"{eticheta} ștearsă (rămâne în jurnalul de audit).")
            return redirect(f"{p.get_absolute_url()}?tab={TAB_PENTRU[tip]}")
        if form.is_valid():
            o = form.save(commit=False)
            eroare = _valideaza(o)
            if eroare:
                form.add_error(None, eroare)
            else:
                if isinstance(o, m.Document):
                    o.proiect, o.incarcat_de = p, request.user
                if isinstance(o, m.RectificareBuget):
                    o.aprobat_de = request.user
                if isinstance(o, m.Plata) and not o.pk:
                    o.propusa_de = request.user
                o.save()
                form.save_m2m()
                _salveaza_scan(request, form, p, o)
                if isinstance(o, m.Factura) and "platita" in form.cleaned_data and not form.fields["platita"].disabled:
                    rez = _seteaza_platita(request, o, form.cleaned_data["platita"], form.cleaned_data.get("data_platii"))
                    if rez:
                        (messages.error if rez[0] == "eroare" else messages.info)(request, rez[1])
                if isinstance(o, m.Contract):
                    _leaga_document_temp(request, o, "contract")
                elif isinstance(o, m.ActAditional):
                    _leaga_document_temp(request, o, "act_aditional", {"contract": o.contract})
                if extragere:
                    _aplica_extragere_contract(request, extragere, o)
                messages.success(request, f"{eticheta} salvată.")
                for avert in _avertismente(o):
                    messages.warning(request, avert)
                dupa = f"{p.get_absolute_url()}?tab={TAB_PENTRU[tip]}"
                if isinstance(o, m.Contract) and o.nealocat_ron > 0:
                    messages.info(request, "Repartizați valoarea contractului pe liniile de buget.")
                    dupa = f"/proiecte/{p.pk}/alocare_contract/nou/?contract={o.pk}&valoare_ron={o.nealocat_ron}"
                elif isinstance(o, m.ContractAlocare) and o.contract.nealocat_ron > 0:
                    dupa = f"/proiecte/{p.pk}/alocare_contract/nou/?contract={o.contract_id}&valoare_ron={o.contract.nealocat_ron}"
                if isinstance(o, m.Factura) and o.nealocat_ron and not o.contract_id:
                    messages.info(request, "Repartizați factura pe liniile de buget.")
                    dupa = f"/proiecte/{p.pk}/alocare/nou/?factura={o.pk}&valoare_ron={o.nealocat_ron}"
                return redirect(dupa)
    return render(request, "platforma/contract_form.html" if tip == "contract" else "platforma/form.html", {
        "form": form, "titlu": f"{eticheta} – {p.cod}", "inapoi": f"{p.get_absolute_url()}?tab={TAB_PENTRU[tip]}",
        "multipart": tip == "document", "poate_sterge": obj and tip in STERGERE_PERMISA,
        "curs_info": _curs_info(request.user, p) if "curs" in form.fields else None,
        "extragere": extragere, "campuri_ai": campuri_ai, "tipuri_conditii": dict(m.ConditieContract.TIP), "document_temp": document_temp,
        "conditii_ai": list(enumerate((extragere.rezultat or {}).get("conditii", []) or [])) if extragere else [],
        "fragmente_ai": ((extragere.rezultat or {}).get("contract", {}) if extragere else {}),
    })


def _poate_seta_curs(user, p):
    return e_admin(user) if p.buget_blocat else poate_edita(user, p)


def _curs_info(user, p):
    azi = curs_mod.curs_azi()
    return {
        "proiect": p.curs_referinta, "proiect_data": p.curs_referinta_data, "proiect_sursa": p.curs_referinta_sursa,
        "azi": azi.eur if azi else None, "azi_data": azi.data if azi else None,
        "poate_seta": _poate_seta_curs(user, p), "pk": p.pk, "blocat": p.buget_blocat,
    }


@login_required
def curs_bnr(request):
    """Cursul BNR EUR la o dată (JSON), pentru fereastra de stabilire a cursului."""
    try:
        data = datetime.date.fromisoformat(request.GET.get("data", ""))
    except ValueError:
        data = timezone.localdate()
    c = curs_mod.curs_la(data)
    if not c:
        return JsonResponse({"eroare": "Nu există curs BNR pentru această dată."}, status=404)
    return JsonResponse({"data": c.data.isoformat(), "data_ro": f"{c.data:%d.%m.%Y}", "eur": str(c.eur)})


@login_required
@require_POST
def proiect_curs(request, pk):
    """Stabilește cursul de referință al proiectului: BNR la o dată aleasă sau valoare manuală."""
    p = _proiect_vizibil(request, pk)
    if not _poate_seta_curs(request.user, p):
        return JsonResponse({"eroare": "Bugetul este blocat: cursul proiectului îl poate schimba doar administratorul."}, status=403)
    mod = request.POST.get("mod")
    try:
        if mod == "bnr":
            data = datetime.date.fromisoformat(request.POST["data"])
            c = curs_mod.curs_la(data)
            if not c:
                return JsonResponse({"eroare": "Nu există curs BNR pentru data aleasă."}, status=400)
            p.curs_referinta, p.curs_referinta_data, p.curs_referinta_sursa = c.eur, c.data, f"BNR din {c.data:%d.%m.%Y}"
        else:
            v = Decimal(request.POST.get("valoare", "").replace(",", "."))
            if not (Decimal("1") < v < Decimal("20")):
                return JsonResponse({"eroare": "Valoare de curs neplauzibilă."}, status=400)
            motiv = request.POST.get("motiv", "").strip()
            p.curs_referinta, p.curs_referinta_data = v.quantize(Decimal("0.0001")), timezone.localdate()
            p.curs_referinta_sursa = ("Stabilit manual" + (f": {motiv}" if motiv else ""))[:80]
    except (KeyError, ValueError, ArithmeticError):
        return JsonResponse({"eroare": "Date invalide."}, status=400)
    p.save()
    return JsonResponse({"eur": str(p.curs_referinta), "data_ro": f"{p.curs_referinta_data:%d.%m.%Y}", "sursa": p.curs_referinta_sursa})


def _aplica_extragere_contract(request, ex, contract):
    d = ex.document
    d.contract = contract
    d.titlu = f"Contract {contract.numar}/{contract.data:%d.%m.%Y} – {contract.partener}"
    d.save()
    alese = {int(i) for i in request.POST.getlist("conditie") if i.isdigit()}
    acum = timezone.now()
    for i, c in enumerate((ex.rezultat or {}).get("conditii", []) or []):
        if i not in alese or not c.get("descriere"):
            continue
        tip = c.get("tip") if c.get("tip") in dict(m.ConditieContract.TIP) else "altele"
        m.ConditieContract.objects.create(
            contract=contract, tip=tip, descriere=c["descriere"], termen=ai._data(c.get("termen")),
            valoare=str(c.get("valoare", ""))[:120], fragment_sursa=c.get("fragment", ""), pagina=str(c.get("pagina", ""))[:20],
            extras_ai=True, validat_de=request.user, validat_la=acum,
        )
    ex.status, ex.validat_de, ex.validat_la = "validat", request.user, acum
    ex.save()


@login_required
def contract_din_scan(request, pk):
    """Pasul 1: încărcarea contractului scanat; AI-ul îl citește și precompletează formularul (pasul 2)."""
    p = _proiect_vizibil(request, pk)
    _verifica_drept(request.user, p, "contract", None)
    if request.method == "POST" and request.FILES.get("fisier"):
        d = m.Document.objects.create(proiect=p, categorie="contract", titlu="Contract în curs de înregistrare",
                                      fisier=request.FILES["fisier"], incarcat_de=request.user)
        ex = ai.ruleaza_extragere(d, "contract")
        if ex.status == "eroare":
            messages.warning(request, f"Contractul nu a putut fi citit automat: {ex.mesaj} Completați datele manual.")
            ex.status = "propus"
            ex.rezultat = {"contract": {}, "conditii": []}
            ex.save()
        elif ex.mesaj:
            messages.warning(request, ex.mesaj)
        gasite = ai.initial_contract(ex.rezultat)
        lipsa = [et for k, et in (("numar", "numărul"), ("partener", "partenerul"), ("valoare", "valoarea")) if k not in gasite]
        if ex.status != "eroare" and len(lipsa) >= 2:
            pagini = (d.text_extras or "").count("[pagina ")
            messages.warning(request, f"Nu am găsit în document {', '.join(lipsa)}. Documentul are {pagini} pagin{'ă' if pagini == 1 else 'i'}: "
                             "verificați dacă s-a scanat și prima pagină (cea cu părțile contractului și valoarea).")
        else:
            messages.success(request, "Contractul a fost citit. Verificați datele propuse înainte de salvare.")
        return redirect(f"/proiecte/{p.pk}/contract/nou/?extragere={ex.pk}")
    return render(request, "platforma/contract_scan.html", {"p": p, "stare_ai": ai.stare_ai(), "ocr": ai.ocr_disponibil()})


def _seteaza_platita(request, factura, platita, data=None, op=""):
    """Bifa „Plătită” pe factură: creează (sau anulează) plata marcată direct din factură. Întoarce mesajul de afișat."""
    marcate = factura.plati.filter(marcata_din_factura=True)
    if platita:
        if factura.achitata:
            return None
        if factura.in_aprobare_ron:
            return ("eroare", f"Factura {factura.numar} are plăți în circuitul de aprobare ({factura.in_aprobare_ron} RON). "
                              "Finalizați sau respingeți-le înainte de a o marca plătită.")
        rest = factura.valoare_ron - factura.platit_ron
        m.Plata.objects.create(factura=factura, data=data or timezone.localdate(), suma_ron=rest, stare="platita",
                               marcata_din_factura=True, propusa_de=request.user, document_plata=op[:80],
                               observatii="Marcată plătită direct din factură")
        mesaj = f"Factura {factura.numar} a fost marcată plătită ({rest} RON) și apare ca achitată în buget."
        if factura.nealocat_ron:
            mesaj += " Atenție: factura nu e repartizată integral pe linii de buget."
        return ("succes", mesaj)
    if marcate.exists():
        n = marcate.count()
        for pl in marcate:
            pl.delete()
        return ("succes", f"Factura {factura.numar} nu mai e marcată plătită ({n} plată anulată; rămâne în jurnalul de audit).")
    return None


@login_required
@require_POST
def factura_platita(request, pk, factura_pk):
    p = _proiect_vizibil(request, pk)
    if not poate_edita_financiar(request.user, p):
        raise PermissionDenied
    factura = get_object_or_404(m.Factura, pk=factura_pk, proiect=p)
    rez = _seteaza_platita(request, factura, request.POST.get("platita") == "1")
    if rez:
        (messages.error if rez[0] == "eroare" else messages.success)(request, rez[1])
    return redirect(f"{p.get_absolute_url()}?tab=financiar")


@login_required
@require_POST
def buget_salveaza(request, pk):
    """Păstrat pentru compatibilitate: bugetul se completează acum în Raportul Zero."""
    p = _proiect_vizibil(request, pk)
    messages.info(request, "Bugetul HG se completează și se modifică în Raportul Zero.")
    return redirect("raport_zero_editare", pk=p.pk) if p.stare_rz in ("ciorna", "respins") else redirect("raport_zero", pk=p.pk)
    if not poate_modifica_buget(request.user, p):
        raise PermissionDenied("Bugetul este blocat: modificările se fac prin rectificări ale administratorului.")
    schimbate, erori = 0, []
    with transaction.atomic():
        for l in p.linii_buget.all():
            cheie = f"aprobat_{l.pk}"
            if cheie not in request.POST:
                continue
            text = request.POST[cheie].strip().replace(" ", "")
            if "," in text:
                text = text.replace(".", "").replace(",", ".")
            elif text.count(".") > 1:
                text = text.replace(".", "")
            try:
                v = Decimal(text or "0")
                if v < 0:
                    raise ValueError
            except Exception:  # noqa: BLE001
                erori.append(f"{l.capitol}: „{request.POST[cheie]}”")
                continue
            if v != l.valoare_aprobata:
                l.valoare_aprobata = v
                l.save(update_fields=["valoare_aprobata"])
                schimbate += 1
    if erori:
        messages.error(request, "Valori invalide (nesalvate): " + "; ".join(erori))
    messages.success(request, f"Buget salvat: {schimbate} linii modificate.")
    return redirect(f"{p.get_absolute_url()}?tab=buget")


def _ai_in_formular(request, Form, form_kw, doc_kw):
    """Bifa „Completează automat cu AI”: încarcă documentul, îl citește și reafișează formularul precompletat.
    Valorile introduse deja de utilizator au prioritate. Întoarce None dacă nu e cazul."""
    fisier = request.FILES.get("scan")
    if not (request.method == "POST" and request.POST.get("citeste_ai") and fisier and not request.POST.get("document_temp")):
        return None
    model = Form._meta.model.__name__
    categorie = {"Contract": "contract", "ActAditional": "act_aditional", "CarteFunciara": "cf", "RaportEvaluare": "evaluare",
                 "AutorizatieFunctionare": "autorizatie_fct", "ControlAutoritate": "control", "ContractFolosinta": "folosinta"}.get(model, "altul")
    d = m.Document.objects.create(titlu="Document în curs de înregistrare", categorie=categorie, fisier=fisier,
                                  incarcat_de=request.user, **doc_kw)
    gol = Form(**form_kw)
    if model == "Contract":
        ex = ai.ruleaza_extragere(d, "contract")
        valori, mesaj = ai.initial_contract(ex.rezultat), ex.mesaj
    elif model == "AutorizatieFunctionare":
        ex = ai.ruleaza_extragere(d, "autorizatie")
        valori, mesaj = ai.initial_autorizatie(ex.rezultat), ex.mesaj
    else:
        valori, mesaj = ai.extrage_campuri(d, gol, model)
    introduse = {k: v for k, v in request.POST.items() if k not in ("csrfmiddlewaretoken", "citeste_ai") and v != ""}
    form = Form(initial={**valori, **introduse}, **form_kw)
    form.fields.pop("scan", None)
    form.fields.pop("citeste_ai", None)
    if mesaj:
        messages.warning(request, mesaj)
    elif valori:
        messages.success(request, f"AI-ul a completat {len(set(valori) - set(introduse))} câmpuri din document. Verificați-le înainte de salvare.")
    else:
        messages.warning(request, "AI-ul nu a găsit date de completat în document.")
    return form, set(valori) - set(introduse), d


def _leaga_document_temp(request, o, camp, legaturi=None):
    pk = request.POST.get("document_temp")
    if not pk:
        return
    d = m.Document.objects.filter(pk=pk, incarcat_de=request.user, titlu="Document în curs de înregistrare").first()
    if d:
        setattr(d, camp, o)
        for k, v in (legaturi or {}).items():
            setattr(d, k, v)
        d.titlu = str(o)[:255]
        d.save()


def _salveaza_scan(request, form, p, o):
    fisier = form.cleaned_data.get("scan") if hasattr(form, "cleaned_data") else None
    if not fisier:
        return
    if isinstance(o, m.Aviz):
        legaturi, categorie, cheie = {"aviz": o}, ("studiu" if o.tip == "studiu" else "aviz"), "aviz"
        titlu = f"{o.denumire}" + (f" nr. {o.numar_aviz}" if o.numar_aviz else "")
    elif isinstance(o, m.RaportProgres):
        legaturi, categorie, cheie = {"raport_progres": o}, "raport_progres", "raport_progres"
        titlu = str(o)
    elif isinstance(o, m.Contract):
        legaturi, categorie, cheie = {"contract": o}, "contract", "contract"
        titlu = f"Contract {o.numar}/{o.data:%d.%m.%Y} – {o.partener}"
    else:
        legaturi, categorie, cheie = {"act_aditional": o, "contract": o.contract}, "act_aditional", "act_aditional"
        titlu = f"Act adițional {o.numar} la {o.contract.numar}"
    anterior = m.Document.objects.filter(arhivat=False, **{cheie: o}).first()
    if anterior and hasattr(anterior, "inlocuit_de"):
        anterior = None
    m.Document.objects.create(proiect=p, categorie=categorie, titlu=titlu[:255], fisier=fisier, incarcat_de=request.user, inlocuieste=anterior, **legaturi)


def _avertismente(o):
    if isinstance(o, m.Factura) and o.contract_id:
        c = o.contract
        if c.facturat_ron > c.valoare_totala_ron:
            yield (f"Facturile pe contractul {c.numar} ({c.facturat_ron} RON) depășesc valoarea contractului "
                   f"({c.valoare_totala_ron} RON, cu acte adiționale). Verificați dacă e nevoie de un act adițional.")


def _valideaza(o):
    if isinstance(o, m.ContractAlocare):
        alte = suma(o.contract.alocari.exclude(pk=o.pk), "valoare_ron")
        if alte + o.valoare_ron > o.contract.valoare_ron:
            return f"Alocările depășesc valoarea contractului ({o.contract.valoare_ron} RON; deja alocat {alte} RON)."
    if isinstance(o, m.FacturaAlocare):
        alte = suma(o.factura.alocari.exclude(pk=o.pk), "valoare_ron")
        if alte + o.valoare_ron > o.factura.valoare_ron:
            return f"Alocările depășesc valoarea facturii ({o.factura.valoare_ron} RON; deja alocat {alte} RON)."
    if isinstance(o, m.Plata):
        f_ = o.factura
        alte = suma(f_.plati.exclude(pk=o.pk).exclude(stare="respinsa"), "suma_ron")
        if alte + o.suma_ron > f_.valoare_ron:
            return f"Plățile depășesc valoarea facturii fără TVA ({f_.valoare_ron} RON; deja plătit sau în aprobare {alte} RON)."
        if f_.nealocat_ron:
            return "Factura nu este repartizată integral pe linii de buget. Completați alocarea înainte de a propune plata."
    return None


def _proiect_al(obj):
    for cale in ("proiect_id", "contract.proiect_id", "factura.proiect_id", "linie.proiect_id"):
        cur = obj
        try:
            for x in cale.split("."):
                cur = getattr(cur, x)
            if cur:
                return cur
        except AttributeError:
            continue
    return None


# ------------------------------------------------------------------ plăți: aprobare și export ERP
@login_required
@require_POST
def plata_actiune(request, pk, actiune):
    pl = get_object_or_404(m.Plata.objects.select_related("factura__proiect"), pk=pk)
    plati_mod.aplica(request.user, pl, actiune, motiv=request.POST.get("motiv", ""), document_plata=request.POST.get("document_plata", ""))
    messages.success(request, f"Plata: {pl.get_stare_display()}.")
    return redirect(request.POST.get("inapoi") or request.META.get("HTTP_REFERER") or "/aprobari/")


@login_required
def aprobari(request):
    plati = plati_mod.plati_de_aprobat(request.user)
    for pl in plati:
        pl.actiuni = plati_mod.actiuni_posibile(request.user, pl)
    rz = m.Proiect.objects.filter(stare_rz="trimis") if poate_aviza_rz(request.user) else []
    return render(request, "platforma/aprobari.html", {"plati": plati, "rz": rz, "cereri_status": stinv.cereri_de_aprobat(request.user)})


@login_required
def export_erp(request):
    if not e_financiar(request.user):
        raise PermissionDenied("Exportul ERP este disponibil responsabililor financiari.")
    facturi = m.Factura.objects.filter(lot_export__isnull=True).select_related("proiect__beneficiar", "furnizor_societate").prefetch_related("alocari")
    if request.GET.get("societate"):
        facturi = facturi.filter(proiect__beneficiar_id=request.GET["societate"])
    plati = m.Plata.objects.filter(stare="aprobata").select_related("factura__proiect__beneficiar", "aprobat_tehnic_de", "aprobat_financiar_de")
    if request.method == "POST":
        try:
            if request.POST.get("tip") == "facturi":
                lot = plati_mod.export_facturi(request.user, facturi.filter(pk__in=request.POST.getlist("id")))
            else:
                lot = plati_mod.export_plati(request.user, plati.filter(pk__in=request.POST.getlist("id")))
            messages.success(request, f"Lotul {lot.numar} a fost generat ({lot.nr_inregistrari} înregistrări).")
        except ValueError as e:
            messages.error(request, str(e))
        return redirect("export_erp")
    loturi = m.LotExport.objects.select_related("creat_de").prefetch_related("plati")[:50]
    societati = m.Societate.objects.filter(proiecte__isnull=False).distinct()
    return render(request, "platforma/export_erp.html", {"facturi": facturi, "plati": plati, "loturi": loturi, "societati": societati})


@login_required
def lot_descarca(request, pk):
    if not e_financiar(request.user):
        raise PermissionDenied
    lot = get_object_or_404(m.LotExport, pk=pk)
    return FileResponse(lot.fisier.open("rb"), as_attachment=True, filename=f"{lot.numar}.xlsx")


@login_required
@require_POST
def lot_confirma(request, pk):
    lot = get_object_or_404(m.LotExport, pk=pk, tip="plati")
    n = 0
    for pl in lot.plati.filter(stare="exportata"):
        plati_mod.aplica(request.user, pl, "confirma", document_plata=request.POST.get("document_plata", ""))
        n += 1
    messages.success(request, f"{n} plăți confirmate ca plătite.")
    return redirect("export_erp")


# ------------------------------------------------------------------ dashboard anual (estimat vs realizat)
@login_required
def dashboard_anual(request):
    proiecte = proiecte_vizibile(request.user)
    g = request.GET
    if g.get("societate"):
        proiecte = proiecte.filter(beneficiar_id=g["societate"])
    if g.get("linie"):
        proiecte = proiecte.filter(linie=g["linie"])
    ids = list(proiecte.values_list("pk", flat=True))
    est = {r["an"]: r["s"] for r in m.PlanAnual.objects.filter(proiect_id__in=ids).values("an").annotate(s=Sum("valoare_estimata"))}
    fac = {r["an"]: r["s"] for r in m.Factura.objects.filter(proiect_id__in=ids).annotate(an=ExtractYear("data")).values("an").annotate(s=Sum("valoare_ron"))}
    pla = {r["an"]: r["s"] for r in m.Plata.objects.filter(factura__proiect_id__in=ids, stare="platita").annotate(an=ExtractYear("data")).values("an").annotate(s=Sum("suma_ron"))}
    ani = sorted(set(est) | set(fac) | set(pla)) or [timezone.localdate().year]
    serii = [{"an": a, "estimat": est.get(a) or 0, "facturat": fac.get(a) or 0, "platit": pla.get(a) or 0} for a in ani]
    an = int(g.get("an") or (timezone.localdate().year if timezone.localdate().year in ani else ani[-1]))
    randuri = []
    for p in proiecte.select_related("beneficiar"):
        e = suma(p.plan_anual.filter(an=an), "valoare_estimata")
        r = _realizat_an(p, an)
        if e or r["facturat"] or r["platit"]:
            randuri.append({"p": p, "estimat": e, **r, "abatere": r["facturat"] - e})
    pe_soc = {}
    for r in randuri:
        s = pe_soc.setdefault(r["p"].beneficiar, {"estimat": Decimal(0), "facturat": Decimal(0), "platit": Decimal(0)})
        for k in ("estimat", "facturat", "platit"):
            s[k] += r[k]
    total = {k: sum((r[k] for r in randuri), Decimal(0)) for k in ("estimat", "facturat", "platit")}
    return render(request, "platforma/dashboard_anual.html", {
        "serii": serii, "serii_json": [{k: (float(v) if k != "an" else v) for k, v in s.items()} for s in serii],
        "an": an, "ani": ani, "randuri": randuri, "pe_soc": sorted(pe_soc.items(), key=lambda x: -x[1]["estimat"]), "total": total,
        "societati": m.Societate.objects.filter(proiecte__isnull=False).distinct(),
        "linii": m.Nomenclator.optiuni("linie"), "query": g,
    })


# ------------------------------------------------------------------ documente
@login_required
def document_descarca(request, pk):
    d = get_object_or_404(m.Document, pk=pk)
    if not d.proiect_id and not d.locatie_id and not e_admin(request.user):
        raise PermissionDenied  # extras încă neatașat (activ în curs de definire)
    if d.proiect_id and not poate_vedea(request.user, d.proiect):
        if d.categorie != "raport_zero":  # Rapoartele Zero sunt vizibile tuturor
            raise PermissionDenied
    return FileResponse(d.fisier.open("rb"), as_attachment=request.GET.get("dl") == "1", filename=d.fisier.name.split("/")[-1])


@login_required
@require_POST
def document_arhiveaza(request, pk):
    d = get_object_or_404(m.Document, pk=pk)
    if d.proiect_id:
        _proiect_editabil(request, d.proiect_id)
    elif not e_admin(request.user):
        raise PermissionDenied
    d.arhivat = True
    d.save()
    messages.success(request, "Document arhivat (se păstrează în istoric).")
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ------------------------------------------------------------------ AI
@login_required
@require_POST
def ai_extrage(request, pk):
    d = get_object_or_404(m.Document, pk=pk, proiect__isnull=False)
    p = _proiect_editabil(request, d.proiect_id)
    tip = request.POST.get("tip")
    if tip not in dict(m.ExtragereAI.TIP):
        raise PermissionDenied
    ex = ai.ruleaza_extragere(d, tip)
    if ex.status == "eroare":
        messages.error(request, f"Extragerea nu a reușit: {ex.mesaj}")
        return redirect(f"{p.get_absolute_url()}?tab=documente")
    return redirect("ai_validare", pk=ex.pk)


@login_required
def ai_validare(request, pk):
    ex = get_object_or_404(m.ExtragereAI.objects.select_related("document__proiect"), pk=pk)
    p = _proiect_editabil(request, ex.document.proiect_id)
    lista_cheie = {"cu_avize": "avize", "contract": "conditii", "finantare": "obligatii"}[ex.tip]
    elemente = ex.rezultat.get(lista_cheie, []) or []
    antet_cheie = {"cu_avize": "certificat", "contract": "contract", "finantare": "finantare"}[ex.tip]
    antet = ex.rezultat.get(antet_cheie, {}) or {}
    if request.method == "POST" and ex.status == "propus":
        alese = {int(i) for i in request.POST.getlist("element")}
        selectie = [e for i, e in enumerate(elemente) if i in alese]
        if ex.tip == "cu_avize":  # avize / studii adăugate manual pe pagina de validare (intermediare sau suplimentare)
            for den, tip_a, orig in zip(request.POST.getlist("extra_denumire"), request.POST.getlist("extra_tip"), request.POST.getlist("extra_origine")):
                if den.strip():
                    selectie.append({"denumire": den.strip(), "tip": tip_a, "origine": orig, "manual": True})
        with transaction.atomic():
            n = _aplica_extragere(request, ex, p, antet, selectie)
            ex.status, ex.validat_de, ex.validat_la = "validat", request.user, timezone.now()
            ex.save()
        messages.success(request, f"Validat: {n} elemente au fost adăugate în proiect.")
        return redirect(f"{p.get_absolute_url()}?tab={ {'cu_avize': 'avize', 'contract': 'contracte', 'finantare': 'finantare'}[ex.tip] }")
    if request.method == "POST" and request.POST.get("respinge"):
        ex.status = "respins"
        ex.save()
        return redirect(f"{p.get_absolute_url()}?tab=documente")
    return render(request, "platforma/ai_validare.html", {"ex": ex, "p": p, "elemente": elemente, "antet": antet,
                                                         "tipuri_aviz": m.Aviz.TIP, "origini_aviz": m.Aviz.ORIGINE})


def _data_sau_none(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _aplica_extragere(request, ex, p, antet, elemente):
    d = ex.document
    acum = timezone.now()
    if ex.tip == "cu_avize":
        cu = d.certificat
        if not cu:
            cu = m.CertificatUrbanism.objects.create(
                proiect=p, numar=antet.get("numar") or "?", data=_data_sau_none(antet.get("data")) or timezone.localdate(),
                emitent=antet.get("emitent", ""), scop=antet.get("scop", ""), valabil_pana=_data_sau_none(antet.get("valabil_pana")),
            )
            m.Document.objects.filter(pk=d.pk).update(certificat=cu)
        for e in elemente:
            tip_a = e.get("tip") if e.get("tip") in dict(m.Aviz.TIP) else ("studiu" if "studiu" in e.get("denumire", "").lower() else "aviz")
            origine = e.get("origine") if e.get("origine") in dict(m.Aviz.ORIGINE) else "cu"
            m.Aviz.objects.create(proiect=p, certificat=cu, denumire=e.get("denumire", "")[:255], emitent=e.get("emitent", "")[:200],
                                  tip=tip_a, origine=origine, fragment_sursa=e.get("fragment", ""), extras_ai=not e.get("manual"))
    elif ex.tip == "contract":
        c = d.contract
        if not c:
            raise PermissionDenied("Documentul trebuie legat de un contract înainte de validare.")
        for e in elemente:
            tip = e.get("tip") if e.get("tip") in dict(m.ConditieContract.TIP) else "altele"
            m.ConditieContract.objects.create(
                contract=c, tip=tip, descriere=e.get("descriere", ""), termen=_data_sau_none(e.get("termen")),
                valoare=str(e.get("valoare", ""))[:120], fragment_sursa=e.get("fragment", ""), pagina=str(e.get("pagina", ""))[:20],
                extras_ai=True, validat_de=request.user, validat_la=acum,
            )
    else:
        fin = d.finantare
        for e in elemente:
            m.Obligatie.objects.create(
                proiect=p, finantare=fin, descriere=e.get("descriere", ""), termen=_data_sau_none(e.get("termen")),
                recurenta=str(e.get("recurenta", ""))[:60], fragment_sursa=e.get("fragment", ""), extras_ai=True,
            )
    return len(elemente)


# ------------------------------------------------------------------ Gantt
@login_required
def gantt_json(request, pk):
    p = _proiect_vizibil(request, pk)
    date = []
    for a in p.activitati.prefetch_related("depinde_de"):
        date.append({
            "id": str(a.pk), "name": a.denumire, "start": a.start.isoformat(), "end": a.sfarsit.isoformat(),
            "progress": a.progres, "dependencies": ",".join(str(x.pk) for x in a.depinde_de.all()),
        })
        if a.sfarsit < timezone.localdate() and a.progres < 100:
            date[-1]["custom_class"] = "intarziat"
    return JsonResponse(date, safe=False)


@login_required
@require_POST
def gantt_muta(request, pk, act_pk):
    p = _proiect_editabil(request, pk)
    a = get_object_or_404(m.Activitate, pk=act_pk, proiect=p)
    if request.POST.get("start"):
        a.start = datetime.date.fromisoformat(request.POST["start"][:10])
        a.sfarsit = datetime.date.fromisoformat(request.POST["end"][:10])
    if request.POST.get("progress"):
        a.progres = max(0, min(100, int(float(request.POST["progress"]))))
    a.save()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def gantt_import(request, pk):
    p = _proiect_editabil(request, pk)
    form = f.ImportGanttForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Fișier invalid.")
        return redirect(f"{p.get_absolute_url()}?tab=gantt")
    fis = form.cleaned_data["fisier"]
    try:
        randuri = _citeste_msproject(fis) if fis.name.lower().endswith(".xml") else _citeste_gantt_xlsx(fis)
    except Exception as e:  # noqa: BLE001
        messages.error(request, f"Nu am putut citi fișierul: {e}")
        return redirect(f"{p.get_absolute_url()}?tab=gantt")
    with transaction.atomic():
        if form.cleaned_data["inlocuieste"]:
            p.activitati.all().delete()
        for i, r in enumerate(randuri):
            m.Activitate.objects.create(proiect=p, ordine=i + 1, **r)
    messages.success(request, f"Import grafic: {len(randuri)} activități.")
    return redirect(f"{p.get_absolute_url()}?tab=gantt")


def _citeste_gantt_xlsx(fis):
    ws = openpyxl.load_workbook(fis, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    antet = [str(h or "").strip().lower() for h in rows[0]]

    def idx(*nume):
        for n in nume:
            for i, h in enumerate(antet):
                if h.startswith(n):
                    return i
        return None

    i_den, i_st, i_sf = idx("activ", "denumire", "task", "nume"), idx("start", "început", "inceput"), idx("sf", "final", "end")
    i_pr, i_resp = idx("progres", "%"), idx("respons")
    if None in (i_den, i_st, i_sf):
        raise ValueError("lipsesc coloanele Activitate / Start / Sfârșit")
    rez = []
    for r in rows[1:]:
        if not r[i_den]:
            continue
        st, sf = _ca_data(r[i_st]), _ca_data(r[i_sf])
        if not st or not sf:
            continue
        pr = r[i_pr] if i_pr is not None else 0
        pr = float(pr or 0)
        rez.append({"denumire": str(r[i_den])[:255], "start": st, "sfarsit": sf, "progres": int(pr * 100 if pr <= 1 and pr > 0 and i_pr is not None and isinstance(r[i_pr], float) else pr),
                    "responsabil": str(r[i_resp] or "")[:120] if i_resp is not None else ""})
    return rez


def _ca_data(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def _citeste_msproject(fis):
    root = ET.parse(fis).getroot()
    ns = {"p": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {"p": ""}
    pre = "p:" if ns["p"] else ""
    rez = []
    for t in root.iterfind(f".//{pre}Tasks/{pre}Task", ns):
        nume = t.findtext(f"{pre}Name", default="", namespaces=ns)
        st, sf = t.findtext(f"{pre}Start", namespaces=ns), t.findtext(f"{pre}Finish", namespaces=ns)
        if not nume or not st or t.findtext(f"{pre}Summary", default="0", namespaces=ns) == "1":
            continue
        rez.append({"denumire": nume[:255], "start": datetime.date.fromisoformat(st[:10]), "sfarsit": datetime.date.fromisoformat(sf[:10]),
                    "progres": int(t.findtext(f"{pre}PercentComplete", default="0", namespaces=ns) or 0)})
    return rez


# ------------------------------------------------------------------ jurnal
@login_required
def jurnal(request):
    if not vede_tot(request.user):
        raise PermissionDenied
    qs = m.JurnalAudit.objects.select_related("user", "proiect")
    if request.GET.get("q"):
        q = request.GET["q"]
        qs = qs.filter(Q(obiect__icontains=q) | Q(model__icontains=q) | Q(user__username__icontains=q) | Q(proiect__cod__icontains=q))
    return render(request, "platforma/jurnal.html", {"pagina": qs, "total": qs.count()})


# ------------------------------------------------------------------ export
def _export_xlsx(nume, coloane, qs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = nume[:30]
    ws.append([c for c, _ in coloane])
    for o in qs:
        rand = []
        for _, attr in coloane:
            v = getattr(o, attr)
            v = v() if callable(v) else v
            if isinstance(v, bool):
                v = "Da" if v else "Nu"
            elif isinstance(v, Decimal):
                v = float(v)
            elif v is not None and not isinstance(v, (int, float, str, datetime.date)):
                v = str(v)
            rand.append(v)
        ws.append(rand)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(45, max(10, max(len(str(c.value or "")) for c in col[:50]) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    r = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = f'attachment; filename="{nume}_{timezone.localdate():%Y%m%d}.xlsx"'
    return r


# ------------------------------------------------------------------ administrare: ștergere proiect cu cod SMS
COD_VALABIL_SECUNDE = 300
COD_MAX_INCERCARI = 3


def _hash_cod(cod, pk):
    import hashlib
    from django.conf import settings as st
    return hashlib.sha256(f"{cod}:{pk}:{st.SECRET_KEY}".encode()).hexdigest()


@login_required
def stergere_proiect(request):
    """Ștergerea definitivă a unui proiect, confirmată cu un cod trimis prin SMS pe telefonul administratorului."""
    import secrets
    import time
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul poate șterge proiecte.")
    telefon = getattr(getattr(request.user, "profil", None), "telefon", "")
    p = m.Proiect.objects.filter(pk=request.POST.get("proiect") or request.GET.get("proiect")).first()
    sesiune = request.session.get("stergere_2fa") or {}
    if request.method == "POST" and p:
        actiune = request.POST.get("actiune")
        if actiune == "trimite_cod":
            if not telefon:
                messages.error(request, "Nu aveți un număr de telefon în profil. Completați-l în Administrare → Utilizatori.")
            elif sesiune.get("pk") == p.pk and time.time() - sesiune.get("trimis", 0) < 60:
                messages.warning(request, "Un cod a fost trimis acum mai puțin de un minut. Așteptați înainte de a cere altul.")
            else:
                cod = f"{secrets.randbelow(1000000):06d}"
                try:
                    sms.trimite(telefon, f"Carmistin Investitii: codul pentru STERGEREA proiectului {p.cod} este {cod}. Valabil 5 minute. Nu il comunicati nimanui.")
                except Exception as e:  # noqa: BLE001
                    messages.error(request, f"SMS-ul nu a putut fi trimis: {e}")
                else:
                    request.session["stergere_2fa"] = {"pk": p.pk, "hash": _hash_cod(cod, p.pk), "trimis": time.time(), "incercari": 0}
                    m.JurnalAudit.objects.create(user=request.user, actiune="cod 2FA", model="ștergere proiect", obiect_id=str(p.pk),
                                                 obiect=f"Cod de ștergere trimis pentru {p.cod} către {sms.ascunde(telefon)}", proiect_id=p.pk)
                    messages.success(request, f"Codul a fost trimis prin SMS la {sms.ascunde(telefon)}. Este valabil 5 minute."
                                     + (" (MOD DEMO: SMS-ul nu pleacă efectiv; codul apare în jurnalul serverului.)" if sms.e_mod_demo() else ""))
            return redirect(f"{request.path}?proiect={p.pk}")
        if actiune == "sterge":
            eroare = None
            if sesiune.get("pk") != p.pk:
                eroare = "Cereți întâi un cod de verificare pentru acest proiect."
            elif time.time() - sesiune.get("trimis", 0) > COD_VALABIL_SECUNDE:
                eroare = "Codul a expirat. Cereți un cod nou."
            elif sesiune.get("incercari", 0) >= COD_MAX_INCERCARI:
                eroare = "Prea multe încercări greșite. Cereți un cod nou."
            elif request.POST.get("confirmare_cod", "").strip().upper() != p.cod.upper():
                eroare = f"Pentru confirmare scrieți exact codul proiectului: {p.cod}."
            elif not secrets.compare_digest(_hash_cod(request.POST.get("cod_sms", "").strip(), p.pk), sesiune.get("hash", "")):
                sesiune["incercari"] = sesiune.get("incercari", 0) + 1
                request.session["stergere_2fa"] = sesiune
                eroare = f"Cod SMS greșit ({COD_MAX_INCERCARI - sesiune['incercari']} încercări rămase)."
            if eroare:
                messages.error(request, eroare)
                return redirect(f"{request.path}?proiect={p.pk}")
            cod_p, den_p, pk_p = p.cod, p.denumire, p.pk
            arhiva = stergere.arhiveaza(p)
            stergere.sterge(p)
            request.session.pop("stergere_2fa", None)
            m.JurnalAudit.objects.create(user=request.user, actiune="stergere", model="proiect (definitiv, 2FA)", obiect_id=str(pk_p),
                                         obiect=f"{cod_p} · {den_p}", proiect_id=pk_p,
                                         modificari={"arhiva": str(arhiva.relative_to(arhiva.parents[2]))})
            messages.success(request, f"Proiectul {cod_p} a fost șters definitiv. O arhivă completă (date și documente) a fost păstrată pe server: {arhiva.name}.")
            return redirect("stergere_proiect")
    return render(request, "platforma/stergere_proiect.html", {
        "proiecte": m.Proiect.objects.select_related("beneficiar").order_by("cod"), "p": p,
        "rezumat": stergere.rezumat(p) if p else [], "telefon": sms.ascunde(telefon) if telefon else "",
        "cod_trimis": bool(p and sesiune.get("pk") == p.pk), "mod_demo": sms.e_mod_demo(),
    })
