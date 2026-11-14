"""Statusul investiției: În execuție / Finalizat / Anulat/Suspendat.

- La avizarea Raportului Zero investiția devine „În execuție”.
- Orice schimbare ulterioară se face printr-o cerere aprobată de administrator: cel care a avizat Raportul Zero
  (dacă e administrator activ), altfel orice administrator. Dacă cel care schimbă statusul este chiar aprobatorul,
  schimbarea se aplică imediat (rămâne înregistrată ca cerere aprobată).
"""
from django.utils import timezone

from . import models as m
from .permisiuni import e_admin

IN_EXECUTIE, FINALIZAT, ANULAT = "În execuție", "Finalizat", "Anulat/Suspendat"


def poate_aproba_status(user, p):
    if not e_admin(user):
        return False
    apr = p.aprobator_status
    return apr is None or apr.pk == user.pk


def aplica(p, status_nou):
    p.status = status_nou
    campuri = ["status"]
    if status_nou == FINALIZAT:
        p.faza, p.progres_fizic = "Finalizat", 100
        if not p.data_finalizare_reala:
            p.data_finalizare_reala = timezone.localdate()
        campuri += ["faza", "progres_fizic", "data_finalizare_reala"]
    p.save(update_fields=campuri)


def solicita(p, status_nou, user, motiv):
    """Cere schimbarea statusului. Întoarce (cerere, aplicat_imediat). Ridică ValueError dacă cererea nu e validă."""
    if status_nou not in dict(m.Proiect.STATUS_INVESTITIE):
        raise ValueError("Status necunoscut.")
    if p.stare_rz != "aprobat" and not p.status:
        raise ValueError("Statusul investiției se stabilește după avizarea Raportului Zero.")
    if status_nou == p.status:
        raise ValueError(f"Investiția are deja statusul „{status_nou}”.")
    if not (motiv or "").strip():
        raise ValueError("Motivul schimbării statusului este obligatoriu.")
    if p.cerere_status_deschisa:
        raise ValueError("Există deja o cerere de schimbare a statusului care așteaptă aprobarea administratorului.")
    c = m.CerereStatus.objects.create(proiect=p, status_vechi=p.status, status_nou=status_nou, motiv=motiv.strip()[:2000], cerut_de=user)
    if poate_aproba_status(user, p):
        decide(c, user, True)
        return c, True
    return c, False


def decide(c, user, aproba, motiv=""):
    if c.stare != "asteptare":
        raise ValueError("Cererea a fost deja soluționată.")
    if not poate_aproba_status(user, c.proiect):
        raise PermissionError("Schimbarea statusului se aprobă de administratorul care a avizat Raportul Zero.")
    c.stare, c.decis_de, c.decis_la, c.motiv_decizie = ("aprobata" if aproba else "respinsa"), user, timezone.now(), (motiv or "")[:500]
    c.save()
    if aproba:
        aplica(c.proiect, c.status_nou)


def cereri_de_aprobat(user):
    if not e_admin(user):
        return m.CerereStatus.objects.none()
    qs = m.CerereStatus.objects.filter(stare="asteptare").select_related("proiect", "cerut_de", "proiect__aprobat_de_user")
    return [c for c in qs if poate_aproba_status(user, c.proiect)]
