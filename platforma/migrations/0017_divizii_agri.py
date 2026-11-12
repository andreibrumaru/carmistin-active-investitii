"""Divizii suplimentare pentru linia Agri: FNC, Peleți, Compost."""
from django.db import migrations


def aplica(apps, schema_editor):
    Nomenclator = apps.get_model("platforma", "Nomenclator")
    for i, d in enumerate(["FNC", "Peleți", "Compost"], start=10):
        Nomenclator.objects.get_or_create(tip="divizie", valoare=f"Agri – {d}", defaults={"grup": "Agri", "ordine": i})


class Migration(migrations.Migration):
    dependencies = [("platforma", "0016_normalizare_linii_divizii")]
    operations = [migrations.RunPython(aplica, migrations.RunPython.noop)]
