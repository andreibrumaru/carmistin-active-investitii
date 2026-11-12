"""Fluxul de aprobare a plăților și exportul pentru Charisma.

Circuit: propusă (PM) → aprobată tehnic (coordonator / aprobator tehnic)
         → aprobată financiar (+ aprobare suplimentară peste prag) → exportată în lot → plătită (confirmare financiar).
Fluxul de date este doar aplicație → ERP: aplicația generează fișierul, Charisma îl importă.
"""
import io
from decimal import Decimal

import openpyxl
from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from . import models as m
from .permisiuni import (e_financiar, poate_aproba_financiar, poate_aproba_suplimentar, poate_aproba_tehnic)


def nume(u):
    return (u.get_full_name() or u.username) if u else ""


def actiuni_posibile(user, plata):
    p = plata.factura.proiect
    a = []
    if plata.stare == "propusa" and poate_aproba_tehnic(user, p):
        a.append(("aproba_tehnic", "Aprobă tehnic"))
    if plata.stare == "aprobata_tehnic":
        if poate_aproba_financiar(user, p) and not plata.aprobat_financiar_de_id:
            a.append(("aproba_financiar", "Aprobă financiar"))
        if plata.necesita_aprobare_suplimentara and not plata.aprobat_suplimentar_de_id and poate_aproba_suplimentar(user, p):
            a.append(("aproba_suplimentar", "Aprobare suplimentară"))
    if plata.stare in ("propusa", "aprobata_tehnic", "aprobata") and (
            poate_aproba_tehnic(user, p) or poate_aproba_financiar(user, p)):
        a.append(("respinge", "Respinge"))
    if plata.stare == "exportata" and e_financiar(user):
        a.append(("confirma", "Confirmă plătită"))
    return a


@transaction.atomic
def aplica(user, plata, actiune, motiv="", document_plata=""):
    if actiune not in dict(actiuni_posibile(user, plata)):
        raise PermissionDenied("Acțiune nepermisă pentru această plată.")
    acum = timezone.now()
    if actiune == "aproba_tehnic":
        plata.stare, plata.aprobat_tehnic_de, plata.aprobat_tehnic_la = "aprobata_tehnic", user, acum
    elif actiune == "aproba_financiar":
        plata.aprobat_financiar_de, plata.aprobat_financiar_la = user, acum
    elif actiune == "aproba_suplimentar":
        plata.aprobat_suplimentar_de, plata.aprobat_suplimentar_la = user, acum
    elif actiune == "respinge":
        plata.stare, plata.respinsa_motiv = "respinsa", motiv[:300]
    elif actiune == "confirma":
        plata.stare = "platita"
        if document_plata:
            plata.document_plata = document_plata
    if plata.stare == "aprobata_tehnic" and plata.aprobat_financiar_de_id and (
            not plata.necesita_aprobare_suplimentara or plata.aprobat_suplimentar_de_id):
        plata.stare = "aprobata"
    plata.save()


def plati_de_aprobat(user):
    qs = m.Plata.objects.filter(stare__in=["propusa", "aprobata_tehnic"]).select_related("factura__proiect__beneficiar")
    return [pl for pl in qs if any(a != "respinge" for a, _ in actiuni_posibile(user, pl))]


# ----------------------------------------------------------------- export
def _numar_lot(tip):
    prefix = "FAC" if tip == "facturi" else "PLT"
    baza = f"{prefix}-{timezone.localdate():%Y%m%d}-"
    n = m.LotExport.objects.filter(numar__startswith=baza).count() + 1
    return f"{baza}{n:03d}"


