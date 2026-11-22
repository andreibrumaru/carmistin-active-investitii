"""Solicitările de plată (flux de aprobare ca la Raportul Zero).

În „Plăți” se bifează facturile neachitate → se generează solicitarea (câte o plată propusă pe factură) → pleacă un email
cu tabelul plăților către aprobatorul financiar al proiectului (și către aprobatorul suplimentar, peste prag), cu link semnat
spre o pagină de confirmare Aprob / Resping. Aprobarea se poate da și din aplicație (Sarcinile mele → Aprobări sau tab-ul Plăți).
La aprobare plățile devin „aprobate (de exportat)” și urmează exportul pentru Charisma; la respingere facturile revin în listă."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .templatetags.fmt import bani2
from .permisiuni import e_admin

SALT = "carmistin.solicitare-plata"
CENT = Decimal("0.01")


def nume(u):
    return (u.get_full_name() or u.username) if u else ""


def facturi_de_plata(p):
    """Facturile proiectului cu rest neplătit (fără sumele deja în aprobare)."""
    rez = []
    for x in p.facturi.select_related("contract").prefetch_related("plati", "alocari", "documente").order_by("scadenta", "data", "pk"):
        rest = x.valoare_ron - x.platit_ron
        de_platit = rest - x.in_aprobare_ron
        if rest <= 0:
            continue
        x.rest = rest
        x.de_platit = max(de_platit, Decimal(0))
        x.blocata = ("nerepartizată pe buget" if x.nealocat_ron else ("deja în aprobare" if de_platit <= 0 else ""))
        rez.append(x)
    return rez


def _numar(p):
    baza = f"SP-{p.cod}-"
    n = m.SolicitarePlata.objects.filter(numar__startswith=baza).count() + 1
    while m.SolicitarePlata.objects.filter(numar=f"{baza}{n:02d}").exists():
        n += 1
    return f"{baza}{n:02d}"


def aprobator_plati():
    """Administratorul care aprobă solicitările de plată: cel ales în Setări aplicație → Fluxul plăților,
    altfel primul administrator activ cu adresă de email."""
    from .permisiuni import e_admin as _e_admin
    uid = m.SetareAplicatie.get("plati_aprobator_id")
    u = User.objects.filter(pk=uid, is_active=True).first() if str(uid).isdigit() else None
    if u:
        return u
    adm = [x for x in User.objects.filter(is_active=True).select_related("profil").order_by("pk") if _e_admin(x)]
    return next((x for x in adm if x.email), adm[0] if adm else None)


def emailuri_trezorerie():
    return [e.strip() for e in m.SetareAplicatie.get("plati_email_trezorerie").replace(";", ",").split(",") if "@" in e]


@transaction.atomic
def creeaza(user, p, sume, data_plata, observatii=""):
    """sume: {factura_pk: suma_ron}. Întoarce solicitarea creată; ridică ValueError cu motivul dacă nu se poate."""
    disponibile = {x.pk: x for x in facturi_de_plata(p)}
    if not sume:
        raise ValueError("Bifați cel puțin o factură.")
    erori = []
    for fid, suma in sume.items():
        x = disponibile.get(fid)
        if not x:
            erori.append(f"factura #{fid} nu mai are rest de plată")
        elif x.blocata:
            erori.append(f"{x.numar}: {x.blocata}")
        elif suma is None or suma <= 0:
            erori.append(f"{x.numar}: sumă invalidă")
        elif suma > x.de_platit + CENT:
            erori.append(f"{x.numar}: suma depășește restul de plată ({x.de_platit})")
    if erori:
        raise ValueError("; ".join(erori))
    s = m.SolicitarePlata(proiect=p, numar=_numar(p), data_plata=data_plata, observatii=observatii[:300], creata_de=user,
                          aprobator=aprobator_plati())
    s.save()
    for fid, suma in sume.items():
        m.Plata.objects.create(factura=disponibile[fid], data=data_plata, suma_ron=suma, propusa_de=user, solicitare=s,
                               observatii=f"Solicitarea {s.numar}")
    return s


# ----------------------------------------------------------------- drepturi și decizie
def poate_aproba(user, s, rol="financiar"):
    if s.stare != "trimisa" or not user or not user.is_active:
        return False
    if rol == "suplimentar":
        return bool(s.aprobator_suplimentar_id and not s.aprobat_suplimentar_de_id
                    and (user.pk == s.aprobator_suplimentar_id or e_admin(user)))
    return not s.aprobat_de_id and (user.pk == s.aprobator_id or e_admin(user))  # aprobă administratorul platformei


def roluri_utilizator(user, s):
    return [r for r in ("financiar", "suplimentar") if poate_aproba(user, s, r)]


@transaction.atomic
def decide(user, s, actiune, motiv="", rol=None):
    """actiune: aproba / respinge. rol: financiar / suplimentar (implicit primul rol pe care îl are utilizatorul)."""
    roluri = roluri_utilizator(user, s)
    if not roluri or (rol and rol not in roluri):
        raise PermissionDenied("Nu puteți decide asupra acestei solicitări de plată.")
    rol = rol or roluri[0]
    acum = timezone.now()
    plati = list(s.plati.all())
    if actiune == "respinge":
        if not motiv.strip():
            raise ValueError("Scrieți motivul respingerii.")
        s.stare, s.respinsa_de, s.respinsa_la, s.motiv = "respinsa", user, acum, motiv.strip()[:300]
        for pl in plati:
            pl.stare, pl.respinsa_motiv = "respinsa", f"Solicitarea {s.numar} respinsă: {motiv}"[:300]
            pl.save()
        s.save()
        return s
    if actiune != "aproba":
        raise ValueError("Acțiune necunoscută.")
    if rol == "suplimentar":
        s.aprobat_suplimentar_de, s.aprobat_suplimentar_la = user, acum
    else:
        s.aprobat_de, s.aprobat_la = user, acum
    gata = s.aprobat_de_id and (not s.aprobator_suplimentar_id or s.aprobat_suplimentar_de_id)
    if gata:
        s.stare = "aprobata"
        for pl in plati:  # aprobarea solicitării ține loc de aprobarea tehnică + financiară a fiecărei plăți
            pl.stare = "aprobata"
            pl.aprobat_tehnic_de, pl.aprobat_tehnic_la = s.creata_de, s.creata_la
            pl.aprobat_financiar_de, pl.aprobat_financiar_la = s.aprobat_de, s.aprobat_la
            if s.aprobat_suplimentar_de_id:
                pl.aprobat_suplimentar_de, pl.aprobat_suplimentar_la = s.aprobat_suplimentar_de, s.aprobat_suplimentar_la
            pl.save()
    s.save()
    return s


@transaction.atomic
def anuleaza(user, s):
    if s.stare != "trimisa" or not (user.pk == s.creata_de_id or e_admin(user)):
        raise PermissionDenied("Doar inițiatorul (sau administratorul) poate anula o solicitare în aprobare.")
    s.stare = "anulata"
    s.save()
    for pl in s.plati.all():
        pl.stare, pl.respinsa_motiv = "respinsa", f"Solicitarea {s.numar} anulată de inițiator"
        pl.save()


def de_aprobat(user):
    qs = m.SolicitarePlata.objects.filter(stare="trimisa").select_related("proiect", "creata_de", "aprobator", "aprobator_suplimentar") \
        .prefetch_related("plati")
    return [s for s in qs if roluri_utilizator(user, s)]


# ----------------------------------------------------------------- email
def link(s, rol):
    return settings.SITE_URL + reverse("solicitare_din_email", args=[signing.dumps({"s": s.pk, "r": rol}, salt=SALT)])


def citeste_token(token):
    """(solicitare, rol, eroare)."""
    try:
        d = signing.loads(token, salt=SALT, max_age=settings.RZ_LINK_ZILE * 86400)
    except signing.SignatureExpired:
        return None, None, f"Linkul a expirat (este valabil {settings.RZ_LINK_ZILE} zile). Aprobați din aplicație."
    except signing.BadSignature:
        return None, None, "Link invalid."
    s = m.SolicitarePlata.objects.select_related("proiect__beneficiar", "aprobator", "aprobator_suplimentar", "creata_de").filter(pk=d.get("s")).first()
    rol = d.get("r", "financiar")
    if not s:
        return None, None, "Link invalid."
    if s.stare != "trimisa":
        return s, rol, f"Solicitarea {s.numar} nu mai așteaptă aprobare (stare: {s.get_stare_display().lower()})."
    if rol == "suplimentar" and s.aprobat_suplimentar_de_id or rol == "financiar" and s.aprobat_de_id:
        return s, rol, "Ați aprobat deja această solicitare; se așteaptă celălalt aprobator."
    return s, rol, None


def randuri_email(s):
    rez = []
    for pl in s.plati.select_related("factura__contract", "factura__proiect").order_by("factura__scadenta", "pk"):
        f = pl.factura
        tva_ron = (f.tva or 0) * (f.curs or 1) if f.moneda == "EUR" else (f.tva or 0)
        tva = (Decimal(tva_ron) * pl.suma_ron / f.valoare_ron).quantize(CENT) if f.valoare_ron else Decimal(0)
        rez.append({"pl": pl, "f": f, "tva": tva, "total": pl.suma_ron + tva})
    return rez


def trimite(s, trimis_de=None):
    """Emailul cu tabelul plăților către aprobator(i). Întoarce (ok, mesaj)."""
    destinatari = [(s.aprobator, "financiar")]
    if s.aprobator_suplimentar_id and not s.aprobat_suplimentar_de_id:
        destinatari.append((s.aprobator_suplimentar, "suplimentar"))
    destinatari = [(u, r) for u, r in destinatari if u and u.email and not (r == "financiar" and s.aprobat_de_id)]
    if not destinatari:
        s.email_eroare = "Aprobatorul nu are adresă de email în cont."
        s.save(update_fields=["email_eroare"])
        return False, "Solicitarea a fost creată, dar aprobatorul nu are adresă de email; aprobarea se poate da din aplicație."
    randuri = randuri_email(s)
    ctx_baza = {"s": s, "p": s.proiect, "randuri": randuri, "total": sum((r["pl"].suma_ron for r in randuri), Decimal(0)),
                "total_tva": sum((r["tva"] for r in randuri), Decimal(0)), "total_plata": sum((r["total"] for r in randuri), Decimal(0)),
                "trimis_de": trimis_de, "zile": settings.RZ_LINK_ZILE,
                "link_aplicatie": settings.SITE_URL + reverse("proiect_detaliu", args=[s.proiect_id]) + "?tab=plati"}
    erori = []
    for u, rol in destinatari:
        ctx = {**ctx_baza, "aprobator": u, "rol": rol, "link": link(s, rol)}
        n = len(randuri)
        subiect = f"Solicitare de plată {s.numar} – {n} factur{'ă' if n == 1 else 'i'} – {bani2(ctx['total_plata'])} RON cu TVA – {s.proiect.cod}"
        msg = EmailMultiAlternatives(subiect, render_to_string("platforma/email/solicitare_plata.txt", ctx), settings.DEFAULT_FROM_EMAIL,
                                     [u.email], reply_to=[trimis_de.email] if trimis_de and trimis_de.email else None)
        msg.attach_alternative(render_to_string("platforma/email/solicitare_plata.html", ctx), "text/html")
        try:
            msg.send()
        except Exception as ex:  # noqa: BLE001
            erori.append(f"{u.email}: {str(ex)[:150]}")
        m.Notificare.objects.create(user=u, proiect=s.proiect, titlu=f"Solicitare de plată {s.numar} de aprobat",
                                    text=f"{len(randuri)} facturi · {bani2(ctx['total_plata'])} RON cu TVA",
                                    link=reverse("sarcini") + "?vedere=aprobari")
    s.email_trimis_la, s.email_catre = timezone.now(), ", ".join(u.email for u, _ in destinatari)[:300]
    s.email_eroare = "; ".join(erori)[:300]
    s.save(update_fields=["email_trimis_la", "email_catre", "email_eroare"])
    if erori:
        return False, "Emailul nu a putut fi trimis: " + s.email_eroare
    if not settings.EMAIL_CONFIGURAT:
        return True, f"Solicitarea a fost creată; emailul către {s.email_catre} a fost salvat local (contul de email nu e configurat)."
    return True, f"Solicitarea a fost trimisă spre aprobare către {s.email_catre}."


def documente_facturi(s, limita_mb=10, total_mb=20):
    """Fișierele facturilor din solicitare (PDF / imagini), pentru atașare la emailul către trezorerie."""
    rez, total = [], 0
    for pl in s.plati.select_related("factura"):
        for d in pl.factura.documente.filter(arhivat=False):
            try:
                marime = d.fisier.size
            except (OSError, ValueError):
                continue
            if marime > limita_mb * 1024 * 1024 or total + marime > total_mb * 1024 * 1024:
                continue
            total += marime
            rez.append(d)
    return rez


def trimite_trezorerie(s, trimis_de=None):
    """După aprobare: emailul către trezorerie cu centralizarea plăților aprobate și facturile atașate. Întoarce (ok, mesaj)."""
    catre = emailuri_trezorerie()
    if s.stare != "aprobata":
        return False, "Solicitarea nu este aprobată."
    if not catre:
        s.trezorerie_eroare = "Adresa trezoreriei nu este setată (Setări aplicație → Fluxul plăților)."
        s.save(update_fields=["trezorerie_eroare"])
        return False, s.trezorerie_eroare
    randuri = randuri_email(s)
    docs = documente_facturi(s)
    cu_doc = {d.factura_id for d in docs}
    ctx = {"s": s, "p": s.proiect, "randuri": randuri, "total": sum((r["pl"].suma_ron for r in randuri), Decimal(0)),
           "total_tva": sum((r["tva"] for r in randuri), Decimal(0)), "total_plata": sum((r["total"] for r in randuri), Decimal(0)),
           "docs": docs, "fara_doc": [r["f"] for r in randuri if r["f"].pk not in cu_doc],
           "link_aplicatie": settings.SITE_URL + reverse("proiect_detaliu", args=[s.proiect_id]) + "?tab=plati"}
    n = len(randuri)
    subiect = (f"De plată: {s.numar} – {s.proiect.beneficiar} – {n} factur{'ă' if n == 1 else 'i'} – "
               f"{bani2(ctx['total_plata'])} RON cu TVA – aprobată")
    msg = EmailMultiAlternatives(subiect, render_to_string("platforma/email/plata_trezorerie.txt", ctx), settings.DEFAULT_FROM_EMAIL, catre,
                                 reply_to=[s.aprobat_de.email] if s.aprobat_de and s.aprobat_de.email else None)
    msg.attach_alternative(render_to_string("platforma/email/plata_trezorerie.html", ctx), "text/html")
    import os
    for d in docs:
        try:
            nume_fisier = f"{d.factura.numar} – {os.path.basename(d.fisier.name)}".replace("/", "-")
            with d.fisier.open("rb") as fh:
                msg.attach(nume_fisier, fh.read())
        except OSError:
            pass
    try:
        msg.send()
    except Exception as ex:  # noqa: BLE001
        s.trezorerie_eroare = str(ex)[:300]
        s.save(update_fields=["trezorerie_eroare"])
        return False, f"Emailul către trezorerie nu a putut fi trimis: {s.trezorerie_eroare}"
    s.trezorerie_trimis_la, s.trezorerie_catre, s.trezorerie_eroare = timezone.now(), ", ".join(catre)[:300], ""
    s.save(update_fields=["trezorerie_trimis_la", "trezorerie_catre", "trezorerie_eroare"])
    return True, f"Centralizarea plăților ({n} factur{'ă' if n == 1 else 'i'}, {len(docs)} document{'' if len(docs) == 1 else 'e'} atașat{'' if len(docs) == 1 else 'e'}) a fost trimisă la trezorerie ({', '.join(catre)})."
