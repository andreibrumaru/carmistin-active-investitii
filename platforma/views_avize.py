"""CU & avize: tabel simplu (certificatul de urbanism, apoi avizele), denumire editabilă direct în tabel, „+” pentru adăugare,
status Nedepus / Depus / Obținut (datele de depunere / obținere rezultă din schimbarea statusului)."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import models as m
from .avize_nume import prescurteaza
from .permisiuni import poate_edita, proiecte_vizibile


def _proiect_editabil(request, pk):
    p = get_object_or_404(m.Proiect, pk=pk)
    if not proiecte_vizibile(request.user).filter(pk=p.pk).exists() or not poate_edita(request.user, p):
        raise PermissionDenied("Nu aveți drept de modificare pe acest proiect.")
    return p


def _rand(request, p, a):
    a = m.Aviz.objects.select_related("responsabil").prefetch_related("documente").get(pk=a.pk)
    return render_to_string("platforma/_aviz_rand.html", {"a": a, "p": p, "editabil": True, "azi": timezone.localdate(),
                                                          "statusuri_aviz": m.Aviz.STATUS}, request=request)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@login_required
@require_POST
def aviz_adauga(request, pk):
    p = _proiect_editabil(request, pk)
    den = (request.POST.get("denumire") or "").strip()
    if not den:
        return JsonResponse({"ok": False, "mesaj": "Scrieți denumirea avizului / studiului."}, status=400)
    avize = list(p.avize.all())
    dupa = next((a for a in avize if a.pk == _int(request.POST.get("dupa"))), None)
    with transaction.atomic():
        if dupa:
            ordine = dupa.ordine + 1
            p.avize.filter(ordine__gte=ordine).update(ordine=F("ordine") + 1)
        else:
            ordine = (p.avize.aggregate(m=Max("ordine"))["m"] or 0) + 1
        scurt = prescurteaza(den)
        a = m.Aviz.objects.create(proiect=p, ordine=ordine, denumire=scurt[:255], denumire_completa=den[:500] if scurt != den else "",
                                  tip="studiu" if "studi" in den.lower() else "aviz", origine="suplimentar",
                                  certificat=p.certificate_urbanism.first())
    return JsonResponse({"ok": True, "html": _rand(request, p, a)})


@login_required
@require_POST
def aviz_modifica(request, pk, apk):
    p = _proiect_editabil(request, pk)
    a = get_object_or_404(m.Aviz, pk=apk, proiect=p)
    g, actiune = request.POST, request.POST.get("actiune")
    azi = timezone.localdate()
    if actiune == "denumire":
        den = (g.get("denumire") or "").strip()
        if not den:
            return JsonResponse({"ok": False, "mesaj": "Denumirea nu poate fi goală."}, status=400)
        if len(den) > 80 and not a.denumire_completa:
            a.denumire_completa = den[:500]
        a.denumire = den[:255]
        a.save(update_fields=["denumire", "denumire_completa"])
    elif actiune == "status":
        st = g.get("status")
        if st not in dict(m.Aviz.STATUS):
            return JsonResponse({"ok": False, "mesaj": "Status necunoscut."}, status=400)
        a.status = st
        if st in ("depus", "obtinut") and not a.data_depunere:
            a.data_depunere = azi
        if st == "obtinut" and not a.data_aviz:
            a.data_aviz = azi
        if st == "nedepus":
            a.data_depunere = None
        if st != "obtinut":
            a.data_aviz = None
        a.save(update_fields=["status", "data_depunere", "data_aviz"])
    elif actiune == "valabil":
        a.valabil_pana = parse_date(g.get("valabil_pana") or "") if g.get("valabil_pana") else None
        a.save(update_fields=["valabil_pana"])
    elif actiune == "sterge":
        if a.documente.exists():
            return JsonResponse({"ok": False, "mesaj": "Avizul are documente atașate – nu se șterge (arhivați documentul sau păstrați rândul)."}, status=400)
        a.delete()
        return JsonResponse({"ok": True})
    else:
        return JsonResponse({"ok": False, "mesaj": "Acțiune necunoscută."}, status=400)
    return JsonResponse({"ok": True, "html": _rand(request, p, a)})


@login_required
@require_POST
def avize_simplifica(request, pk):
    """Prescurtează denumirile lungi ale avizelor proiectului (denumirea completă rămâne păstrată)."""
    from django.contrib import messages
    p = _proiect_editabil(request, pk)
    n = 0
    for a in p.avize.all():
        scurt = prescurteaza(a.denumire)
        if scurt and scurt != a.denumire:
            a.denumire_completa = a.denumire_completa or a.denumire
            a.denumire = scurt[:255]
            a.save(update_fields=["denumire", "denumire_completa"])
            n += 1
    messages.success(request, f"{n} denumir{'e' if n == 1 else 'i'} prescurtat{'ă' if n == 1 else 'e'}. "
                     "Denumirea completă din CU se vede trecând cu mouse-ul peste denumire." if n else "Denumirile sunt deja scurte.")
    return redirect(f"/proiecte/{p.pk}/?tab=avize")


@login_required
@require_POST
def avize_ordine(request, pk):
    """Ordinea avizelor după mutarea cu mouse-ul (lista completă de id-uri, în noua ordine)."""
    p = _proiect_editabil(request, pk)
    ids = [int(x) for x in request.POST.getlist("aviz") if x.isdigit()]
    if set(ids) != set(p.avize.values_list("pk", flat=True)):
        return JsonResponse({"ok": False, "mesaj": "Lista avizelor s-a schimbat între timp – reîncărcați pagina."}, status=400)
    with transaction.atomic():
        for i, apk in enumerate(ids, start=1):
            m.Aviz.objects.filter(pk=apk).update(ordine=i)
    return JsonResponse({"ok": True})
