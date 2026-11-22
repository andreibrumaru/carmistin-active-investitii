"""Sub-ecranul „Contracte și facturi” din tab-ul „Buget și contractare”:
lista contractelor în stânga, desfășurătorul facturilor contractului ales în dreapta.
O factură repartizată pe mai multe linii de buget apare o singură dată (rândul „total factură”),
cu câte un sub-rând pe fiecare linie; suma achitată se împarte proporțional cu repartizarea."""
from decimal import Decimal
from urllib.parse import urlencode

from . import models as m

ZERO = Decimal(0)
LIVRAT = ("livrat", "montat", "receptionat")
SUB_ECRANE = {"buget": "buget", "contracte": "contracte", "echipamente": "echipamente",
              "financiar": "plati", "plati": "plati", "facturi": "plati"}
CULOARE_TIP = {"executie": "lucrari", "furnizare": "furnizare", "proiectare": "servicii", "servicii": "servicii",
               "dirigentie": "servicii", "altul": "altul"}


def _randuri_factura(x):
    """Rândurile afișate pentru o factură: linia (dacă e una singură) sau sub-rândurile pe linii de buget."""
    alocari = list(x.alocari.all())
    platit = x.platit_ron
    sub = []
    for a in alocari:
        cota = (a.valoare_ron / x.valoare_ron) if x.valoare_ron else ZERO
        sub.append({"linie": a.linie_buget, "valoare": a.valoare_ron, "platit": (platit * cota).quantize(Decimal("0.01")), "alocare": a})
    nealocat = x.valoare_ron - sum((a.valoare_ron for a in alocari), ZERO)
    return {"f": x, "sub": sub, "impartita": len(sub) > 1 or (sub and nealocat != 0),
            "linie": sub[0]["linie"] if len(sub) == 1 and not nealocat else None, "nealocat": nealocat,
            "platit": platit, "rest": x.valoare_ron - platit}


def _sumar(randuri):
    facturat = sum((r["f"].valoare_ron for r in randuri), ZERO)
    platit = sum((r["platit"] for r in randuri), ZERO)
    return {"facturat": facturat, "platit": platit, "rest_plata": facturat - platit,
            "scadente_depasite": sum(1 for r in randuri if r["f"].scadenta_depasita)}


def context_contracte(request, p, facturi, echipamente):
    pe_contract = {}
    for x in sorted(facturi, key=lambda x: (x.data, x.pk)):
        pe_contract.setdefault(x.contract_id, []).append(_randuri_factura(x))
    ech_contract = {}
    for e in echipamente:
        if e.contract_id:
            ech_contract.setdefault(e.contract_id, []).append(e)

    contracte = list(p.contracte.prefetch_related("acte_aditionale", "conditii", "documente", "alocari__linie_buget"))
    for c in contracte:
        c.randuri = pe_contract.get(c.pk, [])
        c.sumar = _sumar(c.randuri)
        c.valoare_act = c.valoare_ron + sum((a.diferenta_ron for a in c.acte_aditionale.all()), ZERO)
        c.pct_real = round(float(c.sumar["facturat"]) / float(c.valoare_act) * 100) if c.valoare_act else 0
        c.pct_facturat = min(100, c.pct_real)
        c.rest_facturat = c.valoare_act - c.sumar["facturat"]
        c.scan = next((d for d in c.documente.all() if d.categorie == "contract" and not d.arhivat), None)
        c.lista_ech = ech_contract.get(c.pk, [])
        c.total_ech = sum((e.valoare_ron for e in c.lista_ech), ZERO)
        c.culoare = CULOARE_TIP.get(c.tip, "altul")
        alocat = sum((a.valoare_ron for a in c.alocari.all()), ZERO)
        c.nealocat_buget = c.valoare_ron - alocat
        c.recurenta = c.calendar_recurent([r["f"] for r in c.randuri])
        if c.recurenta and c.recurenta["urmatoarea"]:  # „+ Factură” precompletată cu fee-ul perioadei următoare
            u = c.recurenta["urmatoarea"]
            c.recurenta["link_factura"] = urlencode({
                "contract": c.pk, "furnizor": c.partener, "cui_furnizor": c.cui_partener, "moneda": c.moneda,
                "valoare": f"{c.valoare_perioada:.2f}", "curs": c.curs or "",
                "observatii": f"Fee {c.get_periodicitate_display()} {u['nr']}/{c.nr_perioade} ({u['inceput']:%d.%m.%Y} – {u['sfarsit']:%d.%m.%Y})"})
    # echipamentele: culoarea rândului (roșu necontractat · albastru contractat · verde livrat) și contractul cu scanul încărcat
    dupa_pk = {c.pk: c for c in contracte}
    stat = {"rosu": 0, "albastru": 0, "verde": 0}
    for e in echipamente:
        if e.contract_id in dupa_pk:
            e.contract = dupa_pk[e.contract_id]
        e.culoare = "verde" if e.status in LIVRAT else ("albastru" if e.contract_id else "rosu")
        stat[e.culoare] += 1
        e.estimat_unitar = f"{(e.valoare_estimata / e.cantitate):.2f}" if e.cantitate else ""
    fara = pe_contract.get(None, [])
    pe_tip = {}
    for r in fara:  # totaluri pe tipul cheltuielii: avize, taxe, autorizații…
        t = pe_tip.setdefault(r["f"].tip_cheltuiala, {"cod": r["f"].tip_cheltuiala or "nespecificat",
                                                      "eticheta": r["f"].get_tip_cheltuiala_display() or "nespecificat", "n": 0, "total": ZERO})
        t["n"] += 1
        t["total"] += r["f"].valoare_ron
    sel = request.GET.get("c", "")
    ids = {str(c.pk) for c in contracte}
    if sel not in ids and sel != "fara":
        sel = str(contracte[0].pk) if contracte else "fara"
    return {"contracte": contracte, "contract_activ": sel, "ech_stat": stat, "statusuri_echipament": m.Echipament.STATUS,
            "fara_contract": {"randuri": fara, "sumar": _sumar(fara), "pe_tip": sorted(pe_tip.values(), key=lambda t: -t["total"])},
            "tipuri_fara_contract": m.Factura.TIP_FARA_CONTRACT}


def sub_ecran(tab_cerut):
    """?tab=contracte / echipamente / financiar … → tab-ul comun „buget” + sub-ecranul corespunzător."""
    return SUB_ECRANE.get(tab_cerut)
