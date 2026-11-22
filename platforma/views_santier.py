"""Rapoarte de șantier: formular (ciornă → emis), vizualizare, notificarea echipei (în aplicație + email)."""
import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import models as m
from . import poze
from .permisiuni import e_admin, poate_edita, poate_raporta, proiecte_vizibile

EXTENSII_POZE = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")
EXTENSII_AVIZ = EXTENSII_POZE + (".pdf",)


def materiale_cu_avize(r):
    """Materialele raportului, fiecare cu documentele avizelor de însoțire (x["docs"])."""
    if not r:
        return []
    docs = {d.pk: d for d in r.poze.filter(categorie="aviz_insotire", arhivat=False)}
    rez = []
    for x in r.materiale or []:
        rez.append({**x, "docs": [docs[i] for i in x.get("avize", []) if i in docs]})
    return rez
VREME = ["Senin", "Parțial noros", "Înnorat", "Ceață", "Ploaie", "Averse", "Ninsoare", "Vânt puternic", "Îngheț", "Caniculă"]


def _proiect(request, pk):
    p = get_object_or_404(m.Proiect, pk=pk)
    if not proiecte_vizibile(request.user).filter(pk=p.pk).exists():
        raise PermissionDenied("Nu aveți drept de acces la acest proiect.")
    return p


def _poate_modifica(user, r):
    return r.stare == "ciorna" and (r.autor_id == user.pk or e_admin(user))


