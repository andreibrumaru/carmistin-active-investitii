"""Reguli de acces.

- Registrul activelor și registrul Rapoartelor Zero: vizibile tuturor utilizatorilor autentificați.
- Datele de proiect (buget, contracte, facturi, avize, documente…): doar cu drept.
  admin / management → toate; coordonator → proiectele lui + ale PM-ilor din subordine;
  PM → proiectele lui; responsabilul de implementare → proiectele la care e desemnat;
  rolul „vizualizare” → toate proiectele, fără nicio modificare; oricine → proiectele cu acces explicit.
- Editare: admin, coordonatorul, PM-ul și responsabilul de implementare al proiectului, plus accesele explicite de tip „editare”.
- Activele le modifică doar administratorul.
"""
from django.db.models import Q

from .models import Proiect


def rol(user):
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return "admin"
    p = getattr(user, "profil", None)
    return p.rol if p else "utilizator"


def e_admin(user):
    return rol(user) == "admin"


def vede_tot(user):
    """Admin, management, vizualizare și financiar văd toate proiectele (financiarul are nevoie pentru aprobări și export ERP)."""
    return rol(user) in ("admin", "management", "vizualizare", "financiar")


def doar_citire(user):
    return rol(user) in ("management", "vizualizare")


def e_financiar(user):
    return rol(user) in ("admin", "financiar")


def proiecte_vizibile(user):
    qs = Proiect.objects.select_related("locatie", "beneficiar", "pm", "coordonator")
    if vede_tot(user):
        return qs
    return qs.filter(
        Q(pm=user) | Q(coordonator=user) | Q(pm__profil__coordonator=user) | Q(implementator=user) | Q(accese__user=user)
    ).distinct()


def poate_vedea(user, proiect):
    return proiecte_vizibile(user).filter(pk=proiect.pk).exists()


def poate_edita(user, proiect):
    if e_admin(user):
        return True
    if doar_citire(user):
        return False
    if user.pk in (proiect.pm_id, proiect.coordonator_id, proiect.implementator_id):
        return True
    if proiect.pm_id and getattr(getattr(proiect.pm, "profil", None), "coordonator_id", None) == user.pk:
        return True
    return proiect.accese.filter(user=user, nivel="editare").exists()


def poate_edita_financiar(user, proiect):
    """Facturi și plăți: echipa proiectului + responsabilii financiari."""
    return poate_edita(user, proiect) or rol(user) == "financiar"


def poate_modifica_buget(user, proiect):
    """Înainte de blocare: echipa proiectului. După blocare: doar administratorul (prin rectificări)."""
    if proiect.buget_blocat:
        return e_admin(user)
    return poate_edita(user, proiect)


def poate_bloca_buget(user, proiect):
    return e_admin(user) or user.pk == proiect.coordonator_id


def poate_aproba_tehnic(user, proiect):
    if e_admin(user):
        return True
    return user.pk == (proiect.aprobator_tehnic_id or proiect.coordonator_id)


def poate_aproba_financiar(user, proiect):
    if e_admin(user):
        return True
    if proiect.aprobator_financiar_id:
        return user.pk == proiect.aprobator_financiar_id
    return rol(user) == "financiar"


def poate_aproba_suplimentar(user, proiect):
    return e_admin(user) or (proiect.aprobator_suplimentar_id and user.pk == proiect.aprobator_suplimentar_id)


def poate_crea_proiect(user):
    return rol(user) in ("admin", "coordonator", "pm")


def poate_aviza_rz(user):
    return rol(user) in ("admin", "coordonator")


class DoarCitireMiddleware:
    """Utilizatorii cu drept doar de citire (management, vizualizare) nu pot trimite nicio modificare (POST), oriunde în aplicație.
    Excepție: ieșirea din cont și schimbarea propriei parole."""
    PERMISE = ("/logout/", "/iesire/", "/accounts/logout/", "/password_change/", "/accounts/password_change/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in ("GET", "HEAD", "OPTIONS") and getattr(request, "user", None) is not None \
                and request.user.is_authenticated and doar_citire(request.user) \
                and not any(request.path.endswith(x) or request.path.startswith(x) for x in self.PERMISE):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Contul dumneavoastră are doar drept de vizualizare.")
        return self.get_response(request)
