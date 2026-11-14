"""Finanțarea proiectului: tabloul de bord Fonduri UE (buget eligibil / neeligibil / cumulat), cererile de tragere din
creditul bancar (cu execuția liniei de credit) și încărcarea Certificatului de urbanism cu citire AI."""
import io
from decimal import Decimal

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ai, models as m
from .hg import CAPITOLE, capitol_din
from .permisiuni import e_admin, poate_edita, poate_edita_financiar
from .views import _numar_ro, _proiect_editabil, _proiect_vizibil

ZERO = Decimal(0)


# ------------------------------------------------------------------ Fonduri UE
@login_required
def fonduri_ue(request, pk):
    """Tabloul de bord al proiectelor cu fonduri UE: bugetul pe linii HG împărțit în eligibil / neeligibil / cumulat,
    ajutorul nerambursabil, contribuția proprie și execuția (facturat) pe cele trei vederi."""
    p = _proiect_vizibil(request, pk)
    editabil = poate_edita(request.user, p) and (not p.buget_blocat or e_admin(request.user))
    linii = list(p.linii_buget.prefetch_related("rectificari", "alocari"))
    if request.method == "POST":
        if not editabil:
            raise PermissionDenied("Nu aveți drept să încadrați bugetul (după blocare: doar administratorul).")
        erori = []
        with transaction.atomic():
            for l in linii:
                val = request.POST.get(f"eligibil_{l.pk}", "").strip()
                try:
                    v = _numar_ro(val) if val else None
                except ValueError:
                    erori.append(f"{l.capitol}: „{val}”")
                    continue
                if v is not None and (v < 0 or v > l.valoare_curenta):
                    erori.append(f"{l.capitol}: eligibilul trebuie să fie între 0 și {l.valoare_curenta:,.2f}".replace(",", " "))
                    continue
                if v != l.valoare_eligibila:
                    l.valoare_eligibila = v
                    l.save(update_fields=["valoare_eligibila"])
        if erori:
            messages.error(request, "Valori nesalvate: " + "; ".join(erori))
        else:
            messages.success(request, "Încadrarea bugetului pe eligibil / neeligibil a fost salvată.")
        return redirect("fonduri_ue", pk=p.pk)

    randuri, capitole = [], []
    tot = {k: ZERO for k in ("cumulat", "eligibil", "neeligibil", "facturat", "fact_eligibil", "fact_neeligibil")}
    for cod, den in CAPITOLE.items():
        sub = [l for l in linii if capitol_din(l.capitol) == cod]
        if not sub:
            continue
        cap = {"cod": cod, "denumire": den, "linii": [], **{k: ZERO for k in tot}}
        for l in sub:
            fac = l.facturat
            r = {"l": l, "cumulat": l.valoare_curenta, "eligibil": l.eligibil, "neeligibil": l.neeligibil, "facturat": fac,
                 "fact_eligibil": (fac * l.cota_eligibila).quantize(Decimal("0.01")), "neincadrat": l.valoare_eligibila is None and l.valoare_curenta > 0}
            r["fact_neeligibil"] = fac - r["fact_eligibil"]
            cap["linii"].append(r)
            for k in tot:
                cap[k] += r[k]
                tot[k] += r[k]
        capitole.append(cap)
    ajutor = p._rz_in_ron(p.val_nerambursabil) or suma_finantari(p, "nerambursabila")
    intensitate = round(float(ajutor) / float(tot["eligibil"]) * 100, 1) if ajutor and tot["eligibil"] else None
    ajutor_executie = (tot["fact_eligibil"] * Decimal(intensitate) / 100).quantize(Decimal("0.01")) if intensitate else ZERO
    return render(request, "platforma/fonduri_ue.html", {
        "p": p, "capitole": capitole, "tot": tot, "editabil": editabil, "vedere": request.GET.get("vedere", "cumulat"),
        "ajutor": ajutor, "intensitate": intensitate, "contributie_proprie": tot["cumulat"] - (ajutor or ZERO),
        "ajutor_executie": ajutor_executie, "neincadrate": sum(1 for c in capitole for r in c["linii"] if r["neincadrat"]),
        "structura": p.structura_finantare,
    })


def suma_finantari(p, tip):
    return sum((f.valoare_ron for f in p.finantari.filter(tip=tip)), ZERO)


