"""Închiderea investiției: Procesul-verbal de recepție.
- proiect cu autorizație de construire → Proces-verbal de recepție la terminarea lucrărilor (HG 343/2017);
- proiect fără autorizație → Proces-verbal de recepție și punere în funcțiune (formular 14-2-5/b, OMFP 2634/2015).
Procesul-verbal păstrează situația investiției la data recepției; la semnare proiectul trece în faza „Finalizat”."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import forms as f, models as m
from . import status_investitie as stinv
from .permisiuni import e_admin, poate_edita
from .views import _proiect_vizibil

ZERO = Decimal(0)


def _s(v):
    return str(v) if v is not None else None


def situatie_investitie(p):
    """Fotografia investiției la data recepției (se salvează în procesul-verbal)."""
    linii = list(p.linii_buget.prefetch_related("rectificari", "alocari", "alocari_contracte", "acte_aditionale"))
    contracte = list(p.contracte.prefetch_related("facturi__plati", "acte_aditionale"))
    facturi = list(p.facturi.prefetch_related("plati"))
    rez = {
        "data": timezone.localdate().isoformat(),
        "buget_rz": _s(p.buget_rz_ron), "buget_aprobat": _s(p.buget_aprobat_ron), "buget_actualizat": _s(p.buget_actualizat_ron),
        "angajat": _s(p.angajat_ron), "facturat": _s(p.facturat_ron), "platit": _s(p.platit_ron),
        "rest_plata": _s(p.facturat_ron - p.platit_ron), "progres_fizic": p.progres_fizic,
        "data_demarare": _s(p.data_demarare), "data_finalizare": _s(p.data_finalizare),
        "linii": [{"capitol": l.capitol, "denumire": l.denumire, "aprobat": _s(l.valoare_aprobata), "actualizat": _s(l.valoare_curenta),
                   "angajat": _s(l.angajat), "facturat": _s(l.facturat), "diferenta": _s(l.valoare_curenta - l.facturat)}
                  for l in linii if l.valoare_curenta or l.facturat],
        "contracte": [{"numar": c.numar, "data": _s(c.data), "partener": c.partener, "tip": c.get_tip_display(),
                       "valoare": _s(c.valoare_totala_ron), "facturat": _s(c.facturat_ron), "platit": _s(c.platit_ron),
                       "status": c.get_status_display()} for c in contracte],
        "facturi_neachitate": [{"numar": x.numar, "furnizor": x.furnizor, "data": _s(x.data), "rest": _s(x.rest_de_plata)}
                               for x in facturi if not x.achitata],
        "avize": [{"denumire": a.denumire, "tip": a.get_tip_display(), "status": a.get_status_display(), "numar": a.numar_aviz,
                   "data": _s(a.data_aviz), "valabil_pana": _s(a.valabil_pana)} for a in p.avize.all()],
        "echipamente": [{"denumire": e.denumire, "cantitate": _s(e.cantitate), "um": e.um, "valoare": _s(e.valoare_ron or e.valoare_estimata),
                         "status": e.get_status_display()} for e in p.echipamente.all()],
        "structura": [{"eticheta": x["eticheta"], "ron": _s(x["ron"]), "procent": x["procent"]} for x in p.structura_finantare],
        "credit_tras": _s(p.credit_tras_ron) if (p.fin_bancara or p.cereri_tragere.exists()) else None,
    }
    return rez


def verificari(p, tip):
    """Ce nu e încă închis: (nivel, text). Nu blochează recepția, dar apare la emitere."""
    v = []
    facturi = list(p.facturi.prefetch_related("plati"))
    neachitate = [x for x in facturi if not x.achitata]
    if neachitate:
        v.append(("warn", f"{len(neachitate)} facturi neachitate integral (rest de plată {sum((x.rest_de_plata for x in neachitate), ZERO):,.2f} RON)".replace(",", " ")))
    active = p.contracte.filter(status__in=["activ", "negociere"]).count()
    if active:
        v.append(("warn", f"{active} contracte încă „în derulare” / „în negociere”"))
    if tip == "terminare":
        lipsa = p.avize.exclude(status__in=["obtinut", "nu_e_cazul"]).count()
        if lipsa:
            v.append(("crit", f"{lipsa} avize / acorduri neobținute"))
    nerec = p.echipamente.exclude(status="receptionat").count()
    if nerec:
        v.append(("warn", f"{nerec} echipamente din lista RZ nerecepționate (vor fi marcate „recepționat” la semnare)"))
    if p.progres_fizic < 100:
        v.append(("info", f"progres fizic declarat {p.progres_fizic}% (devine 100% la semnare)"))
    depasite = [l.capitol for l in p.linii_buget.all() if l.depasit]
    if depasite:
        v.append(("crit", "linii de buget depășite: " + ", ".join(depasite)))
    if not facturi:
        v.append(("info", "proiectul nu are facturi înregistrate"))
    return v


def _tip_pv(p):
    return "terminare" if p.tip_autorizare == "cu_ac" else "pif"


def _initial(p, tip):
    """Precompletare din datele proiectului."""
    parteneri = list(dict.fromkeys(c.partener for c in p.contracte.order_by("-pk") if c.partener))
    ac = p.avize.filter(tip="autorizatie").exclude(numar_aviz="").first() or \
        p.avize.filter(denumire__icontains="autoriza").exclude(numar_aviz="").first()
    membri = [f"{u.get_full_name() or u.username} – {et}" for u, et in ((p.coordonator, "coordonator"), (p.pm, "project manager")) if u]
    n = p.procese_verbale.count() + 1
    ini = {
        "numar": f"{p.cod}/PV-{n:02d}", "data": timezone.localdate(), "loc_receptie": p.locatie.adresa or str(p.locatie),
        "data_incepere": p.data_demarare, "data_terminare": p.data_finalizare_reala or timezone.localdate(),
        "executant": ", ".join(parteneri[:3]), "membri_comisie": "\n".join(membri),
        "obiective_realizate": p.descriere_lucrari, "data_pif": timezone.localdate(), "decizie": "admisa",
    }
    if tip == "terminare" and ac:
        ini.update(nr_autorizatie=ac.numar_aviz, data_autorizatie=ac.data_aviz, emitent_autorizatie=ac.emitent)
    return ini


def _poate_semna(user, p):
    return e_admin(user) or user.pk == p.coordonator_id


@login_required
def pv_formular(request, pk, pvpk=None):
    p = _proiect_vizibil(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied("Nu aveți drept de editare pe acest proiect.")
    if not p.tip_autorizare:
        messages.warning(request, "Stabiliți întâi tipul proiectului (cu sau fără autorizație de construire) – în „Urmărire”, tabul Prezentare.")
        return redirect(f"{p.get_absolute_url()}?tab=prezentare")
    pv = get_object_or_404(m.ProcesVerbalInchidere, pk=pvpk, proiect=p) if pvpk else None
    if pv and pv.status == "semnat":
        messages.info(request, "Procesul-verbal este semnat și nu se mai modifică.")
        return redirect("pv_detaliu", pk=p.pk, pvpk=pv.pk)
    if not pv:
        ciorna = p.procese_verbale.filter(status="ciorna").first()
        if ciorna:
            return redirect("pv_editare", pk=p.pk, pvpk=ciorna.pk)
    tip = pv.tip if pv else _tip_pv(p)
    form = f.ProcesVerbalForm(request.POST or None, request.FILES or None, instance=pv, tip=tip,
                              initial=None if pv else _initial(p, tip))
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            o = form.save(commit=False)
            o.proiect, o.tip = p, tip
            if not o.pk:
                o.creat_de = request.user
            o.situatie = situatie_investitie(p)
            o.save()
            fisier = form.cleaned_data.get("scan")
            if fisier:
                anterior = o.documente.filter(arhivat=False).first()
                m.Document.objects.create(proiect=p, proces_verbal=o, categorie="pv_inchidere", titlu=f"Proces-verbal {o.numar} (semnat)",
                                          fisier=fisier, incarcat_de=request.user,
                                          inlocuieste=anterior if anterior and not hasattr(anterior, "inlocuit_de") else None)
        messages.success(request, f"{o} salvat. Verificați documentul, apoi semnați-l pentru a închide investiția.")
        return redirect("pv_detaliu", pk=p.pk, pvpk=o.pk)
    return render(request, "platforma/pv_formular.html", {
        "p": p, "form": form, "pv": pv, "tip": tip, "eticheta_tip": dict(m.ProcesVerbalInchidere.TIP)[tip],
        "verificari": verificari(p, tip), "inapoi": f"{p.get_absolute_url()}?tab=prezentare",
    })


@login_required
def pv_detaliu(request, pk, pvpk):
    p = _proiect_vizibil(request, pk)
    pv = get_object_or_404(m.ProcesVerbalInchidere.objects.select_related("creat_de"), pk=pvpk, proiect=p)
    s = pv.situatie or {}
    membri = [x.strip() for x in (pv.membri_comisie or "").splitlines() if x.strip()]
    return render(request, "platforma/pv_inchidere.html", {
        "p": p, "pv": pv, "s": s, "membri": membri, "editabil": poate_edita(request.user, p) and pv.status == "ciorna",
        "poate_semna": _poate_semna(request.user, p) and pv.status == "ciorna" and pv.decizie in ("admisa", "admisa_obiectii"),
        "poate_redeschide": e_admin(request.user) and pv.status == "semnat",
        "verificari": verificari(p, pv.tip) if pv.status == "ciorna" else [],
        "documente": pv.documente.filter(arhivat=False),
        "temei": "HG nr. 343/2017 – Regulamentul privind recepția construcțiilor" if pv.tip == "terminare"
                 else "Formular 14-2-5/b – OMFP nr. 2634/2015 privind documentele financiar-contabile",
    })


@login_required
@require_POST
def pv_actiune(request, pk, pvpk):
    p = _proiect_vizibil(request, pk)
    pv = get_object_or_404(m.ProcesVerbalInchidere, pk=pvpk, proiect=p)
    act = request.POST.get("actiune")
    if act == "semneaza":
        if not _poate_semna(request.user, p):
            raise PermissionDenied("Doar coordonatorul proiectului sau administratorul semnează închiderea investiției.")
        if pv.status != "ciorna" or pv.decizie not in ("admisa", "admisa_obiectii"):
            messages.error(request, "Se poate semna doar un proces-verbal în ciornă, cu recepția admisă.")
            return redirect("pv_detaliu", pk=p.pk, pvpk=pv.pk)
        with transaction.atomic():
            pv.situatie = situatie_investitie(p)
            pv.status, pv.semnat_la = "semnat", timezone.now()
            pv.save()
            p.faza, p.progres_fizic = "Finalizat", 100
            p.data_finalizare_reala = pv.data_terminare or pv.data
            p.save()
            p.echipamente.exclude(status="receptionat").update(status="receptionat")
            # statusul „Finalizat” se aplică doar cu aprobarea administratorului (imediat, dacă semnează chiar el)
            aplicat = False
            if p.status != stinv.FINALIZAT and not p.cerere_status_deschisa:
                try:
                    _, aplicat = stinv.solicita(p, stinv.FINALIZAT, request.user, f"Investiție închisă prin {pv} (semnat).")
                except ValueError:
                    pass
        messages.success(request, f"{pv} a fost semnat (fază „Finalizat”, finalizată la {p.data_finalizare_reala:%d.%m.%Y}). "
                         + ("Statusul investiției: Finalizat." if aplicat or p.status == stinv.FINALIZAT
                            else "Statusul „Finalizat” așteaptă aprobarea administratorului."))
    elif act == "redeschide":
        if not e_admin(request.user):
            raise PermissionDenied
        pv.status, pv.semnat_la = "ciorna", None
        pv.save()
        messages.warning(request, f"{pv} a fost redeschis pentru corecturi. Faza proiectului rămâne neschimbată; actualizați-o din „Urmărire” dacă e cazul.")
    elif act == "sterge" and pv.status == "ciorna" and poate_edita(request.user, p):
        if pv.documente.exists():
            messages.error(request, "Procesul-verbal are documente atașate și nu se mai șterge.")
            return redirect("pv_detaliu", pk=p.pk, pvpk=pv.pk)
        pv.delete()
        messages.success(request, "Ciorna procesului-verbal a fost ștearsă.")
        return redirect(f"{p.get_absolute_url()}?tab=prezentare")
    else:
        raise PermissionDenied
    return redirect("pv_detaliu", pk=p.pk, pvpk=pv.pk)
