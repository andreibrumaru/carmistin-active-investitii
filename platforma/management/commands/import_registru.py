"""Importă registrul de locații din Excel (REGISTRU LOCATII ... .xlsx).

Foi citite:
  - „Locatii Grup” și „teren investitii” (aceeași structură; rândurile duplicate se unesc după Nr. Înregistrare)
  - „-” (obiective funcționale cu cod și linie de afacere) – completează locațiile existente după cod
  - „Liste” – nomenclatoare (linie, departament, tip finanțare, status, fază)
Rulare:  python manage.py import_registru "/cale/REGISTRU LOCATII 2026 ACTIVE.xlsx"
Importul se poate rula de mai multe ori: actualizează, nu dublează.
"""
import datetime
import re
import warnings
from collections import Counter
from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.management.base import BaseCommand
from django.db import transaction

from platforma.coduri import normalizeaza_cod
from platforma.models import Locatie, Nomenclator, Societate

warnings.filterwarnings("ignore", module="openpyxl")


def curat(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def data(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = curat(v).strip("[]")
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def numar(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = curat(v).replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def coordonate(text):
    s = curat(text)
    m = re.match(r"^(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)$", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    dms = re.findall(r"(\d+)°(\d+)'([\d.]+)\"?\s*([NSEW])", s)
    if len(dms) == 2:
        vals = []
        for g, mi, se, h in dms:
            v = int(g) + int(mi) / 60 + float(se) / 3600
            vals.append(-v if h in "SW" else v)
        return vals[0], vals[1]
    return None, None


def nume_societate(s):
    s = curat(s)
    if not s or s in ("-", "0"):
        return ""
    s = re.sub(r"\bS\.?\s?R\.?\s?L\.?$", "SRL", s, flags=re.I)
    s = re.sub(r"\bS\.?\s?A\.?$", "SA", s)
    return s


class Command(BaseCommand):
    help = "Importă registrul de locații din fișierul Excel"

    def add_arguments(self, parser):
        parser.add_argument("fisier")

    @transaction.atomic
    def handle(self, fisier, **opt):
        wb = openpyxl.load_workbook(fisier, data_only=True)
        self.soc_cache = {}
        self.stat = Counter()
        foi = {ws.title.strip().lower(): ws for ws in wb}
        for nume, tip in (("locatii grup", "activ"), ("teren investitii", "teren")):
            if nume in foi:
                self.importa_registru(foi[nume], tip)
        if "-" in foi:
            self.importa_obiective(foi["-"])
        if "liste" in foi:
            self.importa_liste(foi["liste"])
        self.seteaza_prefixe()
        fara_cod = Locatie.objects.filter(cod__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(
            f"Import terminat: {dict(self.stat)}. Total locații: {Locatie.objects.count()} (fără cod: {fara_cod})."
        ))

    def societate(self, nume):
        nume = nume_societate(nume)
        if not nume:
            return None
        cheie = nume.lower()
        if cheie not in self.soc_cache:
            soc = Societate.objects.filter(denumire__iexact=nume).first() or Societate.objects.create(denumire=nume)
            self.soc_cache[cheie] = soc
        return self.soc_cache[cheie]

    def importa_registru(self, ws, tip_implicit):
        randuri = ws.iter_rows(values_only=True)
        antet = [curat(h).lower() for h in next(randuri)]

        def col(r, *nume):
            for n in nume:
                for i, h in enumerate(antet):
                    if h.startswith(n) and i < len(r):
                        return r[i]
            return None

        for r in randuri:
            if not any(v not in (None, "") for v in r):
                continue
            denumire = curat(col(r, "denumire"))
            nr = numar(col(r, "nr. inregistrare"))
            if not denumire and not nr:
                continue
            cod_txt = curat(col(r, "cod locatie"))
            cod, alias = normalizeaza_cod(cod_txt)
            tag = curat(col(r, "linie")).strip("[]").lower()
            tip = "teren" if tag == "teren" or tip_implicit == "teren" else "activ"
            lat, lng = coordonate(col(r, "locatie google"))
            detinere_txt = curat(col(r, "owned by")).lower()
            detinere = {"owned": "owned", "concesiune": "concesiune"}.get(detinere_txt, "altul" if detinere_txt else "")
            if "rent" in denumire.lower() or "inchiri" in detinere_txt:
                detinere = "inchiriat"
            obs = " ".join(x for x in (curat(col(r, "column2")), curat(col(r, "column1")), curat(col(r, "status contract"))) if x)
            valori = dict(
                cod_original=cod_txt, cod_alias=alias, data_inregistrare=data(col(r, "data")),
                denumire=denumire or "(fără denumire)", tip=tip, utilizator=curat(col(r, "utilizator")).strip("-"),
                proprietar=self.societate(col(r, "proprietar")), grup=curat(col(r, "grup")), sindicat=curat(col(r, "sindicat")),
                valoare_garantare_eur=numar(col(r, "valoare garantare")), data_raport_evaluare=data(col(r, "data raport")),
                adresa=curat(col(r, "adresa")), linie=tag if tag not in ("activ", "teren") else "",
                status=curat(col(r, "tip")), responsabil_activ=curat(col(r, "responsbil", "responsabil")),
                telefon=curat(col(r, "telefon")), carti_funciare=curat(col(r, "carte funciara")),
                coordonate=curat(col(r, "locatie google")), lat=lat, lng=lng, detinere=detinere,
                suprafata_construita_totala=numar(col(r, "suprafata construita totala")),
                suprafata_construita_productie=numar(col(r, "suprafata construita productie")),
                suprafata_teren=numar(col(r, "suprafata teren")), suprafata_constructii=numar(col(r, "suprafata constructii")),
                spatiu_cazare=curat(col(r, "spatiu cazare")), observatii=obs, sursa_import=ws.title.strip(),
            )
            existent = None
            if nr:
                existent = Locatie.objects.filter(nr_registru=int(nr)).first()
            if not existent and cod:
                existent = Locatie.objects.filter(cod=cod).first()
            if existent:
                for k, v in valori.items():
                    if v not in (None, "") and k != "sursa_import":
                        setattr(existent, k, v)
                if cod and not existent.cod and not Locatie.objects.filter(cod=cod).exists():
                    existent.cod = cod
                existent.save()
                self.stat["actualizate"] += 1
            else:
                if cod and Locatie.objects.filter(cod=cod).exists():
                    valori["observatii"] += f" [cod duplicat în Excel: {cod_txt}]"
                    cod = None
                Locatie.objects.create(cod=cod, nr_registru=int(nr) if nr else None, **valori)
                self.stat["create"] += 1

    def importa_obiective(self, ws):
        randuri = ws.iter_rows(values_only=True)
        antet = [curat(h).lower() for h in next(randuri)]
        idx = {h: i for i, h in enumerate(antet) if h}

        def col(r, nume):
            for h, i in idx.items():
                if h.startswith(nume) and i < len(r):
                    return r[i]
            return None

        for r in randuri:
            obiectiv = curat(col(r, "obiective"))
            if not obiectiv:
                continue
            cod_txt = curat(col(r, "cod locatie"))
            cod, alias = normalizeaza_cod(cod_txt)
            linie = curat(col(r, "linie"))
            status = curat(col(r, "tip"))
            existent = Locatie.objects.filter(cod=cod).first() if cod else Locatie.objects.filter(denumire__iexact=obiectiv).first()
            if existent:
                existent.obiectiv_functional = obiectiv
                if linie:
                    existent.linie = linie
                if not existent.status and status:
                    existent.status = status
                existent.save()
                self.stat["obiective_completate"] += 1
            else:
                Locatie.objects.create(
                    cod=cod, cod_original=cod_txt, cod_alias=alias, denumire=obiectiv, obiectiv_functional=obiectiv,
                    tip="obiectiv", proprietar=self.societate(col(r, "proprietar")), adresa=curat(col(r, "adresa")),
                    linie=linie, status=status, detinere="inchiriat" if obiectiv.upper().startswith("RENT") else "",
                    sursa_import="-", observatii=f"Status cod: {curat(col(r, 'status cod'))}".strip(": "),
                )
                self.stat["obiective_noi"] += 1

    def importa_liste(self, ws):
        randuri = list(ws.iter_rows(values_only=True))
        harta = {
            "linie afacere": "linie", "departament": "departament", "tip finantare": "tip_finantare",
            "status": "status_proiect", "faza proiect": "faza_proiect", "tip": "status_activ",
        }
        for i, h in enumerate(randuri[0]):
            tip = harta.get(curat(h).lower())
            if not tip:
                continue
            for ordine, r in enumerate(randuri[1:]):
                v = curat(r[i]) if i < len(r) else ""
                if v and v != "N/A":
                    Nomenclator.objects.get_or_create(tip=tip, valoare=v, defaults={"ordine": ordine})
                    self.stat[f"nomenclator_{tip}"] += 1

    def seteaza_prefixe(self):
        for soc in Societate.objects.filter(prefix_cod=""):
            prefixe = Counter(c.split(".")[0] for c in soc.locatii.exclude(cod__isnull=True).values_list("cod", flat=True))
            if prefixe:
                soc.prefix_cod = prefixe.most_common(1)[0][0]
                soc.save(update_fields=["prefix_cod"])