# ------------------------------------------------------------------ Cereri de tragere (credit bancar)
def _facturi_eligibile_tragere(p, cu_tva, exclude_cerere=None):
    """Facturile proiectului care mai au sumă nesolicitată la tragere: (factura, deja_solicitat, rest)."""
    rez = []
    for x in p.facturi.select_related("contract").prefetch_related("trageri__cerere", "plati").order_by("data", "pk"):
        deja = sum((t.suma_ron for t in x.trageri.all() if t.cerere.status != "respinsa" and t.cerere_id != getattr(exclude_cerere, "pk", None)), ZERO)
        rest = x.baza_tragere(cu_tva) - deja
        if rest > 0:
            rez.append((x, deja, rest))
    return rez


def _numar_cerere(p):
    n = p.cereri_tragere.count() + 1
    while p.cereri_tragere.filter(numar=f"{p.cod}/CT-{n:02d}").exists():
        n += 1
    return f"{p.cod}/CT-{n:02d}"


@login_required
def cerere_tragere_noua(request, pk):
    p = _proiect_vizibil(request, pk)
    if not poate_edita_financiar(request.user, p):
        raise PermissionDenied
    cu_tva = request.POST.get("include_tva", request.GET.get("tva")) in ("1", "on", "true")
    facturi = _facturi_eligibile_tragere(p, cu_tva)
    credite = list(p.finantari.filter(tip="bancara"))
    if request.method == "POST":
        alese = {int(i) for i in request.POST.getlist("factura") if i.isdigit()}
        pozitii, erori = [], []
        for x, deja, rest in facturi:
            if x.pk not in alese:
                continue
            val = request.POST.get(f"suma_{x.pk}", "").strip()
            try:
                s = _numar_ro(val) if val else rest
            except ValueError:
                erori.append(f"factura {x.numar}: sumă invalidă")
                continue
            if s <= 0 or s > rest + Decimal("0.01"):
                erori.append(f"factura {x.numar}: suma trebuie să fie între 0 și {rest:,.2f}".replace(",", " "))
                continue
            pozitii.append((x, s))
        total = sum((s for _, s in pozitii), ZERO)
        if not pozitii:
            erori.append("bifați cel puțin o factură")
        elif total > p.credit_disponibil_ron + Decimal("0.01"):
            erori.append(f"totalul solicitat ({total:,.2f} RON) depășește disponibilul din linia de credit "
                         f"({p.credit_disponibil_ron:,.2f} RON)".replace(",", " "))
        if erori:
            messages.error(request, "Cererea nu a fost emisă: " + "; ".join(erori) + ".")
        else:
            with transaction.atomic():
                c = m.CerereTragere.objects.create(
                    proiect=p, numar=_numar_cerere(p), include_tva=cu_tva, creat_de=request.user,
                    finantare=next((f for f in credite if str(f.pk) == request.POST.get("finantare")), credite[0] if credite else None),
                    observatii=request.POST.get("observatii", "")[:2000])
                for x, s in pozitii:
                    m.CerereTragereFactura.objects.create(cerere=c, factura=x, suma_ron=s)
            messages.success(request, f"{c} a fost emisă: {len(pozitii)} facturi, {total:,.2f} RON.".replace(",", " "))
            return redirect("cerere_tragere", pk=p.pk, cpk=c.pk)
    return render(request, "platforma/cerere_tragere_noua.html", {
        "p": p, "facturi": facturi, "cu_tva": cu_tva, "credite": credite,
        "limita": p.limita_credit_ron, "tras": p.credit_tras_ron, "in_curs": p.credit_in_curs_ron, "disponibil": p.credit_disponibil_ron,
        "alese": set(request.POST.getlist("factura")),
    })


