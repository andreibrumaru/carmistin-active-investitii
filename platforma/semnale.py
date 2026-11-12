from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profil

User = get_user_model()


@receiver(post_save, sender=User)
def creeaza_profil(sender, instance, created, **kw):
    if created:
        Profil.objects.get_or_create(user=instance, defaults={"rol": "admin" if instance.is_superuser else "utilizator"})


try:  # sincronizare rol din grupurile Active Directory la autentificare
    from django_auth_ldap.backend import populate_user

    @receiver(populate_user)
    def rol_din_ad(sender, user, ldap_user, **kw):
        grupuri = set(ldap_user.group_names)
        ordine = ["admin", "management", "coordonator", "financiar", "pm"]
        roluri = [r for g, r in settings.LDAP_GRUP_ROL.items() if g in grupuri]
        if user.pk is None:
            user.save()
        p, _ = Profil.objects.get_or_create(user=user)
        p.rol = next((r for r in ordine if r in roluri), "utilizator")
        p.save()
except ImportError:
    pass
