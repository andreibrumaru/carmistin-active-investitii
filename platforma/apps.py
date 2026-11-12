from django.apps import AppConfig


class PlatformaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platforma"
    verbose_name = "Active & Investiții"

    def ready(self):
        from . import audit, semnale  # noqa: F401
