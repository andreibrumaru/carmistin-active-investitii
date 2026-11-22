"""Utilitățile unui activ: energie, gaz, apă, stație de epurare, gunoi – date tehnice, consum (inclusiv curba lunară), documente."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import models as m
from .permisiuni import e_admin

LUNI = ["Ian", "Feb", "Mar", "Apr", "Mai", "Iun", "Iul", "Aug", "Sep", "Oct", "Noi", "Dec"]
CATEGORII_DOC = ["ut_racordare", "ut_bransament", "ut_contract", "ut_consum", "ut_contor", "ut_alt"]


def _numar(v):
    t = str(v or "").strip().replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _fmt(v):
    if v is None or v == "":
        return ""
    d = Decimal(str(v))
    s = f"{d:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return s[:-3] if s.endswith(",00") else s


def context_utilitati(loc):
    existente = {u.tip: u for u in loc.utilitati.prefetch_related("documente")}
    rez = []
    for tip, et, ic, um in m.UtilitateLocatie.TIPURI:
        u = existente.get(tip)
        det = (u.detalii if u else {}) or {}
        campuri = [{"cheie": k, "eticheta": e, "um": unit, "valoare": det.get(k, "")} for k, e, unit in m.UtilitateLocatie.CAMPURI[tip]]
        ani = sorted((u.curba_consum or {}).keys(), reverse=True) if u else []
        curba = []
        for an in ani:
            vals = (u.curba_consum.get(an) or [None] * 12) + [None] * 12
            vals = vals[:12]
            nums = [float(x) for x in vals if x not in (None, "")]
            mx = max(nums) if nums else 0
            curba.append({"an": an, "luni": [{"luna": LUNI[i], "v": vals[i], "fmt": _fmt(vals[i]) if vals[i] not in (None, "") else "",
                                               "h": round(float(vals[i]) / mx * 100) if vals[i] not in (None, "") and mx else 0} for i in range(12)],
                          "total": _fmt(sum(nums)) if nums else "", "n": len(nums)})
        docs = [d for d in (u.documente.all() if u else []) if not d.arhivat]
        sumar = []
        if u:
            if tip == "energie" and det.get("pv_putere"):
                sumar.append(f"PV {det['pv_putere']} kWp")
            if tip == "energie" and det.get("baterii_capacitate"):
                sumar.append(f"baterii {det['baterii_capacitate']} kWh")
            if u.consum_anual:
                sumar.append(f"{_fmt(u.consum_anual)} {um}/an")
        rez.append({"tip": tip, "eticheta": et, "iconita": ic, "um": um, "u": u, "campuri": campuri, "curba": curba,
                    "docs": docs, "sumar": " · ".join(sumar), "completata": bool(u and (u.furnizor or u.titular or det or u.consum_anual or docs))})
    return {"utilitati": rez, "utilitati_completate": sum(1 for x in rez if x["completata"]), "luni": LUNI,
            "categorii_doc_utilitati": [(k, v) for k, v in m.Document.CATEGORII if k in CATEGORII_DOC],
            "stari_utilitate": m.UtilitateLocatie.STARE, "an_curent": timezone.localdate().year}


def _verifica(request, pk, tip):
    if not e_admin(request.user):
        raise PermissionDenied("Datele activelor le completează administratorul.")
    if tip not in dict((c, 1) for c, _, _, _ in m.UtilitateLocatie.TIPURI):
        raise PermissionDenied
    loc = get_object_or_404(m.Locatie, pk=pk)
    u, _ = m.UtilitateLocatie.objects.get_or_create(locatie=loc, tip=tip)
    return loc, u


def _inapoi(loc, tip):
    return redirect(f"{loc.get_absolute_url()}?tab=utilitati&u={tip}")


@login_required
@require_POST
def utilitate_salveaza(request, pk, tip):
    loc, u = _verifica(request, pk, tip)
    g = request.POST
    if g.get("stare") in dict(m.UtilitateLocatie.STARE):
        u.stare = g["stare"]
    for camp, lung in (("furnizor", 200), ("titular", 200), ("cod_loc_consum", 80), ("nr_contract", 120)):
        setattr(u, camp, g.get(camp, "").strip()[:lung])
    u.observatii = g.get("observatii", "").strip()
    u.detalii = {k: g.get(f"d_{k}", "").strip()[:200] for k, _, _ in m.UtilitateLocatie.CAMPURI[tip] if g.get(f"d_{k}", "").strip()}
    # curba de consum: câte un rând pe an (12 luni); rândul nou are câmpurile c_nou_*
    curba = {}
    randuri = [(an, f"c_{an}_") for an in g.getlist("an")]
    an_nou = (g.get("an_nou") or "").strip()
    if an_nou:
        randuri.append((an_nou, "c_nou_"))
    for an, prefix in randuri:
        if not (an.isdigit() and 2000 <= int(an) <= 2100):
            continue
        vals = [_numar(g.get(f"{prefix}{i}")) for i in range(12)]
        if any(v is not None for v in vals):
            curba[an] = [float(v) if v is not None else None for v in vals]
    u.curba_consum = curba
    u.consum_anual, u.consum_lunar = _numar(g.get("consum_anual")), _numar(g.get("consum_lunar"))
    calculat = False
    if curba and (u.consum_anual is None or u.consum_lunar is None):  # din cel mai recent an al curbei
        vals = [v for v in curba[max(curba)] if v is not None]
        medie = Decimal(str(sum(vals) / len(vals))).quantize(Decimal("0.01"))
        if u.consum_lunar is None:
            u.consum_lunar = medie
        if u.consum_anual is None:
            u.consum_anual = (medie * 12).quantize(Decimal("0.01"))
        calculat = True
    u.save()
    messages.success(request, f"{u.get_tip_display()}: datele au fost salvate."
                     + (" Consumul mediu a fost calculat din curba de consum." if calculat else ""))
    return _inapoi(loc, tip)


@login_required
@require_POST
def utilitate_documente(request, pk, tip):
    loc, u = _verifica(request, pk, tip)
    cat = request.POST.get("categorie") if request.POST.get("categorie") in CATEGORII_DOC else "ut_alt"
    fisiere = request.FILES.getlist("fisiere")[:20]
    titlu = request.POST.get("titlu", "").strip()
    for x in fisiere:
        nume = x.name.rsplit(".", 1)[0].replace("_", " ")
        m.Document.objects.create(locatie=loc, utilitate=u, categorie=cat, fisier=x, incarcat_de=request.user,
                                  titlu=((titlu if titlu and len(fisiere) == 1 else f"{u.get_tip_display()} – {nume}"))[:255])
    if fisiere:
        messages.success(request, f"{len(fisiere)} document{'e' if len(fisiere) != 1 else ''} atașat{'e' if len(fisiere) != 1 else ''} la {u.get_tip_display().lower()}.")
    return _inapoi(loc, tip)
