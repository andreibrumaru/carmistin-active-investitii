"""Curs EUR de la BNR (https://curs.bnr.ro/nbrfxrates.xml)."""
import datetime
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

import requests

from .models import CursValutar

URL_ZILNIC = "https://curs.bnr.ro/nbrfxrates.xml"
URL_AN = "https://curs.bnr.ro/files/xml/years/nbrfxrates{an}.xml"


def _parse(xml):
    root = ET.fromstring(xml)
    ns = {"b": re.match(r"\{(.*)\}", root.tag).group(1)}
    n = 0
    for cube in root.iter(f"{{{ns['b']}}}Cube"):
        data = datetime.date.fromisoformat(cube.get("date"))
        for rate in cube.findall("b:Rate", ns):
            if rate.get("currency") == "EUR":
                CursValutar.objects.update_or_create(data=data, defaults={"eur": Decimal(rate.text), "sursa": "BNR"})
                n += 1
    return n


def actualizeaza(an=None):
    url = URL_AN.format(an=an) if an else URL_ZILNIC
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return _parse(r.content)


_ultima_incercare = None


def curs_azi():
    """Cursul BNR cel mai recent; încearcă o actualizare de la BNR dacă lipsește cursul zilei (cel mult o dată pe oră)."""
    global _ultima_incercare
    from django.utils import timezone
    azi = timezone.localdate()
    ultim = CursValutar.objects.first()
    acum = timezone.now()
    if (not ultim or ultim.data < azi) and (not _ultima_incercare or (acum - _ultima_incercare).seconds > 3600):
        _ultima_incercare = acum
        try:
            actualizeaza()
        except Exception:  # noqa: BLE001 – fără internet rămâne ultimul curs cunoscut
            pass
        ultim = CursValutar.objects.first()
    return ultim


def curs_la(data):
    """Cursul BNR valabil la o dată (ultimul publicat până la acea dată)."""
    c = CursValutar.objects.filter(data__lte=data).first()
    if c is None or (data >= datetime.date.today() and c.data < data):
        c = curs_azi() if data >= datetime.date.today() else c
    return c
