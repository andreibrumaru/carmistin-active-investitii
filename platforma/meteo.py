"""Vremea pe șantier din Open-Meteo (https://open-meteo.com) – serviciu gratuit, fără cont și fără cheie de acces.

Se trimit doar coordonatele activului și data. Pentru azi și ultimele ~3 luni se folosește serviciul de prognoză
(care păstrează și zilele trecute), pentru date mai vechi arhiva istorică. Rezultatele se țin în cache o oră.
Dacă serviciul nu răspunde, aplicația merge mai departe fără vreme (completare manuală).
"""
import datetime

import requests
from django.core.cache import cache
from django.utils import timezone

URL_PROGNOZA = "https://api.open-meteo.com/v1/forecast"
URL_ARHIVA = "https://archive-api.open-meteo.com/v1/archive"
TIMEOUT = (3, 4)  # conectare, citire (secunde)
INCERCARI = 3
ZILE_PROGNOZA_TRECUT = 90

# codurile WMO ale stării vremii → opțiunile din formularul raportului de șantier
def stare_din_cod(cod):
    if cod is None:
        return ""
    if cod == 0:
        return "Senin"
    if cod in (1, 2):
        return "Parțial noros"
    if cod == 3:
        return "Înnorat"
    if cod in (45, 48):
        return "Ceață"
    if 51 <= cod <= 67:
        return "Ploaie"
    if 71 <= cod <= 77 or cod in (85, 86):
        return "Ninsoare"
    if 80 <= cod <= 82 or cod >= 95:
        return "Averse"
    return "Înnorat"


def _fmt(v, zecimale=0):
    if v is None:
        return ""
    return f"{v:.{zecimale}f}".replace(".", ",") if zecimale else str(round(v))


def vremea(lat, lng, data=None):
    """{"vreme": opțiunea din formular, "detalii": text, "valori": {...}} sau ridică ValueError cu un mesaj pentru utilizator."""
    data = data or timezone.localdate()
    azi = timezone.localdate()
    if data > azi:
        data = azi
    cheie = f"meteo:{round(float(lat), 3)}:{round(float(lng), 3)}:{data.isoformat()}"
    rez = cache.get(cheie)
    if rez:
        return rez
    arhiva = data < azi - datetime.timedelta(days=ZILE_PROGNOZA_TRECUT)
    param = {
        "latitude": round(float(lat), 4), "longitude": round(float(lng), 4), "timezone": "Europe/Bucharest",
        "start_date": data.isoformat(), "end_date": data.isoformat(),
        "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_sum,wind_speed_10m_max",
    }
    zi, eroare = None, None
    for _ in range(INCERCARI):  # serviciul răspunde de obicei în sub o secundă, dar uneori o cerere rămâne agățată
        try:
            r = requests.get(URL_ARHIVA if arhiva else URL_PROGNOZA, params=param, timeout=TIMEOUT,
                             headers={"User-Agent": "aplicatie-active-investitii/1.0"})
            r.raise_for_status()
            zi = r.json().get("daily") or {}
            break
        except (requests.RequestException, ValueError) as ex:
            eroare = ex
    if zi is None:
        raise ValueError("Serviciul meteo nu a răspuns – completați vremea manual.") from eroare
    val = lambda k: (zi.get(k) or [None])[0]  # noqa: E731
    cod, tmin, tmax = val("weather_code"), val("temperature_2m_min"), val("temperature_2m_max")
    precip, vant = val("precipitation_sum"), val("wind_speed_10m_max")
    if cod is None and tmax is None:
        raise ValueError("Nu există date meteo pentru această zi și locație.")
    stare = stare_din_cod(cod)
    if tmax is not None and tmax >= 33:
        stare = "Caniculă"
    elif tmin is not None and tmin <= -2 and stare in ("Senin", "Parțial noros"):
        stare = "Îngheț"
    elif vant is not None and vant >= 50 and stare in ("Senin", "Parțial noros", "Înnorat"):
        stare = "Vânt puternic"
    parti = []
    if tmin is not None and tmax is not None:
        parti.append(f"{_fmt(tmin)}…{_fmt(tmax)} °C")
    if precip:
        parti.append(f"{_fmt(precip, 1)} mm")
    if vant is not None:
        parti.append(f"vânt {_fmt(vant)} km/h")
    rez = {"vreme": stare, "detalii": ", ".join(parti),
           "valori": {"cod": cod, "tmin": tmin, "tmax": tmax, "precipitatii": precip, "vant": vant, "data": data.isoformat()}}
    cache.set(cheie, rez, 3600)
    return rez
