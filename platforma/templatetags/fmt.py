from decimal import Decimal

from django import template

register = template.Library()


def _fmt(v, zecimale=0):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return v
    s = f"{v:,.{zecimale}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@register.filter
def bani(v):
    return _fmt(v, 0)


@register.filter
def bani2(v):
    return _fmt(v, 2)


def _in_moneda_proiect(v, p):
    """Sumele interne sunt în RON; la un proiect cu Raportul Zero în EUR se afișează în EUR, la cursul RZ."""
    k = getattr(p, "curs_urmarire", None) if getattr(p, "moneda_urmarire", "RON") == "EUR" else None
    if k:
        try:
            return Decimal(str(v or 0)) / Decimal(str(k))
        except Exception:  # noqa: BLE001
            return v
    return v


@register.filter
def bp(v, p):
    """Sumă în moneda de urmărire a proiectului (0 zecimale)."""
    return _fmt(_in_moneda_proiect(v, p), 0)


@register.filter
def bp2(v, p):
    """Sumă în moneda de urmărire a proiectului (2 zecimale)."""
    return _fmt(_in_moneda_proiect(v, p), 2)


@register.filter
def in_eur(v, curs):
    try:
        return _fmt(Decimal(v or 0) / Decimal(curs), 0)
    except Exception:
        return "—"


@register.filter
def contine(colectie, element):
    try:
        return element in colectie
    except TypeError:
        return False


@register.filter
def procent(a, b):
    try:
        return round(float(a or 0) / float(b) * 100, 1) if b else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def plafon(v, maxim=100):
    try:
        return min(float(v), float(maxim))
    except (TypeError, ValueError):
        return 0


@register.filter
def get(d, k):
    return d.get(k) if hasattr(d, "get") else None


@register.filter
def ha(mp):
    """mp -> hectare, cu 2 zecimale (format românesc)."""
    try:
        return _fmt(float(mp) / 10000, 2)
    except (TypeError, ValueError):
        return ""


@register.filter
def sub(a, b):
    """a − b (pentru totaluri în șabloane)."""
    try:
        return a - b
    except TypeError:
        return None


@register.filter
def baza_tragere(factura, cu_tva):
    """Valoarea facturii care se poate solicita la tragere (cu sau fără TVA)."""
    return factura.baza_tragere(bool(cu_tva))
