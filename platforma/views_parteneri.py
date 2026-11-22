"""Registrul partenerilor: listă, fișa partenerului, adăugare după CUI (ANAF) și căutarea folosită în formularele de contract / factură."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import models as m
from .parteneri import EroareAnaf, ca_json, cui_valid, curata_cui, interogheaza_anaf, salveaza_partener
from .permisiuni import doar_citire, proiecte_vizibile


def _poate_modifica(user):
    if doar_citire(user):
        raise PermissionDenied("Contul are drept doar de citire.")


@login_required
def parteneri(request):
    q = (request.GET.get("q") or "").strip()
    lista = m.Partener.objects.annotate(nr_contracte=Count("contracte", distinct=True), nr_facturi=Count("facturi", distinct=True))
    if q:
        cui = curata_cui(q)
        lista = lista.filter(Q(denumire__icontains=q) | Q(nr_reg_com__icontains=q) | (Q(cui__startswith=cui) if cui else Q(pk=None)))
    lista = list(lista.order_by("denumire"))
    # valoarea contractată se însumează doar pe proiectele pe care utilizatorul le vede
    vizibile = proiecte_vizibile(request.user)
    valori = dict(m.Contract.objects.filter(proiect__in=vizibile, firma_partener__isnull=False)
                  .values_list("firma_partener").annotate(s=Sum("valoare_ron")))
    for pt in lista:
        pt.valoare_contracte = valori.get(pt.pk, Decimal(0))
    return render(request, "platforma/parteneri.html", {
        "parteneri": lista, "q": q, "total": m.Partener.objects.count(),
        "neverificati": m.Partener.objects.filter(verificat_anaf_la__isnull=True).count(),
        "poate_modifica": not doar_citire(request.user),
    })


@login_required
def partener_detaliu(request, pk):
    pt = get_object_or_404(m.Partener, pk=pk)
    if request.method == "POST":
        _poate_modifica(request.user)
        for camp, lung in (("telefon", 60), ("email", 120), ("persoana_contact", 120), ("iban", 40)):
            setattr(pt, camp, request.POST.get(camp, "").strip()[:lung])
        pt.observatii = request.POST.get("observatii", "").strip()
        pt.save()
        messages.success(request, "Datele de contact au fost salvate.")
        return redirect("partener", pk=pt.pk)
    vizibile = proiecte_vizibile(request.user)
    contracte = list(pt.contracte.filter(proiect__in=vizibile).select_related("proiect").prefetch_related("facturi__plati", "acte_aditionale"))
    facturi = list(pt.facturi.filter(proiect__in=vizibile).select_related("proiect", "contract").prefetch_related("plati").order_by("-data"))
    return render(request, "platforma/partener.html", {
        "pt": pt, "contracte": contracte, "facturi": facturi, "poate_modifica": not doar_citire(request.user),
        "total_contracte": sum((c.valoare_ron for c in contracte), Decimal(0)),
        "total_facturi": sum((f.valoare_ron for f in facturi), Decimal(0)),
        "total_platit": sum((f.platit_ron for f in facturi), Decimal(0)),
    })


@login_required
@require_POST
def partener_adauga(request):
    """Adăugare în registru după CUI, cu datele de la ANAF."""
    _poate_modifica(request.user)
    cui = curata_cui(request.POST.get("cui"))
    json = "application/json" in request.headers.get("Accept", "")  # din lista derulantă a formularului de contract / factură

    def eroare(text, cod=400):
        if json:
            return JsonResponse({"eroare": text}, status=cod)
        messages.error(request, text)
        return redirect("parteneri")
    if not cui_valid(cui):
        return eroare("CUI invalid (cifra de control nu corespunde).")
    try:
        date = interogheaza_anaf(cui)
    except EroareAnaf as e:
        return eroare(str(e), 503)
    if not date:
        return eroare(f"CUI {cui} nu figurează în baza de date ANAF.", 404)
    pt = salveaza_partener(cui, date_anaf=date)
    if json:
        return JsonResponse({**ca_json(pt), "nou_adaugat": True})
    messages.success(request, f"{pt.denumire} a fost adăugat / actualizat în registru.")
    return redirect("partener", pk=pt.pk)


@login_required
@require_POST
def partener_actualizeaza(request, pk):
    """Reverificarea datelor la ANAF (stare TVA, inactivitate, adresă)."""
    _poate_modifica(request.user)
    pt = get_object_or_404(m.Partener, pk=pk)
    from django.core.cache import cache
    cache.delete(f"anaf:{pt.cui}")
    try:
        date = interogheaza_anaf(pt.cui)
    except EroareAnaf as e:
        messages.error(request, str(e))
        return redirect("partener", pk=pt.pk)
    if not date:
        messages.warning(request, f"CUI {pt.cui} nu mai figurează la ANAF.")
    else:
        salveaza_partener(pt.cui, date_anaf=date)
        messages.success(request, "Datele au fost actualizate de la ANAF.")
    return redirect("partener", pk=pt.pk)


@login_required
def parteneri_cauta(request):
    """Căutare în registru pentru formulare (după denumire sau CUI)."""
    q = (request.GET.get("q") or "").strip()
    toti = m.Partener.objects.order_by("denumire")
    if not q:  # lista derulantă deschisă fără text: tot registrul (primii 100)
        return JsonResponse({"rezultate": [ca_json(pt) for pt in toti[:100]], "total": toti.count()})
    cui = curata_cui(q)
    filtru = Q(denumire__icontains=q) | (Q(cui__startswith=cui) if cui else Q(pk=None))
    if q.upper() != q.lower():  # SQLite compară fără diferențe de majuscule doar ASCII
        filtru |= Q(denumire__icontains=q.upper()) | Q(denumire__icontains=q.capitalize())
    return JsonResponse({"rezultate": [ca_json(pt) for pt in toti.filter(filtru)[:40]]})


@login_required
def partener_anaf(request):
    """Datele unei firme după CUI: din registru (dacă a fost verificată la ANAF) sau direct de la ANAF."""
    cui = curata_cui(request.GET.get("cui"))
    if not cui:
        return JsonResponse({"eroare": "Introduceți un CUI."}, status=400)
    if not cui_valid(cui):
        return JsonResponse({"eroare": f"CUI {cui} invalid (cifra de control nu corespunde)."}, status=400)
    try:
        date = interogheaza_anaf(cui)
    except EroareAnaf as e:
        pt = m.Partener.objects.filter(cui=cui).first()
        if pt:
            return JsonResponse({**ca_json(pt), "avertisment": str(e)})
        return JsonResponse({"eroare": str(e)}, status=503)
    if not date:
        return JsonResponse({"eroare": f"CUI {cui} nu figurează în baza de date ANAF."}, status=404)
    exista = m.Partener.objects.filter(cui=cui).exists()
    return JsonResponse({**date, "sursa": "anaf", "in_registru": exista})