def _xlsx(antet, randuri):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(antet)
    for r in randuri:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in r])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(40, max(10, max(len(str(c.value or "")) for c in col) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ANTET_FACTURI = [
    "Lot", "Societate", "CUI societate", "Cod Charisma societate", "Centru cost", "Cod proiect", "Cont", "Furnizor", "CUI furnizor",
    "Intragrup", "Nr. factură", "Data factură", "Scadență", "Monedă", "Valoare fără TVA (valută)", "TVA (valută)", "Curs",
    "Linie buget (cap. HG)", "Valoare alocată fără TVA (RON)", "TVA alocat (RON)", "Total alocat (RON)", "Contract",
]


@transaction.atomic
def export_facturi(user, facturi):
    facturi = list(facturi)
    erori = [f"{f} – nealocat {f.nealocat_ron} RON" for f in facturi if f.nealocat_ron != 0]
    erori += [f"{f} – deja exportată în {f.lot_export}" for f in facturi if f.lot_export_id]
    if erori:
        raise ValueError("Facturi care nu pot fi exportate: " + "; ".join(erori))
    lot = m.LotExport(tip="facturi", numar=_numar_lot("facturi"), creat_de=user)
    randuri, total = [], Decimal(0)
    for f in facturi:
        p, soc = f.proiect, f.proiect.beneficiar
        tva_ron = (f.tva or 0) * (f.curs or 1) if f.moneda == "EUR" else (f.tva or 0)
        for a in f.alocari.select_related("linie_buget"):
            pondere = a.valoare_ron / f.valoare_ron if f.valoare_ron else 0
            tva_a = (tva_ron * pondere).quantize(Decimal("0.01"))
            randuri.append([
                lot.numar, soc.denumire, soc.cui, soc.cod_erp, p.centru_cost, p.cod, p.cont_contabil, f.furnizor, f.cui_furnizor,
                "DA" if f.intragrup else "NU", f.numar, f.data, f.scadenta, f.moneda, f.valoare, f.tva, f.curs if f.moneda == "EUR" else 1,
                a.linie_buget.capitol, a.valoare_ron, tva_a, a.valoare_ron + tva_a, f.contract.numar if f.contract_id else "",
            ])
        total += f.valoare_ron
    lot.nr_inregistrari, lot.total_ron = len(facturi), total
    lot.fisier.save(f"{lot.numar}.xlsx", ContentFile(_xlsx(ANTET_FACTURI, randuri)), save=False)
    lot.save()
    for f in facturi:  # salvare individuală, ca exportul să apară în jurnalul de audit
        f.lot_export = lot
        f.save(update_fields=["lot_export"])
    return lot


ANTET_PLATI = [
    "Lot", "Societate plătitoare", "CUI societate", "Cod Charisma societate", "Centru cost", "Cod proiect", "Beneficiar plată",
    "CUI beneficiar", "Intragrup", "Factură", "Data factură", "Sumă fără TVA (RON)", "TVA aferent (RON)", "Total de plată (RON)",
    "Data plății", "Sursă fonduri", "Propusă de", "Aprobat tehnic", "Data aprobare tehnică", "Aprobat financiar",
    "Data aprobare financiară", "Aprobare suplimentară", "Observații",
]


@transaction.atomic
def export_plati(user, plati):
    plati = [pl for pl in plati if pl.stare == "aprobata"]
    if not plati:
        raise ValueError("Nu există plăți aprobate de exportat.")
    lot = m.LotExport(tip="plati", numar=_numar_lot("plati"), creat_de=user)
    randuri, total = [], Decimal(0)
    for pl in plati:
        f, p = pl.factura, pl.factura.proiect
        soc = p.beneficiar
        tva_ron = (f.tva or 0) * (f.curs or 1) if f.moneda == "EUR" else (f.tva or 0)
        tva_pl = (tva_ron * pl.suma_ron / f.valoare_ron).quantize(Decimal("0.01")) if f.valoare_ron else Decimal(0)
        randuri.append([
            lot.numar, soc.denumire, soc.cui, soc.cod_erp, p.centru_cost, p.cod, f.furnizor, f.cui_furnizor, "DA" if f.intragrup else "NU",
            f.numar, f.data, pl.suma_ron, tva_pl, pl.suma_ron + tva_pl, pl.data, pl.sursa_fonduri, nume(pl.propusa_de),
            nume(pl.aprobat_tehnic_de), pl.aprobat_tehnic_la and timezone.localtime(pl.aprobat_tehnic_la).strftime("%d.%m.%Y %H:%M"),
            nume(pl.aprobat_financiar_de), pl.aprobat_financiar_la and timezone.localtime(pl.aprobat_financiar_la).strftime("%d.%m.%Y %H:%M"),
            nume(pl.aprobat_suplimentar_de), pl.observatii,
        ])
        total += pl.suma_ron + tva_pl
    lot.nr_inregistrari, lot.total_ron = len(plati), total
    lot.fisier.save(f"{lot.numar}.xlsx", ContentFile(_xlsx(ANTET_PLATI, randuri)), save=False)
    lot.save()
    for pl in plati:
        pl.lot_export, pl.stare = lot, "exportata"
        pl.save(update_fields=["lot_export", "stare"])
    return lot
