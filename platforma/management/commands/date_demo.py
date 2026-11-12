"""Date demonstrative: utilizatori de test, cele două Rapoarte Zero reale (FFR.07.01, GFF.01.08) și un proiect cu finanțare.

Valorile de buget/contracte/facturi sunt DEMONSTRATIVE (marcate în observații), nu date reale.
Rulare: python manage.py date_demo [--pdf-dir "/Users/.../Desktop/ABC"]
"""
import datetime as dt
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction

from platforma import models as m
from platforma.hg import creeaza_linii

User = get_user_model()
D = dt.date
PAROLA_DEMO = "Demo2026!"
NOTA = "DATE DEMONSTRATIVE – a se înlocui cu datele reale."


class Command(BaseCommand):
    help = "Încarcă utilizatori și proiecte demonstrative"

    def add_arguments(self, parser):
        parser.add_argument("--pdf-dir", default="")

    def user(self, username, nume, prenume, rol, coordonator=None, superuser=False):
        u, creat = User.objects.get_or_create(username=username, defaults={"first_name": prenume, "last_name": nume, "is_staff": superuser, "is_superuser": superuser})
        if creat:
            u.set_password(PAROLA_DEMO)
            u.save()
        p = u.profil
        p.rol, p.coordonator = rol, coordonator
        p.save()
        return u

    @transaction.atomic
    def handle(self, pdf_dir, **opt):
        self.curs()
        admin = self.user("admin", "Administrator", "Platformă", "admin", superuser=True)
        mgmt = self.user("management", "Management", "Grup", "management")
        coord = self.user("coordonator", "Coordonator", "Investiții", "coordonator")
        pm1 = self.user("alin.ancuta", "Ancuța", "Alin", "pm", coordonator=coord)
        pm2 = self.user("octavian.paltanea", "Păltânea", "Octavian", "pm", coordonator=coord)
        fin = self.user("financiar", "Financiar", "Responsabil", "financiar")
        self.user("vizitator", "Utilizator", "Simplu", "utilizator")

        # ---------- FFR.07.01 (din Raport Zero real) ----------
        loc = m.Locatie.objects.get(cod="FFR.07")
        p1, creat = m.Proiect.objects.get_or_create(cod="FFR.07.01", defaults=dict(
            locatie=loc, nr_ordine=1, nr_inregistrare="FFR.20240320.1", denumire="Platformă betonată pentru dejecții",
            departament="Investitii", linie="Pui", beneficiar=loc.proprietar, solicitant="Alin Ancuța", responsabil_tehnic="Alin Ancuța",
            responsabil_implementare="Alin Ancuța", obiectiv="Bălcești, jud. Vâlcea", cladire="Nr. cadastral 36493 / UAT Bălcești",
            descriere_lucrari="Autorizări, prestări servicii și materiale necesare realizării unei platforme betonate.",
            valoare_rz=Decimal("200000"), moneda_rz="EUR", finantare="Fonduri Proprii", prioritate="mare",
            data_demarare=D(2024, 3, 1), data_finalizare=D(2024, 12, 31), intocmit_de="Alin Ancuța", avizat_de="Andrei Brumaru",
            stare_rz="aprobat", aprobat_de_user=admin, coordonator=coord, pm=pm1, status="In lucru", faza="Executie", progres_fizic=60,
            observatii=NOTA,
        ))
        if creat:
            p1.curs_rz = m.CursValutar.la_data(p1.data_demarare)
            p1.save()
            m.RevizieRaportZero.objects.create(proiect=p1, revizie=0, date=p1.snapshot_rz(), aprobat_de=admin)
            creeaza_linii(p1)
            self.buget(p1, {"3.2": 15000, "3.5": 45000, "4.1": 850000, "5.1": 20000, "5.3": 40000})
            c = m.Contract.objects.create(proiect=p1, tip="executie", numar="DEMO-12",
                                          data=D(2024, 5, 10), partener="Constructor Demo SRL", valoare=Decimal("160000"), moneda="EUR",
                                          termen_executie=D(2024, 11, 30), termen_plata_zile=30, obiect="Execuție platformă betonată. " + NOTA)
            m.ContractAlocare.objects.create(contract=c, linie_buget=p1.linii_buget.get(capitol="4.1"), valoare_ron=c.valoare_ron)
            f1 = m.Factura.objects.create(proiect=p1, contract=c, furnizor=c.partener, numar="CD-1001", data=D(2024, 7, 15), valoare=Decimal("280000"), tva=Decimal("53200"))
            self.plata(f1, D(2024, 8, 14), "280000", "OP 4521", pm1, coord, fin)
            f2 = m.Factura.objects.create(proiect=p1, contract=c, furnizor=c.partener, numar="CD-1044", data=D(2024, 10, 20), valoare=Decimal("310000"), tva=Decimal("58900"))
            self.plata(f2, D(2024, 11, 19), "150000", "OP 4980", pm1, coord, fin)
            m.PlanAnual.objects.create(proiect=p1, an=2024, valoare_estimata=Decimal("900000"))
            m.PlanAnual.objects.create(proiect=p1, an=2025, valoare_estimata=Decimal("110000"))
            p1.buget_blocat, p1.buget_blocat_de, p1.buget_blocat_la = True, admin, dt.datetime(2024, 3, 15, tzinfo=dt.timezone.utc)
            p1.save()
            for den, st in (("Aviz de gospodărire a apelor", "obtinut"), ("Act administrativ APM", "obtinut"), ("Aviz DSVSA", "depus")):
                m.Aviz.objects.create(proiect=p1, denumire=den, status=st, responsabil=pm1)
            self.atașeaza(p1, pdf_dir, "Raport Zero FFR.07.01.pdf", pm1)

        # ---------- GFF.01.08 (din Raport Zero real) ----------
        loc = m.Locatie.objects.get(cod="GFF.01")
        p2, creat = m.Proiect.objects.get_or_create(cod="GFF.01.08", defaults=dict(
            locatie=loc, nr_ordine=8, nr_inregistrare="GFF.20260211.3", denumire="Construcție fermă pilot prefabricat beton",
            departament="Investitii", linie="Pui", beneficiar=loc.proprietar, solicitant="Octavian Păltânea", obiectiv="Potcoava, Olt",
            cladire="Clădire nouă",
            descriere_lucrari="Realizare clădire nouă din beton prefabricat.\nAchiziție echipamente producție broiler.\nRealizare branșamente utilități pentru hală.",
            valoare_rz=Decimal("900000"), moneda_rz="EUR", finantare="Fonduri Proprii", prioritate="mare",
            data_demarare=D(2026, 2, 12), data_finalizare=D(2026, 9, 30), intocmit_de="Octavian Păltânea", avizat_de="Andrei Brumaru",
            stare_rz="aprobat", aprobat_de_user=admin, coordonator=coord, pm=pm2, status="In lucru", faza="Executie", progres_fizic=45,
            observatii=NOTA,
        ))
        if creat:
            p2.curs_rz = m.CursValutar.la_data(p2.data_demarare)
            p2.save()
            m.RevizieRaportZero.objects.create(proiect=p2, revizie=0, date=p2.snapshot_rz(), aprobat_de=admin)
            creeaza_linii(p2)
            self.buget(p2, {"2.1": 250000, "3.2": 30000, "3.5": 120000, "3.8": 45000, "4.1": 2600000, "4.3": 1300000, "5.1": 60000, "5.3": 180000})
            c1 = m.Contract.objects.create(proiect=p2, tip="proiectare", numar="ST-2026-07",
                                           data=D(2026, 2, 20), partener="Star Trading", valoare=Decimal("115000"), termen_executie=D(2026, 4, 15),
                                           obiect="Proiectare DTAC + PT. " + NOTA)
            m.ContractAlocare.objects.create(contract=c1, linie_buget=p2.linii_buget.get(capitol="3.5"), valoare_ron=c1.valoare_ron)
            c2 = m.Contract.objects.create(proiect=p2, tip="furnizare", numar="INNO-26-114",
                                           data=D(2026, 3, 10), partener="INNO AG", valoare=Decimal("270000"), moneda="EUR",
                                           termen_executie=D(2026, 8, 31), termen_plata_zile=45, garantie_buna_executie="10% scrisoare bancară",
                                           perioada_garantie_luni=24, obiect="Hală prefabricată și echipamente broiler. " + NOTA)
            # contract repartizat pe două linii: hala (construcții) și echipamentele
            hala = (c2.valoare_ron * Decimal("0.6")).quantize(Decimal("0.01"))
            m.ContractAlocare.objects.create(contract=c2, linie_buget=p2.linii_buget.get(capitol="4.1"), valoare_ron=hala)
            m.ContractAlocare.objects.create(contract=c2, linie_buget=p2.linii_buget.get(capitol="4.3"), valoare_ron=c2.valoare_ron - hala)
            m.ActAditional.objects.create(contract=c2, numar="1", data=D(2026, 6, 2), obiect="Suplimentare sistem ventilație", diferenta_ron=Decimal("95000"), termen_nou=D(2026, 9, 15))
            m.ConditieContract.objects.create(contract=c2, tip="avans", descriere="Avans 30% la semnare, contra scrisoare de garanție de avans", valoare="30%")
            m.ConditieContract.objects.create(contract=c2, tip="penalitate", descriere="Penalități de întârziere 0,1%/zi din valoarea nelivrată, max. 10%", valoare="0,1%/zi")
            f = m.Factura.objects.create(proiect=p2, contract=c1, furnizor="Star Trading", numar="ST 0331", data=D(2026, 4, 20), valoare=Decimal("115000"), tva=Decimal("24150"))
            self.plata(f, D(2026, 5, 15), "115000", "OP 1203", pm2, coord, fin)
            f = m.Factura.objects.create(proiect=p2, contract=c2, furnizor="INNO AG", numar="INV-26-0912", data=D(2026, 3, 15), valoare=Decimal("81000"), moneda="EUR")
            self.plata(f, D(2026, 3, 25), f.valoare_ron, "OP 1102", pm2, coord, fin)
            f = m.Factura.objects.create(proiect=p2, contract=c2, furnizor="INNO AG", numar="INV-26-1530", data=D(2026, 10, 20), valoare=Decimal("95000"), moneda="EUR", tva=Decimal("0"))
            m.Plata.objects.create(factura=f, data=D(2026, 11, 20), suma_ron=f.valoare_ron, propusa_de=pm2, observatii="Tranșa 2 hală – " + NOTA)
            # factură repartizată pe două linii de buget, fără contract
            f = m.Factura.objects.create(proiect=p2, furnizor="Distribuție Energie (demo)", numar="DE-88120", data=D(2026, 7, 10), valoare=Decimal("180000"))
            m.FacturaAlocare.objects.create(factura=f, linie_buget=p2.linii_buget.get(capitol="2.1"), valoare_ron=Decimal("150000"))
            m.FacturaAlocare.objects.create(factura=f, linie_buget=p2.linii_buget.get(capitol="5.1"), valoare_ron=Decimal("30000"))
            pl = m.Plata.objects.create(factura=f, data=D(2026, 11, 12), suma_ron=Decimal("180000"), propusa_de=pm2, stare="aprobata_tehnic",
                                        aprobat_tehnic_de=coord, aprobat_tehnic_la=dt.datetime(2026, 11, 5, 9, tzinfo=dt.timezone.utc))
            # factură fără contract: taxă autorizație de construire
            f = m.Factura.objects.create(proiect=p2, furnizor="Primăria Potcoava", numar="CH-1187", data=D(2026, 5, 20), valoare=Decimal("26500"), tva=Decimal("0"),
                                         observatii="Taxă autorizație de construire – " + NOTA)
            m.FacturaAlocare.objects.create(factura=f, linie_buget=p2.linii_buget.get(capitol="5.2"), valoare_ron=Decimal("26500"))
            # factură intragrup
            soc_grup = m.Societate.objects.filter(denumire__icontains="Avirom").first()
            f = m.Factura.objects.create(proiect=p2, furnizor=soc_grup.denumire if soc_grup else "Societate grup", furnizor_societate=soc_grup,
                                         numar="AVR-2026-041", data=D(2026, 9, 2), valoare=Decimal("42000"), observatii="Refacturare intragrup – " + NOTA)
            m.FacturaAlocare.objects.create(factura=f, linie_buget=p2.linii_buget.get(capitol="5.1"), valoare_ron=Decimal("30000"))
            for an, v in ((2026, "4300000"), (2027, "285000")):
                m.PlanAnual.objects.create(proiect=p2, an=an, valoare_estimata=Decimal(v))
            p2.prag_aprobare_suplimentara, p2.aprobator_suplimentar = Decimal("400000"), admin
            p2.buget_blocat, p2.buget_blocat_de, p2.buget_blocat_la = True, admin, dt.datetime(2026, 2, 25, tzinfo=dt.timezone.utc)
            p2.save()
            cu = m.CertificatUrbanism.objects.create(proiect=p2, numar="DEMO-14", data=D(2026, 1, 20), emitent="Primăria Potcoava", valabil_pana=D(2027, 1, 20), scop="Construire hală prefabricată")
            for den, st, nr in (("Aviz alimentare cu energie electrică", "obtinut", "7012"), ("Aviz alimentare cu apă", "obtinut", "118"),
                                ("Aviz securitate la incendiu (ISU)", "depus", ""), ("Act administrativ APM", "obtinut", "45"),
                                ("Aviz sanitar-veterinar (DSVSA)", "in_pregatire", ""), ("Notificare DSP", "necesar", "")):
                m.Aviz.objects.create(proiect=p2, certificat=cu, denumire=den, status=st, numar_aviz=nr, responsabil=pm2,
                                      data_aviz=D(2026, 3, 1) if st == "obtinut" else None, valabil_pana=D(2026, 11, 25) if nr == "118" else None)
            for i, (den, s, e, pr) in enumerate((
                ("Proiectare DTAC + PT", D(2026, 2, 20), D(2026, 4, 15), 100), ("Obținere avize", D(2026, 3, 1), D(2026, 5, 30), 70),
                ("Autorizație de construire", D(2026, 5, 15), D(2026, 6, 15), 100), ("Fundații și platformă", D(2026, 6, 16), D(2026, 7, 31), 80),
                ("Montaj hală prefabricată", D(2026, 8, 1), D(2026, 9, 10), 20), ("Echipamente broiler", D(2026, 8, 20), D(2026, 9, 25), 0),
                ("Branșamente utilități", D(2026, 7, 1), D(2026, 9, 20), 30), ("Recepție și punere în funcțiune", D(2026, 9, 26), D(2026, 10, 15), 0),
            )):
                a = m.Activitate.objects.create(proiect=p2, ordine=i + 1, denumire=den, start=s, sfarsit=e, progres=pr)
                if i:
                    a.depinde_de.add(prev)
                prev = a
            self.atașeaza(p2, pdf_dir, "Raport zero GFF.01.08.pdf", pm2)

        # ---------- BIO.03.01 – exemplu cu fonduri nerambursabile ----------
        loc = m.Locatie.objects.get(cod="BIO.03")
        p3, creat = m.Proiect.objects.get_or_create(cod="BIO.03.01", defaults=dict(
            locatie=loc, nr_ordine=1, nr_inregistrare="BIO.20261015.1", denumire="Fabrică pet food Turburea (exemplu demonstrativ)",
            departament="Proiecte", linie="Others", beneficiar=loc.proprietar, solicitant="Coordonator Investiții",
            descriere_lucrari="Exemplu demonstrativ pentru un proiect cu finanțare nerambursabilă.", valoare_rz=Decimal("12500000"),
            moneda_rz="RON", finantare="Fonduri  Nerambursabile", prioritate="medie", data_demarare=D(2026, 11, 1), data_finalizare=D(2028, 6, 30),
            intocmit_de="Coordonator Investiții", stare_rz="trimis", coordonator=coord, faza="In scriere", status="In lucru", observatii=NOTA,
        ))
        if creat:
            creeaza_linii(p3)
            fi = m.Finantare.objects.create(proiect=p3, tip="nerambursabila", finantator="Program demonstrativ (ex. PNS / AFIR)", valoare_ron=Decimal("12500000"),
                                            valoare_eligibila_ron=Decimal("11000000"), procent_nerambursabil=Decimal("50"),
                                            data_limita_implementare=D(2028, 6, 30), perioada_monitorizare_ani=5, responsabil=fin)
            for desc, t, r in (("Depunere cerere de finanțare", D(2026, 12, 15), "o singură dată"),
                               ("Raport de progres trimestrial", D(2027, 4, 15), "trimestrial"),
                               ("Menținerea investiției și a locurilor de muncă în perioada de monitorizare", D(2033, 6, 30), "anual"),
                               ("Panou de publicitate pe șantier", D(2027, 3, 1), "o singură dată")):
                m.Obligatie.objects.create(proiect=p3, finantare=fi, descriere=desc, termen=t, recurenta=r, responsabil=fin)
            m.AccesProiect.objects.create(proiect=p3, user=fin, nivel="editare")
            for an, v in ((2026, "300000"), (2027, "7200000"), (2028, "5000000")):
                m.PlanAnual.objects.create(proiect=p3, an=an, valoare_estimata=Decimal(v))

        self.stdout.write(self.style.SUCCESS(
            f"Date demo încărcate. Utilizatori: admin, management, coordonator, alin.ancuta, octavian.paltanea, financiar, vizitator (parola {PAROLA_DEMO})."
        ))

    def plata(self, factura, data, suma, op, pm, coord, fin):
        t = dt.datetime.combine(data, dt.time(10), tzinfo=dt.timezone.utc)
        return m.Plata.objects.create(factura=factura, data=data, suma_ron=Decimal(suma), document_plata=op, stare="platita",
                                      propusa_de=pm, aprobat_tehnic_de=coord, aprobat_tehnic_la=t, aprobat_financiar_de=fin, aprobat_financiar_la=t)

    def buget(self, p, valori):
        for cap, v in valori.items():
            p.linii_buget.filter(capitol=cap).update(valoare_aprobata=Decimal(v))

    def atașeaza(self, p, pdf_dir, nume, user):
        cale = Path(pdf_dir) / nume if pdf_dir else None
        if cale and cale.exists():
            with cale.open("rb") as fh:
                m.Document.objects.create(proiect=p, categorie="raport_zero", titlu=f"Raport Zero {p.cod} semnat", fisier=File(fh, name=nume), incarcat_de=user)

    def curs(self):
        if m.CursValutar.objects.exists():
            return
        try:
            from platforma.curs import actualizeaza
            for an in (2024, 2025, 2026):
                actualizeaza(an)
            self.stdout.write(f"Curs BNR importat: {m.CursValutar.objects.count()} zile.")
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"BNR indisponibil ({e}); folosesc cursuri aproximative."))
            for d, v in ((D(2024, 3, 1), "4.9710"), (D(2025, 1, 2), "4.9743"), (D(2026, 1, 5), "5.0900")):
                m.CursValutar.objects.create(data=d, eur=Decimal(v), sursa="manual (aprox.)")
