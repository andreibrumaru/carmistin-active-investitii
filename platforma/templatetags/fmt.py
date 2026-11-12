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


@register.filter
def in_eur(v, curs):
    try:
        return _fmt(Decimal(v or 0) / Decimal(curs), 0)
    except Exception:
        return "—"


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
