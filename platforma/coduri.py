"""Normalizarea și generarea codurilor (locație BIO.03, proiect BIO.03.01, nr. înregistrare BIO.20260212.1)."""
import re

from django.utils import timezone

_RE_COD = re.compile(r"^([A-Za-z]{2,6})[\s._-]*(\d{1,3})(?:[\s._-]+(\d{1,3}))?$")


def normalizeaza_cod(text):
    """'BIO 03' -> ('BIO.03', ''); 'FFR 7' -> 'FFR.07'; 'FFR 03 / AXL 03' -> ('FFR.03', 'AXL.03').
    Întoarce (cod, alias); cod None dacă textul nu e recunoscut."""
    if not text:
        return None, ""
    parti = [p.strip() for p in re.split(r"/", str(text)) if p.strip()]
    rez = []
    for p in parti:
        m = _RE_COD.match(p)
        if not m:
            continue
        prefix, nr, sub = m.group(1).upper(), int(m.group(2)), m.group(3)
        cod = f"{prefix}.{nr:02d}"
        if sub:
            cod += f".{int(sub):02d}"
        rez.append(cod)
    if not rez:
        return None, ""
    return rez[0], ", ".join(rez[1:])


def urmatorul_cod_locatie(prefix):
    from .models import Locatie
    prefix = prefix.upper()
    max_nr = 0
    for c in Locatie.objects.filter(cod__startswith=prefix + ".").values_list("cod", flat=True):
        try:
            max_nr = max(max_nr, int(c.split(".")[1]))
        except (IndexError, ValueError):
            pass
    return f"{prefix}.{max_nr + 1:02d}"


def nr_inregistrare_nou(prefix, data=None):
    from .models import Proiect
    data = data or timezone.localdate()
    baza = f"{prefix}.{data:%Y%m%d}."
    n = Proiect.objects.filter(nr_inregistrare__startswith=baza).count() + 1
    while Proiect.objects.filter(nr_inregistrare=f"{baza}{n}").exists():
        n += 1
    return f"{baza}{n}"


# ---------------------------------------------------------------- acronime (prefixe) pe societăți
import unicodedata as _ud

_SUFIXE = {"SRL", "SA", "SC", "SNC", "SCS", "PFA", "II", "IF", "AG", "GMBH", "LTD", "SPA", "SAS", "COMPANY", "CO"}
_UMPLUTURA = {"DE", "SI", "AND", "THE", "OF", "PENTRU", "LA", "&", "-"}


def prefixe_folosite(exclude_societate=None):
    """Toate prefixele deja folosite: cele alocate societăților și cele din codurile de locații existente."""
    from .models import Locatie, Societate
    qs = Societate.objects.exclude(prefix_cod="")
    if exclude_societate:
        qs = qs.exclude(pk=exclude_societate)
    folosite = {p.upper() for p in qs.values_list("prefix_cod", flat=True)}
    folosite |= {c.split(".")[0].upper() for c in Locatie.objects.exclude(cod__isnull=True).values_list("cod", flat=True)}
    return folosite


def _cuvinte(denumire):
    t = _ud.normalize("NFD", denumire or "").encode("ascii", "ignore").decode().upper()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"\bS\.?\s?R\.?\s?L\.?\b|\bS\.?\s?A\.?\b|\bS\.?\s?C\.?\b", " ", t)
    cuv = [w for w in re.findall(r"[A-Z0-9]+", t) if w not in _SUFIXE and w not in _UMPLUTURA]
    return cuv or ["X"]


def _candidati(denumire):
    cuv = _cuvinte(denumire)
    litere = lambda w: re.sub(r"[^A-Z]", "", w)
    cuv = [litere(w) for w in cuv if litere(w)] or ["XXX"]
    cons = lambda w: w[0] + re.sub(r"[AEIOU]", "", w[1:])
    c = []
    if len(cuv) >= 3:
        c.append("".join(w[0] for w in cuv[:3]))
        c.append("".join(w[0] for w in cuv[:4]))
    if len(cuv) >= 2:
        a, b = cuv[0], cuv[1]
        c += [a[0] + cons(b)[:2], cons(a)[:2] + b[0], a[:2] + b[0], a[0] + b[:2], cons(a)[:2] + cons(b)[:2]]
    w = cuv[0]
    c += [cons(w)[:3], w[:3], cons(w)[:2] + w[-1], cons(w)[:4]]
    if len(cuv) >= 2:
        c += [cons(cuv[0])[:3] + cuv[1][0], cuv[0][0] + cons(cuv[1])[:3]]
    rez = []
    for x in c:
        if 2 <= len(x) <= 5 and x not in rez:
            rez.append(x)
    return rez


def propune_acronim(denumire, exclude_societate=None):
    """Acronim unic (2–5 litere) derivat din numele societății, care nu se suprapune cu niciun prefix existent."""
    folosite = prefixe_folosite(exclude_societate)
    for c in _candidati(denumire):
        if c not in folosite:
            return c
    baza = (_candidati(denumire) or ["SOC"])[0][:3]
    for litera in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if baza + litera not in folosite:
            return baza + litera
    n = 2
    while f"{baza}{n}" in folosite:
        n += 1
    return f"{baza}{n}"


def valideaza_prefix(prefix, societate_pk=None):
    """Întoarce mesajul de eroare sau None. Prefixul trebuie să fie 2–5 litere și să nu aparțină altei societăți."""
    from .models import Locatie, Societate
    prefix = (prefix or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}", prefix):
        return "Acronimul trebuie să aibă 2–5 litere (fără cifre, spații sau diacritice)."
    alta = Societate.objects.filter(prefix_cod__iexact=prefix).exclude(pk=societate_pk).first()
    if alta:
        return f"Acronimul {prefix} este deja alocat societății {alta}."
    loc = Locatie.objects.filter(cod__istartswith=prefix + ".").exclude(proprietar_id=societate_pk).first()
    if loc:
        return f"Acronimul {prefix} este deja folosit de locația {loc.cod} ({loc.proprietar or 'alt proprietar'})."
    return None
