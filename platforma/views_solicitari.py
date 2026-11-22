"""Solicitări de plată: generarea din tab-ul Plăți, aprobarea din aplicație și din email, anulare, retrimitere email."""
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import models as m
from . import solicitari as sol
from .permisiuni import e_admin, poate_edita_financiar


def _suma(text):
    t = str(text or "").strip().replace(" ", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _la_plati(p):
    return redirect(f"{p.get_absolute_url()}?tab=plati")


@login_required
@require_POST
def solicitare_creeaza(request, pk):
    p = get_object_or_404(m.Proiect, pk=pk)
    if not poate_edita_financiar(request.user, p):
        raise PermissionDenied("Nu aveți drept să propuneți plăți pe acest proiect.")
    g = request.POST
    sume = {}
    for fid in g.getlist("factura"):
        if fid.isdigit():
            sume[int(fid)] = _suma(g.get(f"suma_{fid}"))
    try:
        data = datetime.date.fromisoformat(g.get("data_plata") or "")
    except ValueError:
        data = timezone.localdate()
    try:
        s = sol.creeaza(request.user, p, sume, data, g.get("observatii", "").strip())
    except ValueError as e:
        messages.error(request, f"Solicitarea nu a fost generată: {e}")
        return _la_plati(p)
    ok, mesaj = sol.trimite(s, request.user)
    n = s.plati.count()
    (messages.success if ok else messages.warning)(request, f"Solicitarea {s.numar} ({n} factur{'ă' if n == 1 else 'i'}). {mesaj}")
    return _la_plati(p)


def _inapoi(request, s):
    dest = request.POST.get("inapoi", "")
    if dest and url_has_allowed_host_and_scheme(dest, allowed_hosts={request.get_host()}):
        return redirect(dest)
    return _la_plati(s.proiect)


@login_required
@require_POST
def solicitare_decizie(request, spk):
    s = get_object_or_404(m.SolicitarePlata.objects.select_related("proiect"), pk=spk)
    actiune = request.POST.get("actiune")
    try:
        if actiune == "anuleaza":
            sol.anuleaza(request.user, s)
            messages.success(request, f"Solicitarea {s.numar} a fost anulată; facturile au revenit în lista de plată.")
        elif actiune == "retrimite":
            if s.stare == "aprobata" and e_admin(request.user):  # centralizarea către trezorerie
                ok, mesaj = sol.trimite_trezorerie(s, request.user)
            elif s.stare == "trimisa" and (request.user.pk == s.creata_de_id or e_admin(request.user)):
                ok, mesaj = sol.trimite(s, request.user)
            else:
                raise PermissionDenied
            (messages.success if ok else messages.warning)(request, mesaj)
        else:
            sol.decide(request.user, s, actiune, request.POST.get("motiv", ""))
            if s.stare == "aprobata":
                ok, mesaj = sol.trimite_trezorerie(s, request.user)
                (messages.success if ok else messages.warning)(request, f"Solicitarea {s.numar} a fost aprobată. {mesaj}")
            else:
                messages.success(request, f"Solicitarea {s.numar} a fost respinsă; facturile au revenit în lista de plată.")
    except ValueError as e:
        messages.error(request, str(e))
    return _inapoi(request, s)


def solicitare_din_email(request, token):
    """Pagina deschisă din email: decizia se dă prin butonul din pagină (POST), nu prin simpla deschidere a linkului."""
    s, rol, eroare = sol.citeste_token(token)
    ctx = {"s": s, "rol": rol, "eroare": eroare, "actiune": request.GET.get("actiune", "aproba")}
    if s and not eroare:
        apr = s.aprobator_suplimentar if rol == "suplimentar" else s.aprobator
        ctx["aprobator"] = apr
        if not apr or not apr.is_active:
            ctx["eroare"] = "Aprobatorul nu are cont activ în aplicație."
        elif request.method == "POST":
            actiune, motiv = request.POST.get("actiune"), request.POST.get("motiv", "").strip()
            if actiune == "respinge" and not motiv:
                ctx["actiune"], ctx["lipsa_motiv"] = "respinge", True
            else:
                from . import audit
                audit._local.user = apr  # jurnalul de audit înregistrează aprobatorul
                try:
                    with transaction.atomic():
                        sol.decide(apr, s, actiune, motiv, rol=rol)
                    ctx["gata"] = actiune
                    if s.stare == "aprobata":
                        ctx["trezorerie_ok"], ctx["trezorerie_mesaj"] = sol.trimite_trezorerie(s, apr)
                except (ValueError, PermissionDenied) as e:
                    ctx["eroare"] = str(e) or "Acțiune nepermisă."
    if s:
        r = sol.randuri_email(s)
        ctx.update({"randuri": r, "total": sum((x["pl"].suma_ron for x in r), Decimal(0)),
                    "total_tva": sum((x["tva"] for x in r), Decimal(0)), "total_plata": sum((x["total"] for x in r), Decimal(0))})
    return render(request, "platforma/solicitare_email.html", ctx)


def context_plati(request, p, user):
    """Datele sub-ecranului „Plăți”: facturile cu rest de plată, solicitările și istoricul plăților."""
    solicitari = list(p.solicitari_plata.select_related("creata_de", "aprobator", "aprobator_suplimentar", "aprobat_de", "respinsa_de")
                      .prefetch_related("plati__factura"))
    for s in solicitari:
        s.roluri = sol.roluri_utilizator(user, s)
        s.poate_anula = s.stare == "trimisa" and (user.pk == s.creata_de_id or e_admin(user))
    return {"facturi_de_plata": sol.facturi_de_plata(p), "solicitari": solicitari,
            "azi_iso": timezone.localdate().isoformat()}
