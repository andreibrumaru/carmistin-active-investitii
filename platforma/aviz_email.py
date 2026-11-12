"""Avizarea Raportului Zero din email: la trimiterea spre avizare pleacă un email cu fișa raportului către aprobator,
cu un link semnat (valabil RZ_LINK_ZILE zile, o singură dată) care deschide o pagină de confirmare Aprobă / Respinge.
Linkul NU aprobă direct (scanerele de email deschid automat linkurile); aprobarea cere apăsarea butonului din pagină."""
import hashlib
import json

from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import models as m

SALT = "carmistin.aviz-rz"


def aprobator():
    """Utilizatorul care avizează din email: contul cu adresa RZ_APROBATOR_EMAIL, altfel administratorul principal."""
    return (User.objects.filter(email__iexact=settings.RZ_APROBATOR_EMAIL, is_active=True).first()
            or User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first())


def amprenta(p):
    """Amprenta conținutului RZ: dacă raportul se modifică după trimitere, linkul din email nu mai e valabil."""
    return hashlib.sha256(json.dumps(p.snapshot_rz(), sort_keys=True, default=str).encode()).hexdigest()


def link(e):
    return settings.SITE_URL + reverse("aviz_din_email", args=[signing.dumps({"e": e.pk}, salt=SALT)])


def citeste_token(token):
    """Întoarce (EmailAvizRZ, None) dacă linkul e valabil, altfel (email sau None, motivul)."""
    try:
        d = signing.loads(token, salt=SALT, max_age=settings.RZ_LINK_ZILE * 86400)
    except signing.SignatureExpired:
        return None, f"Linkul a expirat (este valabil {settings.RZ_LINK_ZILE} zile). Cereți retrimiterea emailului din aplicație."
    except signing.BadSignature:
        return None, "Link invalid."
    e = m.EmailAvizRZ.objects.select_related("proiect__locatie", "proiect__beneficiar", "aprobator").filter(pk=d.get("e")).first()
    if not e:
        return None, "Link invalid."
    p = e.proiect
    if e.raspuns:
        return e, f"Răspunsul a fost deja dat din acest email ({e.get_raspuns_display()}, {timezone.localtime(e.raspuns_la):%d.%m.%Y %H:%M})."
    if p.stare_rz != "trimis" or p.revizie_rz != e.revizie:
        return e, f"Raportul Zero nu mai așteaptă avizare (stare actuală: {p.get_stare_rz_display()}{f', rev.{p.revizie_rz}' if p.revizie_rz else ''})."
    if e.amprenta and e.amprenta != amprenta(p):
        return e, "Raportul Zero a fost modificat după trimiterea acestui email. Folosiți emailul cel mai recent sau avizați din aplicație."
    if p.emailuri_aviz.filter(revizie=e.revizie, trimis_la__gt=e.trimis_la).exclude(eroare__gt="").exists():
        return e, "Pentru acest raport a fost trimis între timp un email mai nou. Folosiți linkul din emailul cel mai recent."
    return e, None


def trimite(p, trimis_de=None):
    """Trimite emailul de avizare. Întoarce (ok, mesaj)."""
    from .views import _buget_rz
    apr = aprobator()
    catre = settings.RZ_APROBATOR_EMAIL
    e = m.EmailAvizRZ.objects.create(proiect=p, revizie=p.revizie_rz, catre=catre, aprobator=apr, trimis_de=trimis_de,
                                     amprenta=amprenta(p), salvat_local=not settings.EMAIL_CONFIGURAT)
    capitole, total = _buget_rz(p)
    ctx = {"p": p, "e": e, "link": link(e), "link_aplicatie": settings.SITE_URL + reverse("raport_zero", args=[p.pk]),
           "capitole_buget": capitole, "total_buget": total,
           "echipamente": list(p.echipamente.select_related("linie_buget").order_by("linie_buget__capitol", "pk")), "zile": settings.RZ_LINK_ZILE, "trimis_de": trimis_de}
    subiect = f"Avizare Raport Zero {p.cod}{f' rev.{p.revizie_rz}' if p.revizie_rz else ''} – {p.denumire}"[:200]
    text = render_to_string("platforma/email/aviz_rz.txt", ctx)
    html = render_to_string("platforma/email/aviz_rz.html", ctx)
    msg = EmailMultiAlternatives(subiect, text, settings.DEFAULT_FROM_EMAIL, [catre],
                                 reply_to=[trimis_de.email] if trimis_de and trimis_de.email else None)
    msg.attach_alternative(html, "text/html")
    for d in p.documente.filter(categorie="raport_zero", arhivat=False)[:3]:  # PDF-ul semnat, dacă există
        try:
            if d.fisier.size <= 10 * 1024 * 1024:
                msg.attach_file(d.fisier.path)
        except OSError:
            pass
    try:
        msg.send()
    except Exception as ex:  # noqa: BLE001
        e.eroare = str(ex)[:300]
        if "535" in e.eroare or "unexpectedly closed" in e.eroare:  # Gmail respinge parola, apoi închide conexiunea
            e.eroare = "Gmail a refuzat autentificarea – verificați parola de aplicație (16 litere mici) din .env."
        e.save(update_fields=["eroare"])
        return False, f"Emailul către {catre} nu a putut fi trimis: {e.eroare}"
    if e.salvat_local:
        return True, (f"Email de avizare pregătit pentru {catre}, dar NU a plecat: contul Gmail nu e configurat încă "
                      f"(EMAIL_HOST_PASSWORD în .env). Emailul a fost salvat în date/emailuri.")
    return True, f"Email de avizare trimis către {catre}."
