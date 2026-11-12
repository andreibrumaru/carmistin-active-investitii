"""Nomenclatorul de divizii pe linii + normalizarea valorilor de linie importate din Excel
(ex. „pui”, „PUI” → „Pui”; „pui][rep.” → linia „Pui”, divizia „Reproducție”)."""
from django.db import migrations

LINII = {"pui": "Pui", "porc": "Porc", "agri": "Agri", "retail": "Retail", "others": "Others", "office": "Office",
         "fotovoltaice": "Fotovoltaice", "investitii teren": "Investitii Teren", "investitii active": "Investitii Active"}
SUB = {"rep.": "Reproducție", "rep": "Reproducție"}
DIVIZII = {
    "Pui": ["Reproducție", "Incubație", "Broiler", "Abatorizare", "Procesare", "Fabrică de furaje"],
    "Porc": ["Reproducție", "Maternitate", "Creștere-îngrășare", "Abatorizare", "Procesare"],
    "Agri": ["Culturi vegetale", "Depozitare cereale", "Mecanizare"],
    "Retail": ["Magazine", "Distribuție"],
    "Others": ["Logistică", "Administrativ", "Energie"],
}


def aplica(apps, schema_editor):
    Nomenclator = apps.get_model("platforma", "Nomenclator")
    for i, (linie, divizii) in enumerate(DIVIZII.items()):
        Nomenclator.objects.get_or_create(tip="linie", valoare=linie, defaults={"ordine": i})
        for j, d in enumerate(divizii):
            Nomenclator.objects.get_or_create(tip="divizie", valoare=f"{linie} – {d}", defaults={"grup": linie, "ordine": j})
    for model in ("Locatie", "Proiect"):
        M = apps.get_model("platforma", model)
        for o in M.objects.exclude(linie=""):
            parti = [p.strip() for p in o.linie.strip("[] ").split("][")]
            linie = LINII.get(parti[0].lower(), o.linie)
            divizie = SUB.get(parti[1].lower(), parti[1]) if len(parti) > 1 else o.divizie
            if linie != o.linie or divizie != o.divizie:
                o.linie, o.divizie = linie, divizie
                o.save(update_fields=["linie", "divizie"])


class Migration(migrations.Migration):
    dependencies = [("platforma", "0015_linie_divizie")]
    operations = [migrations.RunPython(aplica, migrations.RunPython.noop)]
