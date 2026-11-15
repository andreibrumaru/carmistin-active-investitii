"""Echipa proiectului, monitorizarea proiectului (etape) și raportul zilnic de activitate (cu fotografii)."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Max, Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import models as m
from . import poze
from .permisiuni import e_admin, poate_edita, poate_gestiona_echipa, poate_raporta, proiecte_vizibile

EXTENSII_POZE = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff")


def _proiect(request, pk):
    p = get_object_or_404(m.Proiect, pk=pk)
    if not proiecte_vizibile(request.user).filter(pk=p.pk).exists():
        raise PermissionDenied("Nu aveți drept de acces la acest proiect.")
    return p


def _inapoi(p, tab):
    return redirect(f"/proiecte/{p.pk}/?tab={tab}")


def _data(v):
    try:
        return parse_date(v) if v else None
    except ValueError:
        return None


def _int(v, implicit=None, minim=None, maxim=None):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return implicit
    if minim is not None:
        n = max(minim, n)
    if maxim is not None:
        n = min(maxim, n)
    return n


# ------------------------------------------------------------------ context pentru fișa proiectului
def arbore_etape(p):
    """Etapele principale, fiecare cu lista subetapelor ei în .copii (ordonate)."""
    toate = list(p.etape.select_related("responsabil__user"))
    for e in toate:
        e.copii = [x for x in toate if x.parinte_id == e.pk]
    return [e for e in toate if not e.parinte_id]


def etape_liniar(p):
    """Etapele în ordinea arborelui (principală, apoi subetapele ei) – pentru liste de alegere."""
    rez = []
    for e in arbore_etape(p):
        rez.append(e)
        rez += e.copii
    return rez

def context_monitorizare(request, p):
    u = request.user
    membri = list(p.echipa.select_related("user"))
    pe_categorii = [{"cod": c, "eticheta": e, "iconita": i, "membri": [x for x in membri if x.categorie == c]}
                    for c, e, i in m.MembruEchipa.CATEGORII]
    etape = arbore_etape(p)
    from django.db.models import Count, Q
    rapoarte = list(p.rapoarte_activitate.filter(Q(stare="emis") | Q(autor=u)).select_related("autor", "etapa")
                    .annotate(nr_poze=Count("poze", filter=Q(poze__arhivat=False, poze__categorie="foto"), distinct=True), nr_snag=Count("snag", distinct=True))[:100])
    progres = round(sum(e.progres for e in etape) / len(etape)) if etape else None  # media etapelor principale
    azi = timezone.localdate()
    for c in pe_categorii:
        c["membri_json"] = [membru_json(p, x) for x in c["membri"]]
    return {
        "utilizatori_json": [{"id": x.pk, "nume": x.get_full_name() or x.username,
                              "rol": x.profil.get_rol_display() if hasattr(x, "profil") else ""}
                             for x in User.objects.filter(is_active=True).select_related("profil").order_by("first_name", "last_name", "username")],
        "echipa_categorii": pe_categorii, "echipa_nr": len(membri), "echipa_membri": membri,
        "echipa_membri_json": [{**membru_json(p, x), "categorie": x.get_categorie_display()} for x in membri],
        "poate_echipa": poate_gestiona_echipa(u, p), "poate_raporta": poate_raporta(u, p),
        "utilizatori_echipa": User.objects.filter(is_active=True).order_by("first_name", "last_name", "username"),
        "etape": etape, "progres_etape": progres,
        "etape_finalizate": sum(1 for e in etape if e.status == "finalizata"),
        "etape_intarziate": sum(1 for e in etape for x in [e] + e.copii if x.intarziata),
        "rapoarte_activitate": rapoarte, "azi": azi,
        "raportat_azi": any(r.data == azi and r.autor_id == u.pk for r in rapoarte),
        "rapoarte_emise": sum(1 for r in rapoarte if r.stare == "emis"),
        "ciorne_mele": [r for r in rapoarte if r.stare == "ciorna"],
        "gantt_vechi": p.activitati.count() if not etape else 0,
        "statusuri_etapa": m.EtapaProiect.STATUS,
    }


# ------------------------------------------------------------------ echipa proiectului
def _vrea_json(request):
    return "application/json" in request.headers.get("Accept", "")


def membru_json(p, mb):
    nume = mb.nume_afisat or "?"
    initiale = "".join(x[0] for x in nume.split()[:2]).upper()
    return {"id": mb.pk, "nume": nume, "initiale": initiale, "cont": bool(mb.user_id), "user": mb.user_id,
            "detalii": " · ".join(x for x in (mb.functie, mb.firma, mb.telefon, mb.email) if x),
            "sterge": f"/proiecte/{p.pk}/echipa/{mb.pk}/sterge/"}


def _raspuns_echipa(request, p, ok, mesaj, mb=None):
    if _vrea_json(request):
        return JsonResponse({"ok": ok, "mesaj": mesaj, "membru": membru_json(p, mb) if mb else None}, status=200 if ok else 400)
    (messages.success if ok else messages.error)(request, mesaj)
    return _inapoi(p, "echipa")


@login_required
@require_POST
def echipa_adauga(request, pk):
    p = _proiect(request, pk)
    if not poate_gestiona_echipa(request.user, p):
        raise PermissionDenied("Echipa o stabilește responsabilul proiectului sau administratorul.")
    g = request.POST
    categorie = g.get("categorie", "")
    if categorie not in dict((c, 1) for c, _, _ in m.MembruEchipa.CATEGORII):
        return _raspuns_echipa(request, p, False, "Alegeți categoria din echipă.")
    user = User.objects.filter(pk=_int(g.get("user"), 0), is_active=True).first()
    nume = g.get("nume", "").strip()
    if not user and not nume:
        return _raspuns_echipa(request, p, False, "Alegeți un utilizator al aplicației sau scrieți numele persoanei externe.")
    if user and p.echipa.filter(user=user, categorie=categorie).exists():
        return _raspuns_echipa(request, p, False, f"{user.get_full_name() or user.username} este deja în această categorie.")
    mb = m.MembruEchipa.objects.create(
        proiect=p, categorie=categorie, user=user, nume="" if user else nume[:150], functie=g.get("functie", "").strip()[:150],
        firma=g.get("firma", "").strip()[:150], telefon=g.get("telefon", "").strip()[:40], email=g.get("email", "").strip()[:254],
        adaugat_de=request.user)
    return _raspuns_echipa(request, p, True, f"{mb.nume_afisat} a fost adăugat(ă) în echipă – {mb.get_categorie_display()}."
                           + (" Are acum acces la proiect și poate scrie rapoarte de activitate." if user else ""), mb)


@login_required
@require_POST
def echipa_sterge(request, pk, mpk):
    p = _proiect(request, pk)
    if not poate_gestiona_echipa(request.user, p):
        raise PermissionDenied
    mb = get_object_or_404(m.MembruEchipa, pk=mpk, proiect=p)
    nume = mb.nume_afisat
    mb.delete()
    return _raspuns_echipa(request, p, True, f"{nume} a fost scos(scoasă) din echipa proiectului.")


@login_required
def echipe_admin(request):
    """Tablou administrativ: echipele tuturor proiectelor, pe categorii."""
    if not e_admin(request.user):
        raise PermissionDenied("Doar administratorul vede tabloul echipelor.")
    proiecte = (m.Proiect.objects.select_related("pm", "coordonator", "beneficiar")
                .prefetch_related(Prefetch("echipa", queryset=m.MembruEchipa.objects.select_related("user")))
                .annotate(nr_echipa=Count("echipa")).order_by("-status", "cod"))
    filtru = request.GET.get("filtru", "")
    if filtru == "fara":
        proiecte = proiecte.filter(nr_echipa=0)
    elif filtru == "executie":
        proiecte = proiecte.filter(status="În execuție")
    randuri = []
    for p in proiecte:
        membri = list(p.echipa.all())
        randuri.append({"p": p, "nr": len(membri), "pe_cat": [[x for x in membri if x.categorie == c] for c, _, _ in m.MembruEchipa.CATEGORII]})
    return render(request, "platforma/echipe_admin.html", {
        "randuri": randuri, "categorii": m.MembruEchipa.CATEGORII, "filtru": filtru,
        "total_fara": sum(1 for r in randuri if not r["nr"]) if not filtru else m.Proiect.objects.annotate(n=Count("echipa")).filter(n=0).count(),
    })


# ------------------------------------------------------------------ etapele proiectului
def etape_principale(p):
    """Etapele principale propuse după tipul investiției și tipul de autorizare: (denumire, descriere, pondere în durată)."""
    tip, cu_ac = p.tip_investitie, p.tip_autorizare == "cu_ac"
    lucrari = tip in ("lucrari", "lucrari_ech", "mentenanta") or not tip
    echip = tip in ("echipamente", "lucrari_ech", "mentenanta")
    E = [("Inițiere și Raport Zero avizat", "Echipa de proiect, bugetul și graficul inițial", 3)]
    if tip == "studii":
        E += [("Achiziții și contractare", "Selecția și contractarea elaboratorului", 10),
              ("Elaborare studii / masterplan", "Culegere date, variante, documentația propriu-zisă", 45),
              ("Avizare internă și recepție studiu", "Observații, revizii, recepția documentației", 12)]
    else:
        if lucrari:
            E.append(("Studii și documentații tehnice", "SF / DALI, studii topografice și geotehnice", 8))
        if cu_ac or lucrari:
            E.append(("Certificat de urbanism și avize", "CU și avizele / acordurile cerute prin CU", 10))
        if lucrari:
            E.append(("Proiectare tehnică", "PT + DE, verificarea proiectului", 10))
        if cu_ac:
            E.append(("Autorizație de construire", "Depunerea documentației și obținerea AC", 8))
        E.append(("Achiziții și contractare", "Oferte, selecția furnizorilor / executanților, contracte", 10))
        if echip:
            E.append(("Livrare echipamente", "Fabricație, transport, recepție cantitativă", 12))
        if lucrari and tip != "echipamente":
            E.append(("Execuția lucrărilor", "Organizare de șantier, execuție, faze determinante", 25))
        if echip:
            E.append(("Montaj, probe și punere în funcțiune", "Montaj, teste, instruirea personalului", 8))
        E.append(("Recepția la terminarea lucrărilor" if cu_ac else "Recepție și punere în funcțiune",
                  "Comisia de recepție, procesul-verbal, remedieri", 4))
    E.append(("Închiderea proiectului", "PV de închidere, decontare finală, documentație as-built", 2))
    return E


@login_required
@require_POST
def etape_genereaza(request, pk):
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    existente = {e.denumire.lower() for e in p.etape.filter(parinte__isnull=True)}
    propuse = [x for x in etape_principale(p) if x[0].lower() not in existente]
    if not propuse:
        messages.info(request, "Etapele principale există deja.")
        return _inapoi(p, "monitorizare")
    # termenele planificate: durata proiectului împărțită proporțional cu ponderea etapelor
    start, final = p.data_demarare, p.data_finalizare
    zile = (final - start).days if start and final and final > start else None
    total = sum(x[2] for x in propuse)
    urm = (p.etape.filter(parinte__isnull=True).aggregate(m=Max("ordine"))["m"] or 0) + 1
    cursor = start
    with transaction.atomic():
        for i, (den, desc, pond) in enumerate(propuse):
            sp = fp = None
            if zile:
                sp = cursor
                fp = final if i == len(propuse) - 1 else start + datetime.timedelta(days=round(zile * sum(x[2] for x in propuse[:i + 1]) / total))
                cursor = fp
            m.EtapaProiect.objects.create(proiect=p, ordine=urm + i, denumire=den, descriere=desc, start_plan=sp, final_plan=fp,
                                          status="finalizata" if i == 0 and p.stare_rz == "aprobat" else "neinceputa",
                                          progres=100 if i == 0 and p.stare_rz == "aprobat" else 0,
                                          final_real=(timezone.localtime(p.aprobat_la).date() if p.aprobat_la else timezone.localdate())
                                          if i == 0 and p.stare_rz == "aprobat" else None)
    messages.success(request, f"Au fost generate {len(propuse)} etape principale"
                     + (" cu termene planificate proporțional între demarare și termenul de finalizare." if zile else
                        " (fără termene: proiectul nu are data de demarare și termenul de finalizare).")
                     + " Le puteți modifica și puteți insera altele.")
    return _inapoi(p, "monitorizare")


@login_required
@require_POST
def etape_din_gantt(request, pk):
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    if p.etape.exists():
        messages.info(request, "Proiectul are deja etape.")
        return _inapoi(p, "monitorizare")
    with transaction.atomic():
        for i, a in enumerate(p.activitati.all(), start=1):
            m.EtapaProiect.objects.create(
                proiect=p, ordine=i, denumire=a.denumire[:200], start_plan=a.start_planificat or a.start, final_plan=a.sfarsit_planificat or a.sfarsit,
                start_real=a.start if a.progres else None, progres=min(a.progres, 100),
                status="finalizata" if a.progres >= 100 else "in_curs" if a.progres else "neinceputa",
                observatii=f"Responsabil (din graficul Gantt): {a.responsabil}" if a.responsabil else "")
    messages.success(request, "Activitățile din vechiul grafic Gantt au fost preluate ca etape.")
    return _inapoi(p, "monitorizare")


def _citeste_etapa(e, g, p):
    e.denumire = (g.get("denumire") or e.denumire or "").strip()[:200]
    e.descriere = g.get("descriere", e.descriere or "").strip()[:300]
    for camp in ("start_plan", "final_plan", "start_real", "final_real"):
        if camp in g:
            setattr(e, camp, _data(g.get(camp)))
    if "responsabil" in g:
        e.responsabil = p.echipa.filter(pk=_int(g.get("responsabil"), 0)).first()
    if "observatii" in g:
        e.observatii = g.get("observatii", "").strip()
    if "progres" in g:
        e.progres = _int(g.get("progres"), e.progres, 0, 100)
    st = g.get("status")
    if st in dict(m.EtapaProiect.STATUS):
        e.status = st
    if e.status == "finalizata":
        e.progres = 100
        e.final_real = e.final_real or timezone.localdate()
    elif e.progres >= 100 and st != "blocata":
        e.status, e.final_real = "finalizata", e.final_real or timezone.localdate()
    elif e.progres > 0 and e.status == "neinceputa":
        e.status = "in_curs"
    if e.status in ("in_curs", "finalizata") and not e.start_real:
        e.start_real = timezone.localdate()
    return e


def _rand_etapa(request, p, e):
    from django.template.loader import render_to_string
    if not e.parinte_id:
        e.copii = list(e.subetape.all())
    return render_to_string("platforma/_etapa_rand.html", {
        "e": e, "p": p, "editabil": True, "echipa_membri": list(p.echipa.select_related("user")),
        "statusuri_etapa": m.EtapaProiect.STATUS}, request=request)


def _json_etapa(request, p, e, sters=False):
    """Răspunsul JSON după o modificare: rândul etapei și, la subetape, rândul etapei principale (progres recalculat)."""
    rez = {"ok": True, "html": "" if sters else _rand_etapa(request, p, e)}
    if e.parinte_id:
        par = m.EtapaProiect.objects.get(pk=e.parinte_id)
        rez["parinte_id"], rez["parinte_html"] = par.pk, _rand_etapa(request, p, par)
    return JsonResponse(rez)


@login_required
@require_POST
def etape_ordine(request, pk):
    """Ordinea etapelor după mutarea cu mouse-ul: id-urile unui nivel (etapele principale sau subetapele unei etape), în noua ordine."""
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    ids = [int(x) for x in request.POST.getlist("etapa") if x.isdigit()]
    parinte = _int(request.POST.get("parinte"), None)
    existente = set(p.etape.filter(parinte_id=parinte).values_list("pk", flat=True))
    if set(ids) != existente:
        return JsonResponse({"ok": False, "mesaj": "Lista etapelor s-a schimbat între timp – reîncărcați pagina."}, status=400)
    with transaction.atomic():
        for i, pk_ in enumerate(ids, start=1):
            m.EtapaProiect.objects.filter(pk=pk_).update(ordine=i)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def etapa_adauga(request, pk):
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    g = request.POST
    if not g.get("denumire", "").strip():
        if _vrea_json(request):
            return JsonResponse({"ok": False, "mesaj": "Scrieți denumirea etapei."}, status=400)
        messages.error(request, "Scrieți denumirea etapei.")
        return _inapoi(p, "monitorizare")
    parinte = p.etape.filter(pk=_int(g.get("parinte"), 0), parinte__isnull=True).first()  # subetapele au un singur nivel
    etape = list(p.etape.filter(parinte=parinte))
    dupa = next((e for e in etape if str(e.pk) == g.get("dupa")), None)
    ordine = (dupa.ordine + 1) if dupa else ((max((e.ordine for e in etape), default=0)) + 1)
    with transaction.atomic():
        if dupa:  # inserare după etapa aleasă: etapele următoare se deplasează
            for e in etape:
                if e.ordine >= ordine:
                    m.EtapaProiect.objects.filter(pk=e.pk).update(ordine=e.ordine + 1)
        e = _citeste_etapa(m.EtapaProiect(proiect=p, ordine=ordine, parinte=parinte), g, p)
        e.save()
        if parinte:
            parinte.actualizeaza_din_subetape()
    if _vrea_json(request):
        return _json_etapa(request, p, e)
    messages.success(request, f"Etapa „{e.denumire}” a fost adăugată.")
    return _inapoi(p, "monitorizare")


@login_required
@require_POST
def etapa_modifica(request, pk, epk):
    p = _proiect(request, pk)
    e = get_object_or_404(m.EtapaProiect, pk=epk, proiect=p)
    g = request.POST
    actiune = g.get("actiune", "salveaza")
    responsabilul_etapei = bool(e.responsabil_id and e.responsabil.user_id == request.user.pk)
    # responsabilul etapei își actualizează singur statusul și data finalizării (din „Sarcinile mele” sau din listă)
    if not (poate_edita(request.user, p) or (responsabilul_etapei and actiune in ("status", "data_final"))):
        raise PermissionDenied("Etapa o actualizează responsabilul ei sau cine are drept de editare pe proiect.")
    if actiune == "responsabil":  # responsabilul ales din echipa proiectului (sau un utilizator adăugat atunci în echipă)
        uid = _int(g.get("user"), 0)
        if uid:
            if not poate_gestiona_echipa(request.user, p):
                return JsonResponse({"ok": False, "mesaj": "Doar responsabilul proiectului sau administratorul adaugă oameni în echipă."}, status=403)
            u = get_object_or_404(User, pk=uid, is_active=True)
            mb = p.echipa.filter(user=u).order_by("pk").first() or m.MembruEchipa.objects.create(
                proiect=p, categorie="echipa", user=u, adaugat_de=request.user)
        else:
            mb = p.echipa.filter(pk=_int(g.get("responsabil"), 0)).first()
        anterior = e.responsabil_id
        e.responsabil = mb
        e.save(update_fields=["responsabil"])
        if mb and mb.user_id and mb.pk != anterior and mb.user_id != request.user.pk:  # notificare: sarcină nouă
            m.Notificare.objects.create(
                user_id=mb.user_id, proiect=p, link="/sarcini/",
                titlu=f"Sarcină nouă: {'subetapa' if e.parinte_id else 'etapa'} „{e.denumire}” – {p.cod}"[:200],
                text=(f"termen {e.final_plan:%d.%m.%Y} · " if e.final_plan else "") + f"alocată de {request.user.get_full_name() or request.user.username}")
        return _json_etapa(request, p, e)
    if actiune == "status":  # statusul ales direct din rând
        st = g.get("status")
        if st not in dict(m.EtapaProiect.STATUS):
            return JsonResponse({"ok": False, "mesaj": "Status necunoscut."}, status=400)
        if st != "finalizata" and e.status == "finalizata":
            e.final_real = None
            e.progres = min(e.progres, 90)
        _citeste_etapa(e, {"status": st}, p).save()
        if e.parinte_id:
            e.parinte.actualizeaza_din_subetape()
        return _json_etapa(request, p, e)
    if actiune == "data_final":  # data de la capătul rândului: finalizarea etapei (golită = etapa redevine în curs)
        e.final_real = _data(g.get("final_real"))
        if e.final_real:
            e.status, e.progres = "finalizata", 100
            e.start_real = e.start_real or e.final_real
        elif e.status == "finalizata":
            e.status, e.progres = "in_curs", min(e.progres, 90)
        e.save()
        if e.parinte_id:
            e.parinte.actualizeaza_din_subetape()
        return _json_etapa(request, p, e)
    if actiune == "sterge":
        if e.rapoarte.exists():
            m.RaportActivitate.objects.filter(etapa=e).update(etapa=None)
        par = e.parinte
        m.RaportActivitate.objects.filter(etapa__parinte=e).update(etapa=None)
        e.delete()
        if par:
            par.actualizeaza_din_subetape()
        if _vrea_json(request):
            if par:
                return JsonResponse({"ok": True, "parinte_id": par.pk, "parinte_html": _rand_etapa(request, p, par)})
            return JsonResponse({"ok": True})
        messages.success(request, f"Etapa „{e.denumire}” a fost ștearsă.")
    elif actiune in ("sus", "jos"):
        etape = list(p.etape.all())
        i = etape.index(e)
        j = i - 1 if actiune == "sus" else i + 1
        if 0 <= j < len(etape):
            etape[i], etape[j] = etape[j], etape[i]
            for k, x in enumerate(etape, start=1):
                m.EtapaProiect.objects.filter(pk=x.pk).update(ordine=k)
    else:
        _citeste_etapa(e, g, p).save()
        if e.parinte_id:
            e.parinte.actualizeaza_din_subetape()
        if _vrea_json(request):
            return _json_etapa(request, p, e)
        messages.success(request, f"Etapa „{e.denumire}” a fost actualizată.")
    return _inapoi(p, "monitorizare")


# ------------------------------------------------------------------ raportul zilnic de activitate
@login_required
@require_POST
def raport_activitate_nou(request, pk):
    p = _proiect(request, pk)
    if not poate_raporta(request.user, p):
        raise PermissionDenied("Rapoartele de activitate le scriu responsabilii și membrii echipei proiectului.")
    g = request.POST
    activitati = g.get("activitati", "").strip()
    if not activitati:
        messages.error(request, "Descrieți activitățile desfășurate.")
        return _inapoi(p, "monitorizare")
    data = _data(g.get("data")) or timezone.localdate()
    if data > timezone.localdate():
        messages.error(request, "Raportul nu poate avea o dată din viitor.")
        return _inapoi(p, "monitorizare")
    etapa = p.etape.filter(pk=_int(g.get("etapa"), 0)).first()
    fisiere = request.FILES.getlist("poze")
    respinse = [x.name for x in fisiere if not x.name.lower().endswith(EXTENSII_POZE)]
    fisiere = [x for x in fisiere if x.name.lower().endswith(EXTENSII_POZE)][:30]
    with transaction.atomic():
        r = m.RaportActivitate.objects.create(
            proiect=p, data=data, autor=request.user, etapa=etapa, activitati=activitati,
            probleme=g.get("probleme", "").strip(), pasi_urmatori=g.get("pasi_urmatori", "").strip(),
            personal=_int(g.get("personal"), None, 0), meteo=g.get("meteo", "").strip()[:60])
        for x in fisiere:
            poze.salveaza(x, proiect=p, raport_activitate=r, incarcat_de=request.user,
                          titlu=f"Raport {data:%d.%m.%Y} – {x.name.rsplit('.', 1)[0]}"[:255])
        progres = _int(g.get("progres_etapa"), None, 0, 100)
        if etapa and progres is not None and progres != etapa.progres and poate_edita(request.user, p):
            _citeste_etapa(etapa, {"progres": progres}, p).save()
    messages.success(request, f"Raportul de activitate din {data:%d.%m.%Y} a fost salvat"
                     + (f", cu {len(fisiere)} fotografi{'e' if len(fisiere) == 1 else 'i'}." if fisiere else "."))
    if respinse:
        messages.warning(request, f"Nu sunt imagini și nu au fost atașate: {', '.join(respinse)}")
    return _inapoi(p, "monitorizare")


@login_required
@require_POST
def raport_activitate_sterge(request, pk, rpk):
    p = _proiect(request, pk)
    r = get_object_or_404(m.RaportActivitate, pk=rpk, proiect=p)
    if not (e_admin(request.user) or (r.autor_id == request.user.pk and r.stare == "ciorna")):
        raise PermissionDenied("Ciorna o poate șterge autorul; un raport emis îl poate șterge doar administratorul.")
    with transaction.atomic():
        r.poze.update(arhivat=True, raport_activitate=None)  # fotografiile rămân arhivate în documentele proiectului
        r.delete()
    messages.success(request, "Raportul de activitate a fost șters (fotografiile rămân arhivate în documentele proiectului).")
    return _inapoi(p, "monitorizare")


@login_required
def raport_activitate_pdf(request, pk, rpk):
    """Raportul de activitate ca fișier PDF (se deschide în vizualizatorul aplicației; ?dl=1 = descărcare)."""
    p = _proiect(request, pk)
    r = get_object_or_404(m.RaportActivitate.objects.select_related("proiect__beneficiar", "proiect__locatie", "autor", "etapa"), pk=rpk, proiect=p)
    from .pdf_raport import genereaza
    nume = f"Raport_santier_{r.cod_raport}_{p.cod.replace('.', '_')}_{r.data:%Y-%m-%d}_{r.pk}.pdf"
    raspuns = HttpResponse(genereaza(r), content_type="application/pdf")
    raspuns["Content-Disposition"] = f'{"attachment" if request.GET.get("dl") == "1" else "inline"}; filename="{nume}"'
    return raspuns
