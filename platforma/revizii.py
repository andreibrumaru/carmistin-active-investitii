"""Revizii ale Raportului Zero: ce s-a modificat față de ultima versiune avizată.

Fotografiile versiunilor avizate (RevizieRaportZero.date) păstrează câmpurile RZ ca text, bugetul pe linii (RON)
și lista de echipamente. Aici se compară proiectul curent cu ultima versiune avizată dinaintea reviziei curente.
"""
from decimal import Decimal, InvalidOperation

# câmp din formularul RZ -> cheia din fotografia reviziei
CAMPURI = {
    "denumire": "denumire", "linie": "linie", "divizie": "divizie", "tip_investitie": "tip_investitie",
    "necesita_ac": "tip_autorizare", "solicitant": "solicitant", "descriere_lucrari": "descriere_lucrari",
    "valoare_rz": "valoare_rz", "moneda_rz": "moneda_rz", "prioritate": "prioritate",
    "data_demarare": "data_demarare", "data_finalizare": "data_finalizare", "fara_termen": "fara_termen", "fonduri_ue": "fonduri_ue",
    "fin_nerambursabil": "fin_nerambursabil", "fin_bancara": "fin_bancara", "fin_proprii": "fin_proprii",
    "val_nerambursabil": "val_nerambursabil", "val_bancara": "val_bancara", "observatii_rz": "observatii_rz",
}
NUMERICE = {"valoare_rz", "val_nerambursabil", "val_bancara"}
BIFE = {"fara_termen", "fonduri_ue", "fin_nerambursabil", "fin_bancara", "fin_proprii", "necesita_ac"}


def _norm(camp, v):
    """Valoare comparabilă (text) – aceeași regulă și în JavaScript-ul formularului."""
    if camp == "necesita_ac":
        return "1" if v in ("cu_ac", True, "True", "1") else "0"
    if camp in BIFE:
        return "1" if v in (True, "True", "1", "on") else "0"
    if v is None:
        return ""
    if camp in NUMERICE:
        try:
            return f"{Decimal(str(v)):.2f}" if str(v).strip() else ""
        except InvalidOperation:
            return str(v)
    return " ".join(str(v).split())


def referinta(p):
    """Ultima versiune avizată dinaintea reviziei curente (None la Raportul Zero inițial)."""
    if not p or not p.pk or not p.revizie_rz:
        return None
    return p.revizii_rz.filter(revizie__lt=p.revizie_rz).order_by("-revizie").first()


def valori_referinta(ref):
    """{câmp formular: valoare normalizată} din versiunea avizată (doar câmpurile existente în fotografie)."""
    if not ref:
        return {}
    d = ref.date or {}
    rez = {c: _norm(c, d.get(cheie)) for c, cheie in CAMPURI.items() if cheie in d}
    if "pm_id" in d or "coordonator_id" in d:
        rez["responsabil"] = str(d.get("pm_id") or d.get("coordonator_id") or "")
    return rez


def valori_curente(p):
    rez = {c: _norm(c, getattr(p, cheie)) for c, cheie in CAMPURI.items()}
    rez["responsabil"] = str(p.pm_id or p.coordonator_id or "")
    return rez


def campuri_modificate(p, ref=None):
    ref = ref if ref is not None else referinta(p)
    if not ref:
        return set()
    vr, vc = valori_referinta(ref), valori_curente(p)
    return {c for c, v in vr.items() if vc.get(c, "") != v}


def coloane_buget(p, curs=None):
    """Coloana de buget a versiunii avizate anterioare (doar ea, nu tot istoricul): rev. N se compară cu rev. N-1.
    [{eticheta, valori{capitol}, total}] în moneda RZ."""
    ref = referinta(p)
    if not ref:
        return []
    k = Decimal(str(curs)) if p.moneda_rz == "EUR" and curs else Decimal(1)
    rez = []
    for r in [ref]:
        valori = {}
        for cap, v in ((r.date or {}).get("buget") or {}).items():
            try:
                valori[cap] = (Decimal(str(v)) / k).quantize(Decimal("0.01"))
            except InvalidOperation:
                pass
        rez.append({"revizie": r.revizie, "eticheta": "Inițial" if r.revizie == 0 else f"Rev. {r.revizie}",
                    "aprobat_la": r.aprobat_la, "valori": valori, "total": sum(valori.values(), Decimal(0))})
    return rez


def echipamente_referinta(ref):
    return list((ref.date or {}).get("echipamente") or []) if ref else []


