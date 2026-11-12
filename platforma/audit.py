"""Jurnal de audit automat: orice creare / modificare / ștergere pe modelele platformei."""
import threading

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

_local = threading.local()


class UtilizatorCurentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.user = getattr(request, "user", None)
        _local.ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip() or None
        try:
            return self.get_response(request)
        finally:
            _local.user = None
            _local.ip = None


def user_curent():
    u = getattr(_local, "user", None)
    return u if u is not None and u.is_authenticated else None


EXCLUSE = {"JurnalAudit", "Session"}


def _serial(v):
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def _valori(obj):
    d = {}
    for f in obj._meta.concrete_fields:
        if f.name in ("text_extras",):
            continue
        d[f.attname] = _serial(getattr(obj, f.attname))
    return d


def _proiect(obj):
    from .models import Proiect
    if isinstance(obj, Proiect):
        return obj.pk
    for cale in ("proiect_id", "contract.proiect_id", "factura.proiect_id", "linie.proiect_id"):
        cur = obj
        try:
            for p in cale.split("."):
                cur = getattr(cur, p)
            if cur:
                return cur
        except Exception:
            continue
    return None


def _app_ok(sender):
    return sender._meta.app_label == "platforma" and sender.__name__ not in EXCLUSE


@receiver(pre_save)
def _inainte(sender, instance, **kw):
    if not _app_ok(sender) or not instance.pk:
        return
    try:
        instance._audit_vechi = _valori(sender.objects.get(pk=instance.pk))
    except sender.DoesNotExist:
        pass


def _scrie(actiune, instance, modificari):
    from .models import JurnalAudit
    JurnalAudit.objects.create(
        user=user_curent(), actiune=actiune, model=instance._meta.verbose_name, obiect_id=str(instance.pk),
        obiect=str(instance)[:300], proiect_id=_proiect(instance), modificari=modificari, ip=getattr(_local, "ip", None),
    )


@receiver(post_save)
def _dupa(sender, instance, created, **kw):
    if not _app_ok(sender) or kw.get("raw"):
        return
    nou = _valori(instance)
    if created:
        _scrie("creare", instance, {k: [None, v] for k, v in nou.items() if v not in (None, "", 0)})
        return
    vechi = getattr(instance, "_audit_vechi", {})
    dif = {k: [vechi.get(k), v] for k, v in nou.items() if vechi.get(k) != v and k != "modificat_la"}
    if dif:
        _scrie("modificare", instance, dif)


@receiver(post_delete)
def _sters(sender, instance, **kw):
    if _app_ok(sender):
        _scrie("stergere", instance, _valori(instance))
