from django.db import migrations


def copiaza(apps, schema_editor):
    Contract = apps.get_model("platforma", "Contract")
    ContractAlocare = apps.get_model("platforma", "ContractAlocare")
    for c in Contract.objects.exclude(linie_buget__isnull=True):
        ContractAlocare.objects.get_or_create(contract=c, linie_buget_id=c.linie_buget_id, defaults={"valoare_ron": c.valoare_ron})


class Migration(migrations.Migration):
    dependencies = [("platforma", "0003_alocari_contracte")]
    operations = [migrations.RunPython(copiaza, migrations.RunPython.noop)]
