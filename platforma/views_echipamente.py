"""Alocarea unui echipament din lista RZ pe un contract și schimbarea statusului, direct din sub-ecranul „Echipamente”."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from . import models as m
from .permisiuni import poate_edita


def _echipament(request, pk):
    e = get_object_or_404(m.Echipament.objects.select_related("proiect", "contract"), pk=pk)
    if not poate_edita(request.user, e.proiect):
        raise PermissionDenied
    return e


def _inapoi(e):
    return redirect(f"{e.proiect.get_absolute_url()}?tab=echipamente")


def _pret(text):
    t = str(text or "").strip().replace(" ", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        v = Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError
    if v < 0:
        raise ValueError
    return v


@login_required
@require_POST
def echipament_aloca(request, pk):
    e = _echipament(request, pk)
    cid = request.POST.get("contract", "")
    if not cid:
        if e.contract_id:
            vechi = e.contract.numar
            e.contract, e.valoare_unitara, e.curs = None, None, None
            if e.status == "contractat":
                e.status = "planificat"
            e.save()
            messages.success(request, f"„{e.denumire}” nu mai este alocat pe contractul {vechi}.")
        return _inapoi(e)
    c = get_object_or_404(m.Contract, pk=cid, proiect=e.proiect)
    try:
        pret = _pret(request.POST.get("pret"))
    except ValueError:
        messages.error(request, f"„{e.denumire}”: prețul unitar nu este un număr valid.")
        return _inapoi(e)
    if pret is None:
        messages.error(request, f"„{e.denumire}”: completați prețul unitar din contract ({c.moneda}).")
        return _inapoi(e)
    e.contract, e.valoare_unitara, e.moneda = c, pret, c.moneda
    e.curs = c.curs if c.moneda == "EUR" else None
    if e.status == "planificat":
        e.status = "contractat"
    e.save()
    messages.success(request, f"„{e.denumire}” a fost alocat pe contractul {c.numar} ({c.partener}).")
    return _inapoi(e)


@login_required
@require_POST
def echipament_status(request, pk):
    e = _echipament(request, pk)
    st = request.POST.get("status")
    if st in dict(m.Echipament.STATUS) and st != e.status:
        if st == "contractat" and not e.contract_id:
            messages.warning(request, f"„{e.denumire}”: alocați întâi echipamentul pe un contract.")
            return _inapoi(e)
        e.status = st
        e.save()  # cu contract alocat, „planificat” rămâne „contractat”
        messages.success(request, f"„{e.denumire}”: status {e.get_status_display().lower()}.")
    return _inapoi(e)
