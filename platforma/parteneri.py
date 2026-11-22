"""Registrul partenerilor + interogarea serviciului public ANAF (Ministerul Finanțelor) după CUI.
Serviciul: https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva – gratuit, fără autentificare, max. 1 cerere / secundă."""
import re
import time

import requests
from django.core.cache import cache
from django.utils import timezone

from . import models as m

URL_ANAF = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"


class EroareAnaf(Exception):
    pass


def curata_cui(text):
    """„RO 14399840” → „14399840”; întoarce '' dacă nu arată ca un CUI."""
    cui = re.sub(r"\D", "", str(text or ""))
    return cui if 2 <= len(cui) <= 10 else ""


def cui_valid(cui):
    """Cifra de control a CUI-ului (algoritmul oficial, cheia 753217532)."""
    if not cui or not cui.isdigit() or len(cui) > 10:
        return False
    corp, control = cui[:-1].zfill(9), int(cui[-1])
    s = sum(int(a) * int(b) for a, b in zip(corp, "753217532")) * 10 % 11
    return (0 if s == 10 else s) == control


def _bool(v):
    return v if isinstance(v, bool) else None


def interogheaza_anaf(cui):
    """Datele firmei de la ANAF (dict cu câmpurile modelului Partener) sau None dacă CUI-ul nu există.
    Rezultatul se ține 1 zi în cache; ridică EroareAnaf dacă serviciul nu răspunde."""
    cui = curata_cui(cui)
    if not cui:
        return None
    cheie = f"anaf:{cui}"
    if (rez := cache.get(cheie)) is not None:
        return rez or None
    ultima_eroare = None
    for incercare in range(3):
        try:
            r = requests.post(URL_ANAF, json=[{"cui": int(cui), "data": timezone.localdate().isoformat()}], timeout=(4, 10),
                              headers={"Content-Type": "application/json"})
            r.raise_for_status()
            date = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            ultima_eroare = e
            time.sleep(1.1)  # ANAF acceptă cel mult o cerere pe secundă
    else:
        raise EroareAnaf(f"Serviciul ANAF nu răspunde momentan ({type(ultima_eroare).__name__}). Încercați din nou.")
    gasit = (date.get("found") or [None])[0]
    if not gasit:
        cache.set(cheie, {}, 3600)
        return None
    g = gasit.get("date_generale") or {}
    tva = gasit.get("inregistrare_scop_Tva") or {}
    inc = gasit.get("inregistrare_RTVAI") or {}
    ina = gasit.get("stare_inactiv") or {}
    sediu = gasit.get("adresa_sediu_social") or {}
    rez = {
        "cui": cui, "denumire": (g.get("denumire") or "").strip()[:200], "nr_reg_com": (g.get("nrRegCom") or "")[:40],
        "adresa": (g.get("adresa") or "")[:400], "judet": (sediu.get("sdenumire_Judet") or "")[:60],
        "cod_caen": (g.get("cod_CAEN") or "")[:10], "platitor_tva": _bool(tva.get("scpTVA")),
        "tva_incasare": _bool(inc.get("statusTvaIncasare")), "inactiv": bool(ina.get("statusInactivi")),
        "radiat": bool(ina.get("dataRadiere")), "stare_inregistrare": (g.get("stare_inregistrare") or "")[:200],
        "e_factura": _bool(g.get("statusRO_e_Factura")), "iban": (g.get("iban") or "")[:40], "telefon": (g.get("telefon") or "")[:60],
    }
    cache.set(cheie, rez, 86400)
    return rez


CAMPURI_ANAF = ("denumire", "nr_reg_com", "adresa", "judet", "cod_caen", "platitor_tva", "tva_incasare", "inactiv", "radiat",
                "stare_inregistrare", "e_factura", "iban", "telefon")


def salveaza_partener(cui, denumire="", date_anaf=None):
    """Creează sau actualizează partenerul din registru. Datele ANAF au prioritate; altfel denumirea introdusă."""
    cui = curata_cui(cui)
    if not cui:
        return None
    pt = m.Partener.objects.filter(cui=cui).first()
    if date_anaf:
        pt = pt or m.Partener(cui=cui)
        for k in CAMPURI_ANAF:
            v = date_anaf.get(k)
            if v not in (None, "") or k in ("inactiv", "radiat"):
                setattr(pt, k, v)
        pt.verificat_anaf_la = timezone.now()
        pt.save()
    elif not pt and (denumire or "").strip():
        pt = m.Partener.objects.create(cui=cui, denumire=denumire.strip()[:200])
    return pt


def partener_pentru(cui, denumire=""):
    """La salvarea unui contract / unei facturi: partenerul din registru (completat de la ANAF dacă nu a fost verificat)."""
    cui = curata_cui(cui)
    if not cui:
        return None
    pt = m.Partener.objects.filter(cui=cui).first()
    if pt and pt.verificat_anaf_la:
        return pt
    try:
        date = interogheaza_anaf(cui)
    except EroareAnaf:
        date = None
    return salveaza_partener(cui, denumire, date)


def ca_json(pt, sursa="registru"):
    return {"cui": pt.cui, "denumire": pt.denumire, "nr_reg_com": pt.nr_reg_com, "adresa": pt.adresa, "judet": pt.judet,
            "platitor_tva": pt.platitor_tva, "tva_incasare": pt.tva_incasare, "inactiv": pt.inactiv, "radiat": pt.radiat,
            "e_factura": pt.e_factura, "stare_inregistrare": pt.stare_inregistrare, "sursa": sursa,
            "verificat": timezone.localtime(pt.verificat_anaf_la).strftime("%d.%m.%Y") if pt.verificat_anaf_la else ""}
