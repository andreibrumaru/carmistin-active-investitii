"""Ștergerea definitivă a unui proiect: arhivare completă înainte, apoi ștergerea în ordinea corectă a legăturilor."""
from pathlib import Path
import json
import shutil

from django.conf import settings
from django.core import serializers
from django.db import transaction
from django.utils import timezone

from . import models as m


def obiecte_proiect(p):
    """Toate înregistrările legate de proiect, grupate pe tip (pentru rezumat și arhivă)."""
    return [
        ("Documente", m.Document.objects.filter(proiect=p)),
        ("Extrageri AI", m.ExtragereAI.objects.filter(document__proiect=p)),
        ("Procese-verbale de închidere", m.ProcesVerbalInchidere.objects.filter(proiect=p)),
        ("Oferte (Raport Zero)", m.OfertaRZ.objects.filter(proiect=p)),
        ("Facturi în cereri de tragere", m.CerereTragereFactura.objects.filter(cerere__proiect=p)),
        ("Cereri de tragere", m.CerereTragere.objects.filter(proiect=p)),
        ("Rapoarte de progres (bancă)", m.RaportProgres.objects.filter(proiect=p)),
        ("Plăți", m.Plata.objects.filter(factura__proiect=p)),
        ("Alocări facturi", m.FacturaAlocare.objects.filter(factura__proiect=p)),
        ("Facturi", m.Factura.objects.filter(proiect=p)),
        ("Obligații", m.Obligatie.objects.filter(proiect=p)),
        ("Surse de finanțare", m.Finantare.objects.filter(proiect=p)),
        ("Avize", m.Aviz.objects.filter(proiect=p)),
        ("Certificate de urbanism", m.CertificatUrbanism.objects.filter(proiect=p)),
        ("Echipamente", m.Echipament.objects.filter(proiect=p)),
        ("Condiții contracte", m.ConditieContract.objects.filter(contract__proiect=p)),
        ("Acte adiționale", m.ActAditional.objects.filter(contract__proiect=p)),
        ("Alocări contracte", m.ContractAlocare.objects.filter(contract__proiect=p)),
        ("Contracte", m.Contract.objects.filter(proiect=p)),
        ("Rectificări buget", m.RectificareBuget.objects.filter(linie__proiect=p)),
        ("Linii de buget", m.LinieBuget.objects.filter(proiect=p)),
        ("Activități Gantt", m.Activitate.objects.filter(proiect=p)),
        ("Bugete anuale", m.PlanAnual.objects.filter(proiect=p)),
        ("Revizii Raport Zero", m.RevizieRaportZero.objects.filter(proiect=p)),
        ("Accese", m.AccesProiect.objects.filter(proiect=p)),
    ]


def rezumat(p):
    return [(nume, qs.count()) for nume, qs in obiecte_proiect(p) if qs.exists()]


def arhiveaza(p):
    """Salvează toate datele proiectului (JSON) și fișierele lui în date/arhiva_stergeri/<cod>_<data-ora>/."""
    # lângă folderul de fișiere al bazei de date folosite (date/arhiva_stergeri; la teste – în copia temporară)
    dest = Path(settings.MEDIA_ROOT).parent / "arhiva_stergeri" / f"{p.cod.replace('.', '_')}_{timezone.now():%Y%m%d-%H%M%S}"
    (dest / "fisiere").mkdir(parents=True, exist_ok=True)
    date = {"proiect": json.loads(serializers.serialize("json", [p]))}
    for nume, qs in obiecte_proiect(p):
        date[nume] = json.loads(serializers.serialize("json", qs))
    (dest / "date.json").write_text(json.dumps(date, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for d in m.Document.objects.filter(proiect=p):
        try:
            shutil.copy2(d.fisier.path, dest / "fisiere" / f"{d.pk}_{d.fisier.name.split('/')[-1]}")
        except (OSError, ValueError):
            pass
    return dest


@transaction.atomic
def sterge(p):
    """Șterge proiectul și tot ce ține de el (după arhivare). Jurnalul de audit se păstrează."""
    for _, qs in obiecte_proiect(p):
        qs.delete()  # ordinea listei respectă legăturile protejate (documente și plăți întâi)
    p.delete()


# ---------------------------------------------------------------- ștergerea unui activ din registrul activelor
def obiecte_activ(l):
    """Înregistrările legate de activ (fără proiecte: un activ cu proiecte de investiții nu se șterge)."""
    return [
        ("Extrageri AI", m.ExtragereAI.objects.filter(document__locatie=l)),
        ("Documente", m.Document.objects.filter(locatie=l)),
        ("Contracte de folosință", m.Locatie._meta.get_field("contracte_folosinta").related_model.objects.filter(locatie=l)),
        ("Istoric stări", m.IstoricStareActiv.objects.filter(locatie=l)),
        ("Cărți funciare", l.carti.all()), ("Rapoarte de evaluare", l.rapoarte_evaluare.all()),
        ("Cerințe autorizații", l.cerinte_autorizatii.all()), ("Autorizații", l.autorizatii.all()),
        ("Standarde", l.standarde.all()), ("Controale", l.controale.all()),
    ]


def arhiveaza_activ(l):
    eticheta = (l.cod or f"fara_cod_{l.pk}").replace(".", "_")
    dest = Path(settings.MEDIA_ROOT).parent / "arhiva_stergeri" / f"activ_{eticheta}_{timezone.now():%Y%m%d-%H%M%S}"
    (dest / "fisiere").mkdir(parents=True, exist_ok=True)
    date = {"activ": json.loads(serializers.serialize("json", [l]))}
    for nume, qs in obiecte_activ(l):
        date[nume] = json.loads(serializers.serialize("json", qs))
    (dest / "date.json").write_text(json.dumps(date, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for d in m.Document.objects.filter(locatie=l):
        try:
            shutil.copy2(d.fisier.path, dest / "fisiere" / f"{d.pk}_{d.fisier.name.split('/')[-1]}")
        except (OSError, ValueError):
            pass
    return dest


@transaction.atomic
def sterge_activ(l):
    docs = m.Document.objects.filter(locatie=l)
    if docs.filter(proiect__isnull=False).exists():
        raise ValueError("activul are documente legate de proiecte de investiții")
    m.Document.objects.filter(inlocuieste__in=docs).update(inlocuieste=None)
    docs.update(inlocuieste=None)
    if l.stare_activ == "reziliat" and l.cod:  # codul activului reziliat rămâne rezervat și după ștergere
        m.CodLocatieRetras.objects.get_or_create(cod=l.cod, defaults={"denumire": l.denumire[:255], "data_reziliere": l.data_reziliere})
    for _, qs in obiecte_activ(l):
        qs.delete()
    l.delete()
