"""Repartizarea unui contract sau a unei facturi pe una sau mai multe linii de buget (bife + valoare pe fiecare linie).
Valoarea se introduce în moneda documentului (EUR dacă contractul / factura e în EUR) și se convertește în RON
la cursul documentului; diferența de rotunjire se pune pe ultima linie, ca totalul în RON să fie exact."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from . import models as m
from .permisiuni import e_admin, poate_edita, poate_edita_financiar

CENT = Decimal("0.01")


def _numar(v):
    t = str(v or "").strip().replace(" ", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return Decimal(t).quantize(CENT)
    except InvalidOperation:
        return None


def _document(tip, obj_pk):
    if tip == "contract":
        o = get_object_or_404(m.Contract, pk=obj_pk)
        return o, o.proiect, o.alocari, "contract", o.valoare, o.valoare_ron
    if tip == "factura":
        o = get_object_or_404(m.Factura, pk=obj_pk)
        return o, o.proiect, o.alocari, "factura", o.valoare, o.valoare_ron
    raise PermissionDenied


@login_required
def repartizare(request, tip, obj_pk):
    o, p, alocari, camp, baza, baza_ron = _document(tip, obj_pk)
    u = request.user
    if not (poate_edita(u, p) if tip == "contract" else poate_edita_financiar(u, p)):
        raise PermissionDenied("Nu aveți drept pentru această operație.")
    if tip == "factura" and o.lot_export_id and not e_admin(u):
        raise PermissionDenied("Factura a fost exportată în ERP; repartizarea nu se mai modifică.")
    eur = o.moneda == "EUR"
    curs = (o.curs or p.curs) if eur else Decimal(1)
    moneda = "EUR" if eur else "RON"
    existente = {a.linie_buget_id: a for a in alocari.all()}
    linii = list(p.linii_buget.prefetch_related(
        "rectificari", "alocari_contracte__contract__acte_aditionale", "alocari_contracte__contract__alocari", "acte_aditionale", "alocari"))
    cid = o.pk if tip == "contract" else (o.contract_id or "fara")
    inapoi = f"{p.get_absolute_url()}?tab=contracte&c={cid}"
    eroare = None
    valori = {}

    if request.method == "POST":
        bifate = [int(x) for x in request.POST.getlist("linie") if x.isdigit()]
        ids = {l.pk for l in linii}
        for lid in bifate:
            if lid not in ids:
                raise PermissionDenied
            valori[lid] = _numar(request.POST.get(f"v_{lid}"))
        if any(v is None or v <= 0 for v in valori.values()):
            eroare = "Completați o valoare pozitivă pentru fiecare linie bifată."
        elif sum(valori.values(), Decimal(0)) > baza + CENT:
            eroare = f"Totalul repartizat ({sum(valori.values()):,.2f} {moneda}) depășește valoarea {('contractului' if tip == 'contract' else 'facturii')} ({baza:,.2f} {moneda})."
        else:
            ron = {lid: (v * curs).quantize(CENT) for lid, v in valori.items()}
            if ron and sum(valori.values()) == baza:  # repartizare integrală: totalul în RON trebuie să fie exact
                ultima = bifate[-1]
                ron[ultima] += baza_ron - sum(ron.values())
            with transaction.atomic():
                for lid, a in existente.items():
                    if lid not in ron:
                        a.delete()
                for lid in bifate:
                    if lid in existente:
                        if existente[lid].valoare_ron != ron[lid]:
                            existente[lid].valoare_ron = ron[lid]
                            existente[lid].save()
                    else:
                        alocari.model.objects.create(**{camp: o, "linie_buget_id": lid, "valoare_ron": ron[lid]})
            rest = baza - sum(valori.values(), Decimal(0))
            messages.success(request, f"Repartizarea pe {len(bifate)} linii de buget a fost salvată."
                             + (f" Rămân nerepartizați {rest:,.2f} {moneda}.".replace(",", " ") if rest > 0 else ""))
            return redirect(inapoi)

    randuri = []
    for l in linii:
        a = existente.get(l.pk)
        if request.method == "POST":
            bifata, val = l.pk in valori, valori.get(l.pk)
        else:
            bifata, val = bool(a), ((a.valoare_ron / curs).quantize(CENT) if a else None)
        randuri.append({"l": l, "bifata": bifata, "valoare": "" if val is None else f"{val:.2f}".replace(".", ","),
                        "buget": l.valoare_curenta, "disponibil": l.disponibil + (a.valoare_ron if a and tip == "contract" else 0),
                        "cu_buget": bool(l.valoare_curenta)})
    eticheta = (f"contractul {o.numar} – {o.partener}" if tip == "contract" else f"factura {o.numar} – {o.furnizor}")
    return render(request, "platforma/repartizare.html", {
        "p": p, "o": o, "tip": tip, "titlu": f"Repartizare pe linii de buget – {p.cod}", "eticheta": eticheta,
        "randuri": randuri, "moneda": moneda, "curs": curs if eur else None, "baza": baza, "inapoi": inapoi, "eroare": eroare,
        "doar_cu_buget": any(r["cu_buget"] for r in randuri),
    })