@login_required
def cerere_tragere(request, pk, cpk):
    p = _proiect_vizibil(request, pk)
    c = get_object_or_404(m.CerereTragere.objects.select_related("finantare", "creat_de"), pk=cpk, proiect=p)
    poz = list(c.pozitii.select_related("factura__contract").order_by("factura__data", "factura__pk"))
    if request.GET.get("format") == "xlsx":
        return _cerere_excel(p, c, poz)
    if request.method == "POST":
        if not poate_edita_financiar(request.user, p):
            raise PermissionDenied
        act = request.POST.get("actiune")
        if act == "transmite" and c.status == "ciorna":
            c.status = "transmisa"
            c.save()
            messages.success(request, "Cererea a fost marcată ca transmisă băncii.")
        elif act == "aproba" and c.status in ("transmisa", "ciorna"):
            val = request.POST.get("suma_aprobata", "").strip()
            try:
                c.suma_aprobata = _numar_ro(val) if val else c.total_solicitat
            except ValueError:
                messages.error(request, "Suma trasă este invalidă.")
                return redirect("cerere_tragere", pk=p.pk, cpk=c.pk)
            c.data_tragere = _data(request.POST.get("data_tragere")) or timezone.localdate()
            c.status = "aprobata"
            c.save()
            messages.success(request, f"Tragere înregistrată: {c.suma_aprobata:,.2f} RON la {c.data_tragere:%d.%m.%Y}.".replace(",", " "))
        elif act == "respinge" and c.status in ("transmisa", "ciorna"):
            c.status = "respinsa"
            c.observatii = (c.observatii + "\nRespinsă: " + request.POST.get("motiv", "")).strip()
            c.save()
            messages.warning(request, "Cererea a fost marcată respinsă; facturile ei pot fi solicitate din nou.")
        elif act == "sterge" and c.status == "ciorna":
            numar = str(c)
            with transaction.atomic():
                c.pozitii.all().delete()
                c.delete()
            messages.success(request, f"{numar} (ciornă) a fost ștearsă.")
            return redirect(f"{p.get_absolute_url()}?tab=finantare#linie-credit")
        else:
            messages.error(request, "Operația nu este permisă în starea actuală a cererii.")
        return redirect("cerere_tragere", pk=p.pk, cpk=c.pk)
    return render(request, "platforma/cerere_tragere.html", {
        "p": p, "c": c, "pozitii": poz, "editabil_fin": poate_edita_financiar(request.user, p), "azi": timezone.localdate(),
        "limita": p.limita_credit_ron, "tras": p.credit_tras_ron, "disponibil": p.credit_disponibil_ron,
        "documente": c.documente.filter(arhivat=False),
    })


def _data(s):
    import datetime
    try:
        return datetime.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _cerere_excel(p, c, poz):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cerere de tragere"
    ws.append([f"Cerere de tragere nr. {c.numar} din {c.data:%d.%m.%Y}"])
    ws.append([f"Beneficiar: {p.beneficiar}", f"Proiect: {p.cod} – {p.denumire}"])
    if c.finantare:
        ws.append([f"Contract de credit: {c.finantare.nr_contract or ''} {c.finantare.finantator or ''}".strip()])
    ws.append([])
    antet = ["Nr.", "Furnizor", "CUI", "Factură", "Data", "Scadență", "Contract", "Valoare fără TVA (RON)", "TVA (RON)",
             "Plătit (RON)", "Solicitat la tragere (RON)"]
    ws.append(antet)
    for i, t in enumerate(poz, 1):
        x = t.factura
        ws.append([i, x.furnizor, x.cui_furnizor, x.numar, x.data, x.scadenta, x.contract.numar if x.contract else "",
                   float(x.valoare_ron), float(x.tva_ron), float(x.platit_ron), float(t.suma_ron)])
    ws.append([])
    ws.append(["", "", "", "", "", "", "", "", "", "TOTAL SOLICITAT", float(c.total_solicitat)])
    for col, lat in zip("ABCDEFGHIJK", (5, 34, 12, 14, 12, 12, 14, 18, 14, 14, 20)):
        ws.column_dimensions[col].width = lat
    buf = io.BytesIO()
    wb.save(buf)
    r = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = f'attachment; filename="cerere_tragere_{c.numar.replace("/", "_")}.xlsx"'
    return r


# ------------------------------------------------------------------ CU cu citire AI
@login_required
@require_POST
def cu_incarca(request, pk):
    """Încarcă Certificatul de urbanism și pornește citirea AI a avizelor cerute (cele bifate în CU).
    Rezultatul se validează pe pagina de validare, unde se pot adăuga și avize / studii intermediare sau suplimentare."""
    p = _proiect_editabil(request, pk)
    fisier = request.FILES.get("fisier")
    if not fisier:
        messages.error(request, "Alegeți fișierul certificatului de urbanism (PDF sau imagine).")
        return redirect(f"{p.get_absolute_url()}?tab=avize")
    d = m.Document.objects.create(proiect=p, categorie="cu", titlu=f"Certificat de urbanism – {fisier.name}"[:255],
                                  fisier=fisier, incarcat_de=request.user)
    ex = ai.ruleaza_extragere(d, "cu_avize")
    if ex.status == "eroare":
        messages.error(request, f"CU a fost salvat, dar citirea AI nu a reușit: {ex.mesaj}. Introduceți avizele manual („+ Aviz / studiu”).")
        return redirect(f"{p.get_absolute_url()}?tab=avize")
    return redirect("ai_validare", pk=ex.pk)
