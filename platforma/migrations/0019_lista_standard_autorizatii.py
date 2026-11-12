"""Lista standard de autorizații de funcționare (validată), cu documentele suport."""
from django.db import migrations

LISTA = [
    ("mediu", "Autorizație de mediu / autorizație integrată de mediu", "Aut. mediu", "APM / ANPM",
     "Studiu de impact asupra mediului (EIM)\nStudiu de evaluare a impactului asupra sănătății\nStudiu de dispersie a mirosurilor\n"
     "Studiu de dispersie noxe și zgomot\nRaport de amplasament\nPlan de management al dejecțiilor\nBilanț de mediu"),
    ("gospodarire_ape", "Autorizație de gospodărire a apelor", "Aut. ape", "Apele Române / ABA",
     "Aviz de gospodărire a apelor\nStudiu hidrogeologic\nAutorizații foraje\nContract de prelevare a apei"),
    ("sanitar_veterinar", "Autorizație sanitar-veterinară", "Aut. DSV", "DSVSA",
     "Înregistrare exploatație (cod ANSVSA)\nPlan de biosecuritate"),
    ("isu", "Autorizație de securitate la incendiu", "Aut. ISU", "ISU",
     "Aviz de securitate la incendiu\nScenariu de securitate la incendiu\nPV verificare instalații de stingere"),
    ("drumuri", "Acord / autorizație de acces la drum național", "Acces DN", "CNAIR / administratorul drumului",
     "Plan de acces\nPV de recepție a accesului"),
    ("sanitar", "Notificare / certificat de conformitate sanitară", "Notif. DSP", "DSP", "Buletine de analiză apă potabilă"),
    ("iscir", "Autorizații ISCIR", "Aut. ISCIR", "ISCIR", "Cazane\nRecipiente sub presiune\nInstalații de ridicat"),
    ("metrologie", "Verificare metrologică", "Metrologie", "BRML", "Buletin de verificare cântare / poduri basculă"),
    ("functionare", "Autorizație de funcționare", "Aut. funcționare", "Primăria", ""),
    ("anre", "Licență / certificat de racordare", "ANRE / racordare", "ANRE / operator de distribuție", "ATR\nCertificat de racordare\nLicență ANRE"),
    ("anif", "Aviz ANIF", "Aviz ANIF", "ANIF", ""),
]


def aplica(apps, schema_editor):
    T = apps.get_model("platforma", "TipAutorizatie")
    for i, (cod, den, scurt, emitent, suport) in enumerate(LISTA):
        T.objects.update_or_create(cod=cod, defaults={"denumire": den, "scurt": scurt, "emitent": emitent,
                                                       "documente_suport": suport, "ordine": i, "activ": True})


class Migration(migrations.Migration):
    dependencies = [("platforma", "0018_autorizatii_standard_standarde")]
    operations = [migrations.RunPython(aplica, migrations.RunPython.noop)]