def _int(v):
    try:
        return max(0, int(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _randuri(post, prefix, campuri):
    """Rândurile unui tabel dinamic (câmpuri cu același nume, în ordine); rândurile goale se ignoră."""
    coloane = [post.getlist(f"{prefix}_{c}") for c in campuri]
    rez = []
    for valori in zip(*coloane):
        rand = {c: (v or "").strip()[:200] for c, v in zip(campuri, valori)}
        if rand[campuri[0]]:
            rez.append(rand)
    return rez


def _sugestii(p):
    """Firme propuse la „Personal în șantier”: partenerii din contractele proiectului și firmele din echipă."""
    firme = set(p.contracte.exclude(partener="").values_list("partener", flat=True))
    firme |= set(p.echipa.exclude(firma="").values_list("firma", flat=True))
    return sorted(firme, key=str.lower)


def _salveaza(request, p, r):
    g = request.POST
    r.data = parse_date(g.get("data") or "") or r.data or timezone.localdate()
    if r.data > timezone.localdate():
        r.data = timezone.localdate()
    r.meteo = " · ".join(x for x in (g.get("vreme", "").strip(), g.get("temperatura", "").strip()) if x)[:60]
    r.constructori = [{"firma": x["firma"], "muncitori": _int(x["muncitori"]) or 0} for x in _randuri(g, "con", ["firma", "muncitori"])]
    r.personal = sum(c["muncitori"] for c in r.constructori) or None
    r.activitati = "\n".join(a.strip() for a in g.getlist("activitate") if a.strip())
    # materiale aprovizionate: rânduri cu cheie, ca avizele de însoțire atașate să rămână legate de rândul lor
    avize_vechi = {d.pk: d for d in r.poze.filter(categorie="aviz_insotire")} if r.pk else {}
    randuri_mat = []
    for k in g.getlist("mat_key"):
        rand = {c: g.get(f"mat_{c}_{k}", "").strip()[:200] for c in ("denumire", "cantitate", "um", "furnizor")}
        rand["avize"] = [int(x) for x in g.get(f"mat_avize_ids_{k}", "").split(",") if x.isdigit() and int(x) in avize_vechi]
        fisiere = [x for x in request.FILES.getlist(f"mat_avize_{k}")[:10] if x.name.lower().endswith(EXTENSII_AVIZ)]
        if rand["denumire"] or rand["avize"] or fisiere:
            randuri_mat.append((rand, fisiere))
    r.materiale = [x for x, _ in randuri_mat]
    r.utilaje = _randuri(g, "util", ["denumire", "numar", "observatii"])
    r.probleme = g.get("probleme", "").strip()
    r.pasi_urmatori = g.get("pasi_urmatori", "").strip()
    r.save()
    pastrate_avize = set()
    for rand, fisiere in randuri_mat:
        for x in fisiere:
            titlu = f"Aviz însoțire – {rand['denumire'] or 'material'}" + (f" ({rand['furnizor']})" if rand["furnizor"] else "")
            if x.name.lower().endswith(EXTENSII_POZE):
                d = poze.salveaza(x, categorie="aviz_insotire", proiect=p, raport_activitate=r, incarcat_de=request.user, titlu=titlu[:255])
            else:
                d = m.Document.objects.create(proiect=p, raport_activitate=r, categorie="aviz_insotire", fisier=x,
                                              incarcat_de=request.user, titlu=titlu[:255])
            rand["avize"].append(d.pk)
        pastrate_avize |= set(rand["avize"])
    r.materiale = [x for x, _ in randuri_mat]
    r.save(update_fields=["materiale"])
    for pk_, d in avize_vechi.items():  # avizele rândurilor șterse rămân arhivate în documentele proiectului
        if pk_ not in pastrate_avize:
            m.Document.objects.filter(pk=pk_).update(arhivat=True, raport_activitate=None)
    # snag list: rândurile au o cheie, ca fotografiile fiecărui punct să rămână legate de el
    existente = {str(s.pk): s for s in r.snag.all()}
    pastrate = set()
    for i, k in enumerate(g.getlist("snag_key"), start=1):
        desc = g.get(f"snag_descriere_{k}", "").strip()[:300]
        s = existente.get(g.get(f"snag_id_{k}", ""))
        if not desc:
            continue
        s = s or m.PunctSnag(raport=r)
        s.ordine, s.descriere = i, desc
        s.zona = g.get(f"snag_zona_{k}", "").strip()[:150]
        s.responsabil = g.get(f"snag_responsabil_{k}", "").strip()[:150]
        s.termen = parse_date(g.get(f"snag_termen_{k}") or "") if g.get(f"snag_termen_{k}") else None
        s.save()
        pastrate.add(s.pk)
        for x in request.FILES.getlist(f"snag_poze_{k}")[:10]:
            if x.name.lower().endswith(EXTENSII_POZE):
                poze.salveaza(x, proiect=p, raport_activitate=r, punct_snag=s, incarcat_de=request.user,
                              titlu=f"Snag {i} – {desc[:60]}"[:255])
    for pk_, s in existente.items():
        if s.pk not in pastrate:
            s.poze.update(punct_snag=None, arhivat=True)
            s.delete()
    for x in request.FILES.getlist("poze")[:30]:
        if x.name.lower().endswith(EXTENSII_POZE):
            poze.salveaza(x, proiect=p, raport_activitate=r, incarcat_de=request.user,
                          titlu=f"Raport șantier {r.data:%d.%m.%Y} – {x.name.rsplit('.', 1)[0]}"[:255])



def destinatari(p, autor):
    """Echipa proiectului cu cont în aplicație + responsabilul proiectului, fără autorul raportului."""
    ids = set(p.echipa.exclude(user=None).values_list("user_id", flat=True)) | {x for x in (p.pm_id, p.coordonator_id) if x}
    ids.discard(getattr(autor, "pk", None))
    return list(User.objects.filter(pk__in=ids, is_active=True))


def notifica(p, r, autor):
    """Notificare în aplicație pentru fiecare destinatar + email (dacă are adresă). Întoarce (nr. notificați, nr. emailuri, eroare)."""
    link = reverse("santier_detaliu", args=[p.pk, r.pk])
    titlu = f"Raport de șantier {r.cod_raport} – {p.cod}"
    autor_n = (autor.get_full_name() or autor.username) if autor else "—"
    text = f"{r.data:%d.%m.%Y} · întocmit de {autor_n}"
    oameni = destinatari(p, autor)
    m.Notificare.objects.bulk_create([m.Notificare(user=u, proiect=p, titlu=titlu, text=text, link=link) for u in oameni])
    mesaje = []
    for u in oameni:
        if not u.email:
            continue
        url = settings.SITE_URL + link
        corp = (f"Bună ziua{', ' + u.first_name if u.first_name else ''},\n\n{autor_n} a emis raportul de șantier {r.cod_raport} "
                f"din {r.data:%d.%m.%Y} pentru proiectul {p.cod} – {p.denumire}.\n\nRaportul se poate vedea aici:\n{url}\n\n"
                f"Activități: {len(r.lista_activitati)} · personal în șantier: {r.total_muncitori} · puncte snag list: {r.snag.count()}\n\n"
                "Mesaj trimis automat de Carmistin – Active & Investiții.")
        html = (f"<p>Bună ziua{', ' + u.first_name if u.first_name else ''},</p><p><b>{autor_n}</b> a emis raportul de șantier "
                f"<b>{r.cod_raport}</b> din {r.data:%d.%m.%Y} pentru proiectul <b>{p.cod} – {p.denumire}</b>.</p>"
                f"<p><a href=\"{url}\" style=\"background:#1d9e75;color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none\">"
                f"Deschide raportul</a></p><p style=\"color:#6b7c78;font-size:12px\">Mesaj trimis automat de Carmistin – Active &amp; Investiții.</p>")
        msg = EmailMultiAlternatives(f"{titlu} – {p.denumire}"[:200], corp, settings.DEFAULT_FROM_EMAIL, [u.email],
                                     reply_to=[autor.email] if autor and autor.email else None)
        msg.attach_alternative(html, "text/html")
        mesaje.append(msg)
    eroare = ""
    if mesaje:
        try:
            get_connection(fail_silently=False).send_messages(mesaje)
        except Exception as ex:  # noqa: BLE001
            eroare = str(ex)[:200]
    return len(oameni), (0 if eroare else len(mesaje)), eroare


@login_required
def santier_formular(request, pk, rpk=None):
    p = _proiect(request, pk)
    if not poate_raporta(request.user, p):
        raise PermissionDenied("Rapoartele de șantier le întocmesc responsabilii și membrii echipei proiectului.")
    r = get_object_or_404(m.RaportActivitate, pk=rpk, proiect=p) if rpk else None
    if r and not _poate_modifica(request.user, r):
        return redirect("santier_detaliu", pk=p.pk, rpk=r.pk)
    if request.method == "POST":
        finalizeaza = request.POST.get("actiune") == "finalizeaza"
        with transaction.atomic():
            r = r or m.RaportActivitate(proiect=p, autor=request.user, stare="ciorna")
            _salveaza(request, p, r)
            if finalizeaza:
                if not r.lista_activitati:
                    transaction.set_rollback(False)
                    messages.error(request, "Raportul a fost salvat ca ciornă: completați cel puțin o activitate realizată ca să-l puteți finaliza.")
                    return redirect("santier_editare", pk=p.pk, rpk=r.pk)
                r.numar = (p.rapoarte_activitate.filter(stare="emis").aggregate(n=Max("numar"))["n"] or 0) + 1
                r.stare, r.emis_la = "emis", timezone.now()
                r.save(update_fields=["numar", "stare", "emis_la"])
        if not finalizeaza:
            messages.success(request, "Ciorna raportului de șantier a fost salvată. O puteți completa și finaliza oricând.")
            return redirect("santier_editare", pk=p.pk, rpk=r.pk)
        nr, emailuri, eroare = notifica(p, r, request.user)
        messages.success(request, f"Raportul de șantier {r.cod_raport} a fost emis. "
                         + (f"Au fost notificați {nr} membri ai echipei" + (f" ({emailuri} prin email)" if emailuri else "") + "." if nr
                            else "Nu există alți membri ai echipei cu cont de notificat – adăugați echipa din Setări."))
        if eroare:
            messages.warning(request, f"Notificările din aplicație au plecat, dar emailurile nu au putut fi trimise: {eroare}")
        return redirect("santier_detaliu", pk=p.pk, rpk=r.pk)
    vreme, temp = "", ""
    if r and r.meteo:
        parti = r.meteo.split(" · ", 1)
        vreme, temp = (parti + [""])[:2] if parti[0] in VREME else ("", r.meteo)
    return render(request, "platforma/santier_form.html", {
        "p": p, "r": r, "etape": [e for e in __import__("platforma.views_monitorizare", fromlist=["x"]).etape_liniar(p)
                                   if e.status != "finalizata" or (r and r.etapa_id == e.pk)],
        "azi": timezone.localdate(), "vreme_optiuni": VREME, "vreme": vreme, "temperatura": temp,
        "firme": _sugestii(p), "editabil": poate_edita(request.user, p),
        "constructori": (r.constructori if r else []) or [{"firma": "", "muncitori": ""}],
        "activitati": (r.lista_activitati if r else []) or [""],
        "materiale": materiale_cu_avize(r) or [{}], "utilaje": (r.utilaje if r else []) or [{}],
        "snag": list(r.snag.prefetch_related("poze")) if r else [],
        "poze_generale": list(r.poze.filter(punct_snag__isnull=True, arhivat=False, categorie="foto")) if r else [],
    })


@login_required
def santier_detaliu(request, pk, rpk):
    p = _proiect(request, pk)
    r = get_object_or_404(m.RaportActivitate.objects.select_related("autor", "etapa"), pk=rpk, proiect=p)
    if r.stare == "ciorna" and not _poate_modifica(request.user, r):
        raise PermissionDenied("Raportul este încă ciornă; îl vede doar autorul.")
    m.Notificare.objects.filter(user=request.user, link=request.path, citita_la__isnull=True).update(citita_la=timezone.now())
    return render(request, "platforma/santier_detaliu.html", {
        "p": p, "r": r, "snag": list(r.snag.prefetch_related("poze")),
        "poze_generale": list(r.poze.filter(punct_snag__isnull=True, arhivat=False, categorie="foto")),
        "materiale": materiale_cu_avize(r),
        "poate_modifica": _poate_modifica(request.user, r),
        "notificati": m.Notificare.objects.filter(link=reverse("santier_detaliu", args=[p.pk, r.pk])).select_related("user"),
    })


@login_required
def notificare_deschide(request, npk):
    n = get_object_or_404(m.Notificare, pk=npk, user=request.user)
    if not n.citita_la:
        n.citita_la = timezone.now()
        n.save(update_fields=["citita_la"])
    return redirect(n.link)


@login_required
@require_POST
def notificari_citite(request):
    m.Notificare.objects.filter(user=request.user, citita_la__isnull=True).update(citita_la=timezone.now())
    from django.utils.http import url_has_allowed_host_and_scheme
    inapoi = request.POST.get("inapoi") or "/"
    return redirect(inapoi if url_has_allowed_host_and_scheme(inapoi, allowed_hosts={request.get_host()}) else "/")


@login_required
def sarcini(request):
    """Sarcinile mele: generate din alocări (etape / subetape, raportul zilnic de șantier), structurate pe proiecte."""
    from .sarcini import sarcini_utilizator
    date = sarcini_utilizator(request.user)
    filtru = request.GET.get("filtru", "")
    if filtru in ("azi", "intarziate"):
        cheie = "azi" if filtru == "azi" else "intarziata"
        grupuri = []
        for g in date["grupuri"]:
            s = [x for x in g["sarcini"] if x[cheie] or (filtru == "azi" and x["intarziata"])]
            if s:
                grupuri.append({**g, "sarcini": s})
        date = {**date, "grupuri": grupuri}
    proiecte_al = list(proiecte_alocare(request.user))
    vedere = request.GET.get("vedere", "")
    if vedere == "alocare" and not proiecte_al:
        vedere = ""
    ctx = {**date, "filtru": filtru, "azi": timezone.localdate(), "statusuri_etapa": m.EtapaProiect.STATUS,
           "poate_aloca": bool(proiecte_al), "vedere": vedere}
    if vedere == "aprobari":
        from .views import context_aprobari
        ctx.update(context_aprobari(request.user))
    if ctx["vedere"] == "alocare":
        from django.db.models import Q
        ids = [x.pk for x in proiecte_al]
        alocate = (m.SarcinaAlocata.objects.filter(Q(proiect_id__in=ids) | Q(creat_de=request.user))
                   .select_related("proiect", "finalizata_de").prefetch_related("responsabili").distinct())
        ctx.update({
            "proiecte_alocare": proiecte_al,
            "echipe_alocare": {str(x.pk): [u.pk for u in echipa_alocabila(x)] for x in proiecte_al},
            "utilizatori_alocare": [{"id": u.pk, "nume": u.get_full_name() or u.username, "rol": u.profil.get_rol_display() if hasattr(u, "profil") else ""}
                                    for u in User.objects.filter(is_active=True).select_related("profil").order_by("first_name", "last_name", "username")],
            "alocate_deschise": [s for s in alocate if s.stare == "deschisa"],
            "alocate_finalizate": [s for s in alocate if s.stare == "finalizata"][:30],
        })
    return render(request, "platforma/sarcini.html", ctx)


@login_required
def santier_meteo(request, pk):
    """Vremea pentru raportul de șantier: din coordonatele activului proiectului, pentru data raportului (JSON)."""
    from django.http import JsonResponse
    from . import meteo
    p = _proiect(request, pk)
    if not poate_raporta(request.user, p):
        raise PermissionDenied
    loc = p.locatie
    if not loc or loc.lat is None or loc.lng is None:
        motiv = "proiectul nu are activ alocat" if not loc else f"activul {loc.cod or loc.denumire} nu are coordonate în registru"
        return JsonResponse({"ok": False, "mesaj": f"Vremea nu se poate prelua automat: {motiv}. Completați-o manual."})
    data = parse_date(request.GET.get("data") or "") or timezone.localdate()
    try:
        rez = meteo.vremea(loc.lat, loc.lng, data)
    except ValueError as ex:
        return JsonResponse({"ok": False, "mesaj": str(ex)})
    return JsonResponse({"ok": True, **rez, "sursa": f"Open-Meteo · {loc.cod or loc.denumire} · {data:%d.%m.%Y}"})


# ------------------------------------------------------------------ alocarea sarcinilor (responsabilul proiectului → echipa)
def proiecte_alocare(user):
    """Proiectele pe care utilizatorul poate aloca sarcini: ale căror Setări le vede (responsabil proiect / administrator)."""
    from .permisiuni import e_admin as _adm
    qs = m.Proiect.objects.select_related("pm", "coordonator")
    if not _adm(user):
        from django.db.models import Q
        qs = qs.filter(Q(pm=user) | Q(coordonator=user))
    return qs.exclude(status__in=["Finalizat", "Anulat/Suspendat"]).order_by("cod")


def echipa_alocabila(p):
    """Oamenii cărora li se pot da sarcini pe proiect: echipa cu cont în aplicație + responsabilul proiectului."""
    ids = set(p.echipa.exclude(user=None).values_list("user_id", flat=True)) | {x for x in (p.pm_id, p.coordonator_id) if x}
    return list(User.objects.filter(pk__in=ids, is_active=True).order_by("first_name", "last_name", "username"))


def _rand_sarcina(request, s):
    from django.template.loader import render_to_string
    return render_to_string("platforma/_sarcina_alocata.html", {"s": s, "azi": timezone.localdate(), "aloc": True}, request=request)


@login_required
@require_POST
def sarcina_adauga(request):
    from django.http import JsonResponse
    g = request.POST
    p = proiecte_alocare(request.user).filter(pk=_int(g.get("proiect")) or 0).first()
    if not p:
        return JsonResponse({"ok": False, "mesaj": "Alegeți proiectul (unul la care sunteți responsabil)."}, status=400)
    desc = (g.get("descriere") or "").strip()
    if not desc:
        return JsonResponse({"ok": False, "mesaj": "Scrieți descrierea sarcinii."}, status=400)
    # oricine are cont activ: membrii echipei sau colegi din afara ei (sarcina o văd la „Sarcinile mele”; accesul la proiect nu se schimbă)
    oameni = list(User.objects.filter(pk__in={_int(x) for x in g.getlist("responsabil")} - {None}, is_active=True))
    if not oameni:
        return JsonResponse({"ok": False, "mesaj": "Alegeți cel puțin un responsabil."}, status=400)
    termen = parse_date(g.get("termen") or "") if g.get("termen") else None
    with transaction.atomic():
        s = m.SarcinaAlocata.objects.create(proiect=p, descriere=desc[:300], termen=termen, creat_de=request.user)
        s.responsabili.set(oameni)
        autor = request.user.get_full_name() or request.user.username
        m.Notificare.objects.bulk_create([m.Notificare(
            user=u, proiect=p, link="/sarcini/", titlu=f"Sarcină nouă – {p.cod}: {desc}"[:200],
            text=(f"termen {termen:%d.%m.%Y} · " if termen else "") + f"alocată de {autor}") for u in oameni if u.pk != request.user.pk])
    return JsonResponse({"ok": True, "html": _rand_sarcina(request, s)})


@login_required
@require_POST
def sarcina_modifica(request, spk):
    from django.http import JsonResponse
    s = get_object_or_404(m.SarcinaAlocata.objects.select_related("proiect"), pk=spk)
    poate_aloca = proiecte_alocare(request.user).filter(pk=s.proiect_id).exists() or s.creat_de_id == request.user.pk
    e_responsabil = s.responsabili.filter(pk=request.user.pk).exists()
    actiune = request.POST.get("actiune")
    if actiune in ("finalizeaza", "redeschide") and (poate_aloca or e_responsabil):
        s.stare = "finalizata" if actiune == "finalizeaza" else "deschisa"
        s.finalizata_de, s.finalizata_la = (request.user, timezone.now()) if actiune == "finalizeaza" else (None, None)
        s.save(update_fields=["stare", "finalizata_de", "finalizata_la"])
        if actiune == "finalizeaza" and s.creat_de_id and s.creat_de_id != request.user.pk:
            m.Notificare.objects.create(user_id=s.creat_de_id, proiect=s.proiect, link="/sarcini/?vedere=alocare",
                                        titlu=f"Sarcină finalizată – {s.proiect.cod}: {s.descriere}"[:200],
                                        text=f"de {request.user.get_full_name() or request.user.username}")
    elif actiune == "sterge" and poate_aloca:
        s.delete()
        return JsonResponse({"ok": True})
    else:
        raise PermissionDenied("Sarcina o modifică cine a alocat-o, responsabilul proiectului sau persoanele cărora le-a fost dată.")
    return JsonResponse({"ok": True, "html": _rand_sarcina(request, s)})


@login_required
def rapoarte_activitate(request):
    """Rapoartele de șantier emise: implicit cele din ziua aleasă (azi); scriind un cod de proiect – toate ale proiectului, sortate."""
    from .views import rapoarte_santier_recente
    from django.db.models import Count, Q
    from .permisiuni import e_admin as _adm, proiecte_vizibile as _viz
    if rapoarte_santier_recente(request.user, 1) is None:
        raise PermissionDenied("Lista rapoartelor de activitate o văd administratorii și coordonatorii de proiect.")
    g = request.GET
    azi = timezone.localdate()
    zi = parse_date(g.get("zi") or "") or azi
    q = (g.get("q") or "").strip()
    qs = m.RaportActivitate.objects.filter(stare="emis")
    if not _adm(request.user):
        qs = qs.filter(proiect__in=_viz(request.user))
    if q:
        qs = qs.filter(Q(proiect__cod__icontains=q) | Q(proiect__denumire__icontains=q)).order_by("proiect__cod", "-data", "-numar")
    else:
        qs = qs.filter(data=zi).order_by("proiect__cod", "-emis_la")
    rapoarte = list(qs.select_related("proiect", "autor").annotate(
        nr_poze=Count("poze", filter=Q(poze__arhivat=False, poze__categorie="foto"), distinct=True),
        nr_snag=Count("snag", distinct=True))[:300])
    return render(request, "platforma/rapoarte_activitate.html", {
        "rapoarte": rapoarte, "zi": zi, "azi": azi, "q": q,
        "zi_prec": zi - datetime.timedelta(days=1), "zi_urm": zi + datetime.timedelta(days=1) if zi < azi else None,
        "proiecte_cu_rapoarte": len({r.proiect_id for r in rapoarte}),
    })