def marcheaza_echipamente(randuri, ref, cheie_valoare_ron):
    """Pune pe fiecare rând „rev_stare” = nou / modificat / '' față de versiunea avizată; întoarce echipamentele eliminate."""
    vechi = echipamente_referinta(ref)
    if not ref:
        return []
    ramase = list(vechi)
    for r in randuri:
        den = (r["denumire"] if isinstance(r, dict) else r.denumire).strip().lower()
        gasit = next((v for v in ramase if (v.get("denumire") or "").strip().lower() == den), None)
        val = cheie_valoare_ron(r)
        stare = "nou"
        if gasit:
            ramase.remove(gasit)
            try:
                egal = abs(Decimal(str(gasit.get("valoare_estimata") or 0)) - Decimal(str(val or 0))) < Decimal("0.5")
            except InvalidOperation:
                egal = False
            linie = r["linie"] if isinstance(r, dict) else (r.linie_buget.capitol if r.linie_buget_id else None)
            stare = "" if egal and (gasit.get("linie") == linie) else "modificat"
        if isinstance(r, dict):
            r["rev_stare"] = stare
        else:
            r.rev_stare = stare
    return [v.get("denumire") for v in ramase]


ETICHETE = {
    "denumire": "Denumire proiect", "linie": "Linie de business", "divizie": "Divizie", "tip_investitie": "Tip investiție",
    "necesita_ac": "Necesită autorizație de construire", "solicitant": "Solicitant", "responsabil": "Responsabil proiect",
    "descriere_lucrari": "Descriere investiție", "valoare_rz": "Valoare Raport Zero", "moneda_rz": "Moneda RZ",
    "prioritate": "Prioritate", "data_demarare": "Data demarare", "data_finalizare": "Termen finalizare", "fara_termen": "Proiect FT (fără termen de finalizare)",
    "fonduri_ue": "Fonduri UE", "fin_nerambursabil": "Finanțare nerambursabilă", "fin_bancara": "Finanțare bancară",
    "fin_proprii": "Fonduri proprii", "val_nerambursabil": "Sumă nerambursabilă", "val_bancara": "Sumă finanțare bancară",
    "observatii_rz": "Observații, alte detalii",
}


def _afisare(camp, v, p):
    """Valoare normalizată -> text lizibil (pentru email și rezumat)."""
    from .models import Proiect
    from django.contrib.auth.models import User
    if v in ("", None):
        return "—"
    if camp in BIFE:
        return "Da" if v == "1" else "Nu"
    if camp == "tip_investitie":
        return dict(Proiect.TIP_INVESTITIE).get(v, v)
    if camp == "prioritate":
        return dict(Proiect._meta.get_field("prioritate").choices).get(v, v)
    if camp == "responsabil":
        u = User.objects.filter(pk=v).first() if str(v).isdigit() else None
        return (u.get_full_name() or u.username) if u else v
    if camp in NUMERICE:
        try:
            s = f"{Decimal(v):,.0f}".replace(",", ".")
        except InvalidOperation:
            return v
        return f"{s} {p.moneda_rz}" if camp != "valoare_rz" else f"{s}"
    if camp in ("data_demarare", "data_finalizare") and len(v) == 10:
        return f"{v[8:10]}.{v[5:7]}.{v[0:4]}"
    return v if len(v) <= 300 else v[:300] + "…"


def rezumat_modificari(p, ref, modificate, capitole, echipamente, eliminate):
    """Lista modificărilor față de versiunea avizată: [{sectiune, ce, inainte, acum}] – pentru emailul de reavizare."""
    if not ref:
        return []
    vr, vc = valori_referinta(ref), valori_curente(p)
    rez = []
    for camp in CAMPURI:
        if camp in modificate:
            rez.append({"sectiune": "Date generale", "ce": ETICHETE.get(camp, camp),
                        "inainte": _afisare(camp, vr.get(camp), p), "acum": _afisare(camp, vc.get(camp), p)})
    if "responsabil" in modificate:
        rez.append({"sectiune": "Date generale", "ce": ETICHETE["responsabil"],
                    "inainte": _afisare("responsabil", vr.get("responsabil"), p), "acum": _afisare("responsabil", vc.get("responsabil"), p)})
    fmt = lambda v: f"{Decimal(str(v or 0)):,.0f}".replace(",", ".")  # noqa: E731
    for c in capitole:
        for l in c["linii"]:
            if l.get("rev_modificat"):
                rez.append({"sectiune": f"Buget ({p.moneda_rz})", "ce": f"{l['cod']} {l['denumire']}",
                            "inainte": fmt(l.get("ref")), "acum": fmt(l["valoare"]),
                            "diferenta": Decimal(str(l["valoare"] or 0)) - Decimal(str(l.get("ref") or 0))})
    for e in echipamente:
        stare = getattr(e, "rev_stare", "") if not isinstance(e, dict) else e.get("rev_stare", "")
        if stare:
            den = e.denumire if not isinstance(e, dict) else e["denumire"]
            val = getattr(e, "valoare_afisata", None) if not isinstance(e, dict) else e.get("valoare")
            rez.append({"sectiune": "Echipamente", "ce": den, "inainte": "—" if stare == "nou" else "altă valoare / linie",
                        "acum": ("adăugat" if stare == "nou" else "modificat") + (f" · {fmt(val)} {p.moneda_rz}" if val is not None else "")})
    for den in eliminate:
        rez.append({"sectiune": "Echipamente", "ce": den, "inainte": "în listă", "acum": "eliminat"})
    return rez
