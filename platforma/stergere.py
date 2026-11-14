"""Ștergerea definitivă a unui proiect: arhivare completă înainte, apoi ștergerea în ordinea corectă a legăturilor."""
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
    dest = settings.BASE_DIR / "date" / "arhiva_stergeri" / f"{p.cod.replace('.', '_')}_{timezone.now():%Y%m%d-%H%M%S}"
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
