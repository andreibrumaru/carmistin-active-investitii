"""Modelul de date al platformei.

Lanțul de trasabilitate:
  Locație (cod BIO.03) → Proiect / Raport Zero (BIO.03.01) → Linii buget HG
  → Contracte (+ acte adiționale, condiții) → Facturi → Plăți
  și, în paralel: Certificat urbanism → Avize; Finanțare → Obligații; Grafic (activități); Documente.
Fiecare modificare se înregistrează în JurnalAudit; documentele nu se șterg, doar se înlocuiesc cu versiuni noi.
"""
import datetime
import hashlib
import re
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

User = settings.AUTH_USER_MODEL
ZERO = Decimal("0")


def suma(qs, camp):
    return qs.aggregate(s=Sum(camp))["s"] or ZERO


# ---------------------------------------------------------------------------
# Utilizatori și roluri
# ---------------------------------------------------------------------------
class Profil(models.Model):
    ROLURI = [
        ("admin", "Administrator"),
        ("management", "Management (vede tot, citire)"),
        ("coordonator", "Coordonator"),
        ("pm", "Project manager"),
        ("financiar", "Responsabil financiar / finanțare"),
        ("utilizator", "Utilizator"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profil")
    rol = models.CharField(max_length=20, choices=ROLURI, default="utilizator")
    coordonator = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="pm_subordonati",
        help_text="Pentru project manageri: coordonatorul care le vede proiectele.",
    )
    telefon = models.CharField(max_length=40, blank=True)
    functie = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name = "profil utilizator"
        verbose_name_plural = "profiluri utilizatori"

    def __str__(self):
        return f"{self.user} ({self.get_rol_display()})"


# ---------------------------------------------------------------------------
# Nomenclatoare
# ---------------------------------------------------------------------------
class Societate(models.Model):
    denumire = models.CharField(max_length=200, unique=True)
    cui = models.CharField("CUI", max_length=20, blank=True)
    prefix_cod = models.CharField("cod firmă", max_length=10, blank=True,
                                  help_text="2–5 litere, ex. Avicarvil SRL → AVI. Activele firmei primesc coduri AVI.01, AVI.02 …")
    cod_erp = models.CharField("cod societate în Charisma", max_length=30, blank=True)
    in_grup = models.BooleanField("aparține grupului", default=True)

    class Meta:
        ordering = ["denumire"]
        verbose_name = "societate (firmă din grup)"
        verbose_name_plural = "societăți (firmele grupului)"

    def __str__(self):
        return self.denumire


class ProprietarExtern(models.Model):
    """Proprietarii din afara grupului de la care grupul închiriază active (bază separată de firmele grupului).
    Lista o întreține administratorul."""
    TIP = [("pj", "Persoană juridică"), ("pf", "Persoană fizică"), ("uat", "Instituție publică / UAT")]
    denumire = models.CharField(max_length=200, unique=True)
    tip = models.CharField(max_length=5, choices=TIP, default="pj")
    cui = models.CharField("CUI / CIF", max_length=20, blank=True)
    adresa = models.CharField(max_length=300, blank=True)
    persoana_contact = models.CharField("persoană de contact", max_length=120, blank=True)
    telefon = models.CharField(max_length=60, blank=True)
    email = models.EmailField("e-mail", blank=True)
    observatii = models.TextField(blank=True)
    activ = models.BooleanField(default=True, help_text="Debifat = nu mai apare în lista de selecție")
    creat_la = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["denumire"]
        verbose_name = "proprietar extern"
        verbose_name_plural = "proprietari externi (închirieri externe)"

    def __str__(self):
        return self.denumire


class Nomenclator(models.Model):
    TIPURI = [
        ("linie", "Linie de afacere"),
        ("divizie", "Divizie (în cadrul liniei)"),
        ("departament", "Departament"),
        ("tip_finantare", "Tip finanțare"),
        ("status_proiect", "Status proiect"),
        ("faza_proiect", "Fază proiect"),
        ("status_activ", "Status activ"),
        ("tip_document", "Categorie document"),
    ]
    tip = models.CharField(max_length=30, choices=TIPURI)
    valoare = models.CharField(max_length=120)
    grup = models.CharField("aparține de (ex. linia diviziei)", max_length=120, blank=True)
    ordine = models.PositiveIntegerField(default=0)
    activ = models.BooleanField(default=True)

    class Meta:
        ordering = ["tip", "ordine", "valoare"]
        unique_together = [("tip", "valoare")]
        verbose_name = "valoare nomenclator"
        verbose_name_plural = "nomenclatoare"

    def __str__(self):
        return self.valoare

    @classmethod
    def optiuni(cls, tip):
        return cls.objects.filter(tip=tip, activ=True)


class CursValutar(models.Model):
    data = models.DateField(unique=True)
    eur = models.DecimalField("RON pentru 1 EUR", max_digits=10, decimal_places=4)
    sursa = models.CharField(max_length=40, default="BNR")

    class Meta:
        ordering = ["-data"]
        verbose_name = "curs EUR"
        verbose_name_plural = "cursuri EUR (BNR)"

    def __str__(self):
        return f"{self.data}: {self.eur}"

    @classmethod
    def la_data(cls, data=None):
        data = data or timezone.localdate()
        c = cls.objects.filter(data__lte=data).first() or cls.objects.order_by("data").first()
        return c.eur if c else Decimal(str(settings.CURS_EUR_IMPLICIT))


# ---------------------------------------------------------------------------
# Registrul activelor
# ---------------------------------------------------------------------------
JUDETE = ["Alba", "Arad", "Argeș", "Bacău", "Bihor", "Bistrița-Năsăud", "Botoșani", "Brăila", "Brașov", "București", "Buzău",
          "Călărași", "Caraș-Severin", "Cluj", "Constanța", "Covasna", "Dâmbovița", "Dolj", "Galați", "Giurgiu", "Gorj", "Harghita",
          "Hunedoara", "Ialomița", "Iași", "Ilfov", "Maramureș", "Mehedinți", "Mureș", "Neamț", "Olt", "Prahova", "Sălaj", "Satu Mare",
          "Sibiu", "Suceava", "Teleorman", "Timiș", "Tulcea", "Vâlcea", "Vaslui", "Vrancea"]


def _fara_diacritice(t):
    import unicodedata
    return unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()


_JUDETE_ASCII = {_fara_diacritice(j): j for j in JUDETE}
# localități frecvente în registru, pentru adresele fără „jud.”
ORASE_JUDET = {
    "ploiesti": "Prahova", "ploiestiori": "Prahova", "blejoi": "Prahova", "mizil": "Prahova", "campina": "Prahova",
    "slatina": "Olt", "plesoiu": "Olt", "potcoava": "Olt", "caracal": "Olt", "craiova": "Dolj", "segarcea": "Dolj",
    "tg\\.? ?jiu": "Gorj", "targu jiu": "Gorj", "turburea": "Gorj", "tantareni": "Gorj", "gaesti": "Dâmbovița",
    "targoviste": "Dâmbovița", "ianca": "Brăila", "francesti": "Vâlcea", "babeni": "Vâlcea", "balcesti": "Vâlcea",
    "ramnicu valcea": "Vâlcea", "rm\\.? ?valcea": "Vâlcea", "pitesti": "Argeș", "mavrodin": "Teleorman", "videle": "Teleorman",
    "deva": "Hunedoara", "mihailesti": "Giurgiu", "jilava": "Ilfov", "buzau": "Buzău", "braila": "Brăila",
    "lisa": "Teleorman", "zimnicea": "Teleorman", "scornicesti": "Olt", "verguleasa": "Olt", "oltenita": "Călărași",
    "baba ana": "Prahova", "ciresanu": "Prahova", "sinaia": "Prahova", "paulesti": "Prahova", "bilciuresti": "Dâmbovița",
    "fracesti": "Vâlcea",
}


def judet_din_adresa(adresa):
    """Extrage județul din textul adresei (ex. „Loc. Olanu, Jud Valcea” → „Vâlcea”)."""
    t = _fara_diacritice(adresa)
    if not t:
        return ""
    m = re.search(r"\bjud(?:etul|etu|et)?\.?\s*([a-z][a-z\- ]{2,20})", t)
    if m:
        cand = m.group(1).strip()
        for cheie, j in sorted(_JUDETE_ASCII.items(), key=lambda x: -len(x[0])):
            if cand.startswith(cheie):
                return j
    if re.search(r"\bbucuresti\b|\bsector(ul)?\s*\d", t):
        return "București"
    for oras, j in ORASE_JUDET.items():
        if re.search(rf"\b{oras}\b", t):
            return j
    for cheie, j in sorted(_JUDETE_ASCII.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(cheie)}\b", t):
            return j
    return ""


class Locatie(models.Model):
    TIP = [("activ", "Activ"), ("teren", "Teren"), ("obiectiv", "Obiectiv funcțional")]
    STARE = [("functional", "Activ funcțional"), ("nefunctional", "Activ nefuncțional"), ("teren", "Teren investiții"), ("retail", "Retail")]
    DETINERE = [("owned", "Proprietate"), ("concesiune", "Concesiune"), ("inchiriat", "Închiriat (RENT)"), ("altul", "Altul")]

    cod = models.CharField("cod locație", max_length=30, unique=True, null=True, blank=True,
                           help_text="Format: PREFIX.NN (ex. BIO.03). Gol = cod nealocat încă.")
    cod_original = models.CharField("cod în Excel", max_length=60, blank=True)
    cod_alias = models.CharField("coduri alternative", max_length=60, blank=True)
    nr_registru = models.PositiveIntegerField("nr. înregistrare", null=True, blank=True, unique=True)
    data_inregistrare = models.DateField(null=True, blank=True)
    denumire = models.CharField("denumire activ", max_length=255)
    tip = models.CharField(max_length=20, choices=TIP, default="activ")
    stare_activ = models.CharField("categorie activ", max_length=15, choices=STARE, default="functional",
                                   help_text="Se schimbă doar de administrator; fiecare schimbare rămâne în istoricul activului")
    utilizator = models.CharField(max_length=200, blank=True)
    proprietar = models.ForeignKey(Societate, null=True, blank=True, on_delete=models.PROTECT, related_name="locatii",
                                   help_text="Firma din grup care deține locația; la închirierea externă, firma din grup care o închiriază")
    inchiriere_externa = models.BooleanField("închiriere externă", default=False,
                                             help_text="Activ închiriat de grup de la un proprietar din afara grupului")
    proprietar_extern = models.ForeignKey(ProprietarExtern, null=True, blank=True, on_delete=models.PROTECT, related_name="locatii",
                                          verbose_name="proprietar extern")
    contract_inchiriere = models.CharField("contract de închiriere (nr. / dată)", max_length=120, blank=True)
    chirie_lunara = models.DecimalField("chirie lunară (fără TVA)", max_digits=14, decimal_places=2, null=True, blank=True)
    moneda_chirie = models.CharField("moneda chiriei", max_length=3, choices=[("RON", "RON"), ("EUR", "EUR")], default="EUR")
    inchiriere_expira = models.DateField("contract valabil până la", null=True, blank=True)
    grup = models.CharField("grup / non grup", max_length=20, blank=True)
    sindicat = models.CharField("sindicat / non sindicat", max_length=30, blank=True)
    valoare_garantare_eur = models.DecimalField("valoare garantare (EUR)", max_digits=16, decimal_places=2, null=True, blank=True)
    data_raport_evaluare = models.DateField(null=True, blank=True)
    adresa = models.CharField(max_length=400, blank=True)
    linie = models.CharField("linie afacere", max_length=60, blank=True)
    divizie = models.CharField("divizie", max_length=60, blank=True, help_text="ex. Reproducție, Incubație, Broiler (în cadrul liniei)")
    status = models.CharField(max_length=60, blank=True)
    obiectiv_functional = models.CharField(max_length=255, blank=True)
    responsabil_activ = models.CharField("responsabil locație", max_length=120, blank=True)
    responsabil_functie = models.CharField("funcția responsabilului", max_length=120, blank=True)
    telefon = models.CharField(max_length=60, blank=True)
    responsabil_email = models.EmailField("e-mail responsabil", blank=True)
    urmareste_autorizatii = models.BooleanField("urmărește lista standard de autorizații", null=True, blank=True,
                                                help_text="Gol = automat (doar activele funcționale); Da / Nu = forțat")
    consolideaza_valoare = models.BooleanField("include valoarea de piață în valoarea grupului", null=True, blank=True,
                                               help_text="Gol = automat (doar dacă proprietarul e o societate din grup); Da / Nu = forțat")
    prezentare = models.TextField("prezentare succintă a obiectivului", blank=True,
                                  help_text="Activitate, capacitate, clădiri principale, utilități etc.")
    carti_funciare = models.TextField("cărți funciare / nr. cadastrale", blank=True)
    coordonate = models.CharField("locație Google (text)", max_length=80, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    detinere = models.CharField("deținere", max_length=20, choices=DETINERE, blank=True)
    suprafata_construita_totala = models.DecimalField("supr. construită totală (mp)", max_digits=14, decimal_places=2, null=True, blank=True)
    suprafata_construita_productie = models.DecimalField("supr. construită producție (mp)", max_digits=14, decimal_places=2, null=True, blank=True)
    suprafata_teren = models.DecimalField("supr. teren (mp)", max_digits=14, decimal_places=2, null=True, blank=True)
    suprafata_constructii = models.DecimalField("supr. construcții (mp)", max_digits=14, decimal_places=2, null=True, blank=True)
    spatiu_cazare = models.CharField(max_length=120, blank=True)
    observatii = models.TextField(blank=True)
    sursa_import = models.CharField(max_length=120, blank=True)
    creat_la = models.DateTimeField(auto_now_add=True)
    modificat_la = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cod", "denumire"]
        verbose_name = "locație / activ"
        verbose_name_plural = "registrul activelor"

    def __str__(self):
        return f"{self.cod or '—'} · {self.denumire}"

    def get_absolute_url(self):
        return reverse("locatie_detaliu", args=[self.pk])

    def save(self, *args, **kwargs):
        if self.stare_activ == "teren":
            self.tip = "teren"
        elif self.tip == "teren":
            self.tip = "activ"
        super().save(*args, **kwargs)

    @property
    def proprietar_afisat(self):
        """Cine deține activul: proprietarul extern la închirieri externe, altfel firma din grup."""
        return self.proprietar_extern if self.inchiriere_externa else self.proprietar

    @property
    def chirie_lunara_ron(self):
        if not self.chirie_lunara:
            return None
        return self.chirie_lunara if self.moneda_chirie == "RON" else (self.chirie_lunara * CursValutar.la_data()).quantize(Decimal("0.01"))

    @property
    def zile_pana_expira(self):
        return (self.inchiriere_expira - timezone.localdate()).days if self.inchiriere_expira else None

    @property
    def prefix(self):
        return (self.cod or "").split(".")[0]

    @property
    def judet(self):
        return judet_din_adresa(self.adresa)

    @property
    def in_lista_autorizatii(self):
        """Lista standard de autorizații se aplică activelor funcționale (sau celor activate manual)."""
        if self.urmareste_autorizatii is not None:
            return self.urmareste_autorizatii
        return self.stare_activ in ("functional", "retail")

    @property
    def evaluare_curenta(self):
        """Ultimul raport de evaluare care are valoarea de piață completată."""
        for e in self.rapoarte_evaluare.all():  # ordonate descrescător după dată
            if e.valoare_piata:
                return e
        return None

    @property
    def regula_valoare_grup(self):
        """(se include?, motiv) – valoarea de piață intră în valoarea consolidată doar pentru activele deținute de grup.
        Activele deținute de grup și închiriate între firmele grupului se adună o singură dată (la proprietar);
        cele doar exploatate de grup prin contracte cu terți nu se adună."""
        if self.consolideaza_valoare is not None:
            return self.consolideaza_valoare, "setat manual: " + ("se include" if self.consolideaza_valoare else "nu se include")
        if self.inchiriere_externa:
            return False, f"închiriat de la un proprietar extern ({self.proprietar_extern or 'necompletat'})"
        if not self.proprietar_id:
            return False, "proprietar necompletat"
        if not self.proprietar.in_grup:
            return False, "proprietar din afara grupului – activ exploatat prin contract"
        if self.detinere in ("inchiriat", "concesiune"):
            return True, "deținut de o societate din grup, închiriat intragrup"
        return True, "deținut de o societate din grup"

    def valoare_piata(self, moneda="EUR"):
        """Valoarea de piață din ultimul raport, convertită la cursul BNR curent."""
        e = self.evaluare_curenta
        if not e:
            return None
        if e.moneda == moneda:
            return e.valoare_piata
        curs = CursValutar.la_data()
        return e.valoare_piata * curs if moneda == "RON" else e.valoare_piata / curs

    def urmatorul_cod_proiect(self):
        if not self.cod:
            return None
        n = (self.proiecte.aggregate(m=models.Max("nr_ordine"))["m"] or 0) + 1
        return f"{self.cod}.{n:02d}", n

    @property
    def valoare_investita_ron(self):
        return suma(Plata.objects.filter(factura__proiect__locatie=self, stare="platita"), "suma_ron")


class ContractFolosinta(models.Model):
    """Dreptul de folosință al activului: proprietate (act + anexe), chirie, superficie, concesiune, arendă, comodat, alte forme.
    Documentul principal are categoria „folosinta”; anexele lui (sublista) au categoria „anexa_drept”."""
    TIP = [
        ("proprietate", "Proprietate (contract / act de proprietate)"), ("inchiriere", "Contract de închiriere (chirie)"),
        ("superficie", "Contract de superficie"), ("concesiune", "Contract de concesiune"), ("arenda", "Contract de arendă"),
        ("comodat", "Contract de comodat"), ("altul", "Altă formă"),
    ]
    TIP_SCURT = {"proprietate": "Proprietate", "inchiriere": "Chirie", "superficie": "Superficie", "concesiune": "Concesiune",
                 "arenda": "Arendă", "comodat": "Comodat", "altul": "Altă formă"}
    CU_PLATA = ("inchiriere", "superficie", "concesiune", "arenda")
    PLATA = {"inchiriere": "chirie", "superficie": "superficie", "concesiune": "redevență", "arenda": "arendă"}
    PERIOADA = [("lunar", "lunar"), ("anual", "anual"), ("total", "total")]
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="contracte_folosinta")
    tip = models.CharField("forma dreptului de folosință", max_length=20, choices=TIP, default="proprietate")
    denumire_act = models.CharField("denumirea actului", max_length=200, blank=True,
                                    help_text="ex. contract de vânzare-cumpărare, certificat de moștenitor, hotărâre judecătorească")
    numar = models.CharField("nr. act / contract", max_length=60)
    data = models.DateField("data actului")
    contraparte = models.CharField("cealaltă parte (vânzător / locator / concedent)", max_length=200)
    folosit_de = models.CharField("titular / folosit de (societatea din grup)", max_length=200, blank=True)
    data_inceput = models.DateField("valabil de la", null=True, blank=True)
    data_sfarsit = models.DateField("valabil până la", null=True, blank=True)
    chirie = models.DecimalField("valoarea lunară a plății", max_digits=14, decimal_places=2, null=True, blank=True,
                                 help_text="chiria / superficia / redevența / arenda pe lună")
    moneda = models.CharField(max_length=3, choices=[("RON", "RON"), ("EUR", "EUR")], default="RON")
    perioada = models.CharField("plătită", max_length=10, choices=PERIOADA, default="lunar", editable=False)
    observatii = models.TextField(blank=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "drept de folosință"
        verbose_name_plural = "drepturi de folosință"

    def __str__(self):
        return f"{self.denumire_act or self.TIP_SCURT.get(self.tip, '')} nr. {self.numar}/{self.data:%d.%m.%Y}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.tip in self.CU_PLATA:
            erori = {}
            if not self.data_inceput:
                erori["data_inceput"] = "Obligatoriu pentru chirie, superficie, concesiune și arendă."
            if not self.data_sfarsit:
                erori["data_sfarsit"] = "Obligatoriu pentru chirie, superficie, concesiune și arendă."
            if self.chirie in (None, ""):
                erori["chirie"] = f"Introduceți valoarea lunară a {self.PLATA[self.tip]}i." if self.tip != "concesiune" else "Introduceți redevența lunară."
            if self.data_inceput and self.data_sfarsit and self.data_sfarsit < self.data_inceput:
                erori["data_sfarsit"] = "Data de sfârșit este înaintea datei de început."
            if erori:
                raise ValidationError(erori)

    @property
    def cu_plata(self):
        return self.tip in self.CU_PLATA

    @property
    def denumire_plata(self):
        return self.PLATA.get(self.tip, "plată")

    @property
    def activ(self):
        return not self.data_sfarsit or self.data_sfarsit >= timezone.localdate()

    @property
    def zile_ramase(self):
        return (self.data_sfarsit - timezone.localdate()).days if self.data_sfarsit else None

    @property
    def scan(self):
        return self.documente.filter(arhivat=False, categorie="folosinta").first()

    @property
    def anexe(self):
        return self.documente.filter(arhivat=False, categorie="anexa_drept").order_by("incarcat_la")


class IstoricStareActiv(models.Model):
    """Schimbările de stare ale unui activ (funcțional / nefuncțional / retail) – nu se modifică și nu se șterg."""
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="istoric_stare")
    stare_veche = models.CharField(max_length=15, choices=Locatie.STARE, blank=True)
    stare_noua = models.CharField(max_length=15, choices=Locatie.STARE)
    data = models.DateField("data schimbării", default=timezone.localdate)
    motiv = models.TextField(blank=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    creat_la = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-creat_la"]
        verbose_name = "schimbare stare activ"
        verbose_name_plural = "istoric stări active"

    def __str__(self):
        return f"{self.locatie}: {self.get_stare_veche_display() or '—'} → {self.get_stare_noua_display()}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Istoricul stărilor nu se modifică.")
        super().save(*args, **kwargs)


class CarteFunciara(models.Model):
    """Cartea funciară a unui activ; extrasul CF (PDF) se atașează ca document versionat."""
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="carti")
    numar = models.CharField("nr. carte funciară", max_length=40)
    uat = models.CharField("UAT", max_length=120, blank=True)
    nr_cadastral = models.CharField("nr. cadastral / topografic", max_length=120, blank=True)
    suprafata_mp = models.DecimalField("suprafață teren (mp)", max_digits=14, decimal_places=2, null=True, blank=True,
                                       help_text="Suprafața terenului din extras (măsurată / din acte), în mp")
    suprafata_constructii_mp = models.DecimalField("suprafață construcții (mp)", max_digits=14, decimal_places=2, null=True, blank=True,
                                                   help_text="Suma suprafețelor construite la sol ale construcțiilor C1, C2… din extras, în mp")
    categorie_folosinta = models.CharField("categorie de folosință", max_length=80, blank=True, help_text="ex. curți-construcții, arabil")
    proprietar_tabular = models.CharField("proprietar tabular", max_length=200, blank=True)
    sarcini = models.TextField("sarcini / ipoteci (partea a III-a)", blank=True)
    data_extras = models.DateField("data extrasului CF", null=True, blank=True)
    observatii = models.TextField(blank=True)

    class Meta:
        ordering = ["locatie", "numar"]
        unique_together = [("locatie", "numar")]
        verbose_name = "carte funciară"
        verbose_name_plural = "cărți funciare"

    def __str__(self):
        return f"CF {self.numar}" + (f" {self.uat}" if self.uat else "")

    @property
    def extras(self):
        return self.documente.filter(arhivat=False).first()


class RaportEvaluare(models.Model):
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="rapoarte_evaluare")
    data = models.DateField("data raportului")
    evaluator = models.CharField(max_length=200)
    scop = models.CharField(max_length=200, blank=True, help_text="ex. garantare credit, raportare financiară, vânzare")
    valoare_piata = models.DecimalField("valoare de piață", max_digits=16, decimal_places=2, null=True, blank=True)
    valoare_garantare = models.DecimalField("valoare de garantare", max_digits=16, decimal_places=2, null=True, blank=True)
    moneda = models.CharField(max_length=3, choices=[("EUR", "EUR"), ("RON", "RON")], default="EUR")
    beneficiar_raport = models.CharField("emis pentru (bancă / beneficiar)", max_length=200, blank=True)
    observatii = models.TextField(blank=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "raport de evaluare"
        verbose_name_plural = "rapoarte de evaluare"

    def __str__(self):
        return f"Evaluare {self.data:%d.%m.%Y} – {self.evaluator}"


class TipAutorizatie(models.Model):
    """Lista standard de autorizații de funcționare (editabilă din Administrare)."""
    cod = models.CharField(max_length=30, unique=True)
    denumire = models.CharField(max_length=200)
    scurt = models.CharField("denumire scurtă", max_length=40)
    emitent = models.CharField("emitent", max_length=120, blank=True)
    documente_suport = models.TextField("documente suport (câte unul pe rând)", blank=True,
                                        help_text="ex. studiu de impact asupra mediului; studiu de dispersie mirosuri")
    responsabil = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                    verbose_name="responsabil implicit (la nivel de grup)")
    ordine = models.PositiveIntegerField(default=0)
    activ = models.BooleanField("în lista standard", default=True)

    class Meta:
        ordering = ["ordine", "denumire"]
        verbose_name = "tip de autorizație"
        verbose_name_plural = "tipuri de autorizații (lista standard)"

    def __str__(self):
        return self.denumire

    @property
    def lista_suport(self):
        return [x.strip(" -•") for x in (self.documente_suport or "").splitlines() if x.strip(" -•")]


class CerintaAutorizatie(models.Model):
    """Pentru un obiectiv: autorizația e necesară sau nu (cu motiv) și cine răspunde de ea (dacă diferă de responsabilul implicit)."""
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="cerinte_autorizatii")
    tip = models.ForeignKey(TipAutorizatie, on_delete=models.CASCADE, related_name="cerinte")
    necesara = models.BooleanField(default=True)
    motiv = models.CharField("motiv (dacă nu e necesară)", max_length=200, blank=True)
    responsabil = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("locatie", "tip")]
        verbose_name = "cerință de autorizație"
        verbose_name_plural = "cerințe de autorizații pe obiective"


class AutorizatieFunctionare(models.Model):
    TIP = [
        ("isu", "Autorizație securitate la incendiu (ISU)"), ("mediu", "Autorizație / autorizație integrată de mediu"),
        ("sanitar_veterinar", "Autorizație sanitar-veterinară (DSVSA)"), ("sanitar", "Autorizație / notificare sanitară (DSP)"),
        ("gospodarire_ape", "Autorizație de gospodărire a apelor"), ("iscir", "Autorizație ISCIR"), ("functionare", "Autorizație de funcționare (primărie)"),
        ("altele", "Altele"),
    ]
    STATUS = [("valabila", "Valabilă"), ("in_reinnoire", "În reînnoire"), ("expirata", "Expirată"), ("suspendata", "Suspendată")]
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="autorizatii")
    tip = models.CharField(max_length=30, help_text="Tipul din lista standard sau „altele”")
    denumire = models.CharField(max_length=255, blank=True, help_text="Dacă diferă de tip (ex. pentru „Altele”)")
    emitent = models.CharField(max_length=200)
    numar = models.CharField("nr. autorizație", max_length=60)
    data = models.DateField("data emiterii")
    valabil_pana = models.DateField("valabilă până la", null=True, blank=True, help_text="Gol = nelimitat / cu viză anuală")
    viza_anuala = models.BooleanField("necesită viză anuală", default=False)
    status = models.CharField(max_length=20, choices=STATUS, default="valabila")
    conditii = models.TextField("condiții / obligații din autorizație", blank=True)

    class Meta:
        ordering = ["tip", "-data"]
        verbose_name = "autorizație de funcționare"
        verbose_name_plural = "autorizații de funcționare"

    TIP_SCURT = {
        "isu": "Aut. ISU", "mediu": "Aut. mediu", "sanitar_veterinar": "Aut. sanitar-vet.", "sanitar": "Aut. DSP",
        "gospodarire_ape": "Aut. ape", "iscir": "Aut. ISCIR", "functionare": "Aut. funcționare",
    }

    def __str__(self):
        return f"{self.denumire_scurta} nr. {self.numar}"

    @property
    def tip_obiect(self):
        if not hasattr(self, "_tip_cache"):
            self._tip_cache = TipAutorizatie.objects.filter(cod=self.tip).first()
        return self._tip_cache

    def get_tip_display(self):
        return self.tip_obiect.denumire if self.tip_obiect else "Altă autorizație"

    @property
    def denumire_scurta(self):
        """Forma scurtă afișată în liste (ex. „Aut. ISU”); denumirea completă rămâne în detalii."""
        if self.tip_obiect:
            return self.tip_obiect.scurt
        if self.tip in self.TIP_SCURT:
            return self.TIP_SCURT[self.tip]
        d = (self.denumire or "Autorizație").strip()
        d = re.sub(r"^autoriza[țţt]i[ae]\s+(de\s+)?", "Aut. ", d, flags=re.I)
        return d if len(d) <= 30 else d[:28].rstrip() + "…"

    @property
    def denumire_completa(self):
        return self.denumire or self.get_tip_display()

    @property
    def zile_ramase(self):
        return (self.valabil_pana - timezone.localdate()).days if self.valabil_pana else None

    @property
    def valabila(self):
        return self.status in ("valabila", "in_reinnoire") and (self.zile_ramase is None or self.zile_ramase >= 0)

    @property
    def scan(self):
        return self.documente.filter(arhivat=False).exclude(categorie="anexa_autoriz").first()

    @property
    def documente_suport(self):
        return self.documente.filter(arhivat=False, categorie="anexa_autoriz").order_by("titlu")


class StandardCertificat(models.Model):
    """Standarde certificate deținute de unitate (ISO, IFS, BRCGS, GLOBALG.A.P. …)."""
    STANDARDE = [
        ("iso9001", "ISO 9001 – Managementul calității"), ("iso14001", "ISO 14001 – Managementul mediului"),
        ("iso45001", "ISO 45001 – Sănătate și securitate în muncă"), ("iso22000", "ISO 22000 / FSSC 22000 – Siguranța alimentelor"),
        ("ifs", "IFS Food"), ("brcgs", "BRCGS"), ("globalgap", "GLOBALG.A.P."), ("iso50001", "ISO 50001 – Managementul energiei"),
        ("altul", "Alt standard"),
    ]
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="standarde")
    standard = models.CharField(max_length=20, choices=STANDARDE)
    denumire_alt = models.CharField("denumire (pentru „Alt standard”)", max_length=120, blank=True)
    organism = models.CharField("organism de certificare", max_length=150, blank=True)
    numar = models.CharField("nr. certificat", max_length=80, blank=True)
    data_emitere = models.DateField("data emiterii", null=True, blank=True)
    valabil_pana = models.DateField("valabil până la", null=True, blank=True)
    domeniu = models.TextField("domeniul certificat", blank=True)
    nivel = models.CharField("nivel / scor (ex. IFS Higher Level)", max_length=80, blank=True)

    class Meta:
        ordering = ["standard", "-data_emitere"]
        verbose_name = "standard certificat"
        verbose_name_plural = "standarde certificate"

    def __str__(self):
        return f"{self.denumire_alt or self.get_standard_display().split(' – ')[0]}" + (f" nr. {self.numar}" if self.numar else "")

    @property
    def zile_ramase(self):
        return (self.valabil_pana - timezone.localdate()).days if self.valabil_pana else None


class ControlAutoritate(models.Model):
    TIP = [("pv_control", "Proces-verbal de control"), ("nota_constatare", "Notă de constatare"),
           ("pv_contraventie", "Proces-verbal de contravenție"), ("dispozitie", "Dispoziție / decizie de măsuri"), ("altul", "Alt document")]
    STATUS = [("deschis", "Măsuri în curs"), ("remediat", "Remediat / închis"), ("contestat", "Contestat"), ("fara_masuri", "Fără măsuri")]
    locatie = models.ForeignKey(Locatie, on_delete=models.CASCADE, related_name="controale")
    tip = models.CharField(max_length=20, choices=TIP)
    autoritate = models.CharField("autoritatea emitentă", max_length=200, help_text="ex. ISU Olt, Garda de Mediu, DSVSA, ITM, ANAF")
    numar = models.CharField(max_length=60, blank=True)
    data = models.DateField()
    constatari = models.TextField("constatări", blank=True)
    masuri = models.TextField("măsuri dispuse", blank=True)
    termen_remediere = models.DateField(null=True, blank=True)
    amenda_ron = models.DecimalField("amendă (RON)", max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="deschis")
    responsabil = models.CharField("responsabil remediere", max_length=120, blank=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "control / proces-verbal"
        verbose_name_plural = "controale și procese-verbale"

    def __str__(self):
        return f"{self.get_tip_display()} {self.numar} – {self.autoritate} ({self.data:%d.%m.%Y})"

    @property
    def intarziat(self):
        return self.status == "deschis" and self.termen_remediere and self.termen_remediere < timezone.localdate()


# ---------------------------------------------------------------------------
# Proiect de investiții + Raport Zero
# ---------------------------------------------------------------------------
class Proiect(models.Model):
    PRIORITATE = [("mica", "Mică"), ("medie", "Medie"), ("mare", "Mare")]
    STARE_RZ = [("ciorna", "Ciornă"), ("trimis", "Trimis spre avizare"), ("aprobat", "Avizat"), ("respins", "Respins")]
    MONEDE = [("RON", "RON"), ("EUR", "EUR")]

    # Identificare / Raport Zero
    locatie = models.ForeignKey(Locatie, on_delete=models.PROTECT, related_name="proiecte")
    nr_ordine = models.PositiveIntegerField()
    cod = models.CharField("cod proiect", max_length=40, unique=True)
    nr_inregistrare = models.CharField("nr. înregistrare document", max_length=60, unique=True)
    denumire = models.CharField("denumire proiect", max_length=300)
    departament = models.CharField("departament responsabil", max_length=80, blank=True)
    linie = models.CharField("linie afacere", max_length=60, blank=True)
    divizie = models.CharField("divizie", max_length=60, blank=True)
    beneficiar = models.ForeignKey(Societate, on_delete=models.PROTECT, related_name="proiecte")
    solicitant = models.CharField(max_length=120, blank=True)
    responsabil_tehnic = models.CharField(max_length=120, blank=True)
    responsabil_implementare = models.CharField(max_length=120, blank=True)
    obiectiv = models.CharField("locație / obiectiv", max_length=255, blank=True)
    cladire = models.CharField("clădire / nr. cadastral", max_length=255, blank=True)
    descriere_lucrari = models.TextField("descriere lucrări", blank=True)
    necesar_achizitii = models.TextField("necesar achiziții", blank=True)
    valoare_rz = models.DecimalField("valoare ofertă / buget (Raport Zero)", max_digits=16, decimal_places=2)
    moneda_rz = models.CharField(max_length=3, choices=MONEDE, default="EUR")
    curs_rz = models.DecimalField("curs EUR la data RZ", max_digits=10, decimal_places=4, null=True, blank=True)
    finantare = models.CharField("finanțare", max_length=80, blank=True)
    prioritate = models.CharField(max_length=10, choices=PRIORITATE, default="medie")
    data_demarare = models.DateField(null=True, blank=True)
    data_finalizare = models.DateField(null=True, blank=True)
    intocmit_de = models.CharField("întocmit", max_length=120, blank=True)
    avizat_de = models.CharField("avizat", max_length=120, blank=True)
    stare_rz = models.CharField("stare Raport Zero", max_length=10, choices=STARE_RZ, default="ciorna")
    revizie_rz = models.PositiveIntegerField("revizie", default=0)
    aprobat_de_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    aprobat_la = models.DateTimeField(null=True, blank=True)

    # Urmărire
    status = models.CharField(max_length=60, blank=True, default="In lucru")
    faza = models.CharField("fază", max_length=60, blank=True, default="Propus/in lucru")
    coordonator = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="proiecte_coordonate")
    pm = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="proiecte_pm", verbose_name="project manager")
    progres_fizic = models.PositiveIntegerField("progres fizic (%)", default=0)

    # Configurator proiect (setări)
    curs_referinta = models.DecimalField("curs de referință EUR (fix pe proiect)", max_digits=10, decimal_places=4, null=True, blank=True,
                                         help_text="Toate conversiile EUR/RON din proiect folosesc acest curs.")
    curs_referinta_data = models.DateField("data cursului de referință", null=True, blank=True)
    curs_referinta_sursa = models.CharField("sursa cursului de referință", max_length=80, blank=True,
                                            help_text="ex. BNR la data Raportului Zero, BNR la o dată aleasă, curs stabilit manual")
    centru_cost = models.CharField("centru de cost (Charisma)", max_length=40, blank=True, help_text="Implicit = codul proiectului.")
    cont_contabil = models.CharField("cont contabil implicit", max_length=20, default="231", blank=True)
    cota_tva = models.DecimalField("cotă TVA implicită (%)", max_digits=5, decimal_places=2, default=Decimal("21"))
    toleranta_depasire = models.DecimalField("toleranță depășire linie buget (%)", max_digits=5, decimal_places=2, default=Decimal("0"))
    aprobator_tehnic = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                         help_text="Aprobă tehnic plățile (implicit coordonatorul).")
    aprobator_financiar = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                            help_text="Aprobă financiar plățile (implicit orice responsabil financiar).")
    prag_aprobare_suplimentara = models.DecimalField("prag aprobare suplimentară (RON)", max_digits=16, decimal_places=2, null=True, blank=True)
    aprobator_suplimentar = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                              help_text="Aprobă plățile peste prag (ex. director general).")
    buget_blocat = models.BooleanField("buget aprobat și blocat", default=False,
                                       help_text="După blocare, bugetul se modifică doar prin rectificări făcute de administrator.")
    buget_blocat_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    buget_blocat_la = models.DateTimeField(null=True, blank=True)
    data_finalizare_reala = models.DateField(null=True, blank=True)
    observatii = models.TextField(blank=True)
    creat_la = models.DateTimeField(auto_now_add=True)
    modificat_la = models.DateTimeField(auto_now=True)

    CAMPURI_RZ = [
        "denumire", "departament", "linie", "beneficiar_id", "solicitant", "responsabil_tehnic", "responsabil_implementare",
        "obiectiv", "cladire", "descriere_lucrari", "necesar_achizitii", "valoare_rz", "moneda_rz", "finantare",
        "prioritate", "data_demarare", "data_finalizare", "intocmit_de", "avizat_de",
    ]

    class Meta:
        ordering = ["cod"]
        unique_together = [("locatie", "nr_ordine")]
        verbose_name = "proiect de investiții"
        verbose_name_plural = "proiecte de investiții"

    def __str__(self):
        return f"{self.cod} · {self.denumire}"

    def get_absolute_url(self):
        return reverse("proiect_detaliu", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.centru_cost:
            self.centru_cost = self.cod
        if not self.curs_referinta:
            self.curs_referinta = self.curs_rz or CursValutar.la_data(self.data_demarare)
            self.curs_referinta_data = self.data_demarare
            self.curs_referinta_sursa = "BNR la data demarării (implicit)"
        super().save(*args, **kwargs)

    @property
    def curs(self):
        return self.curs_referinta or CursValutar.la_data(self.data_demarare)

    def in_eur(self, ron):
        return (Decimal(ron or 0) / self.curs).quantize(Decimal("1")) if self.curs else None

    # --- valori financiare (RON) ---
    @property
    def buget_rz_ron(self):
        if self.moneda_rz == "RON":
            return self.valoare_rz
        return (self.valoare_rz * (self.curs_rz or self.curs)).quantize(Decimal("0.01"))

    @property
    def buget_aprobat_ron(self):
        return suma(self.linii_buget.all(), "valoare_aprobata")

    @property
    def buget_actualizat_ron(self):
        return sum((l.valoare_curenta for l in self.linii_buget.all()), ZERO)

    @property
    def angajat_ron(self):
        return sum((c.valoare_totala_ron for c in self.contracte.all()), ZERO)

    @property
    def facturat_ron(self):
        return suma(self.facturi.all(), "valoare_ron")

    @property
    def platit_ron(self):
        return suma(Plata.objects.filter(factura__proiect=self, stare="platita"), "suma_ron")

    @property
    def procent_executie(self):
        baza = self.buget_actualizat_ron or self.buget_rz_ron
        return round(float(self.facturat_ron) / float(baza) * 100, 1) if baza else 0

    @property
    def intarziat(self):
        return bool(self.data_finalizare and not self.data_finalizare_reala and self.data_finalizare < timezone.localdate())

    @property
    def data_inregistrare_rz(self):
        """Data înregistrării RZ: din nr. de înregistrare (PREFIX.AAAALLZZ.N); altfel data creării în aplicație."""
        m = re.search(r"\.(\d{8})\.", self.nr_inregistrare or "")
        if m:
            try:
                return datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
            except ValueError:
                pass
        return timezone.localdate(self.creat_la) if self.creat_la else None

    @property
    def ultima_avizare_rz(self):
        """Ultima avizare (data și revizia) – din proiect sau din istoricul reviziilor."""
        rev = sorted(self.revizii_rz.all(), key=lambda r: r.revizie)
        if self.aprobat_la:
            return self.aprobat_la, self.revizie_rz
        return (rev[-1].aprobat_la, rev[-1].revizie) if rev else (None, None)

    @property
    def prima_avizare_rz(self):
        rev = sorted(self.revizii_rz.all(), key=lambda r: r.revizie)
        return rev[0].aprobat_la if rev else self.aprobat_la

    def snapshot_rz(self):
        d = {}
        for c in self.CAMPURI_RZ:
            v = getattr(self, c)
            d[c] = str(v) if v is not None else None
        d["cod"] = self.cod
        d["nr_inregistrare"] = self.nr_inregistrare
        d["beneficiar"] = str(self.beneficiar)
        d["buget"] = {l.capitol: str(l.valoare_curenta) for l in self.linii_buget.prefetch_related("rectificari")}
        d["buget_total"] = str(sum((Decimal(v) for v in d["buget"].values()), ZERO))
        d["echipamente"] = [
            {"denumire": e.denumire, "specificatii": e.specificatii, "cantitate": str(e.cantitate), "um": e.um,
             "linie": e.linie_buget.capitol if e.linie_buget_id else None, "valoare_estimata": str(e.valoare_estimata)}
            for e in self.echipamente.select_related("linie_buget").order_by("linie_buget__capitol", "pk")]
        return d


class AccesProiect(models.Model):
    NIVEL = [("citire", "Citire"), ("editare", "Editare")]
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="accese")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="accese_proiecte")
    nivel = models.CharField(max_length=10, choices=NIVEL, default="citire")

    class Meta:
        unique_together = [("proiect", "user")]
        verbose_name = "acces proiect"
        verbose_name_plural = "accese proiecte"

    def __str__(self):
        return f"{self.user} → {self.proiect.cod} ({self.nivel})"


class PlanAnual(models.Model):
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="plan_anual")
    an = models.PositiveIntegerField()
    valoare_estimata = models.DecimalField("buget estimat în an (RON)", max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["proiect", "an"]
        unique_together = [("proiect", "an")]
        verbose_name = "buget anual estimat"
        verbose_name_plural = "bugete anuale estimate"

    def __str__(self):
        return f"{self.proiect.cod} {self.an}: {self.valoare_estimata}"


class RevizieRaportZero(models.Model):
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="revizii_rz")
    revizie = models.PositiveIntegerField()
    date = models.JSONField()
    motiv = models.TextField(blank=True)
    aprobat_de = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    aprobat_la = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["proiect", "-revizie"]
        unique_together = [("proiect", "revizie")]
        verbose_name = "revizie Raport Zero"
        verbose_name_plural = "revizii Raport Zero"

    def __str__(self):
        return f"{self.proiect.cod} rev.{self.revizie}"


# ---------------------------------------------------------------------------
# Buget (structura devizului general — HG 907/2016)
# ---------------------------------------------------------------------------
class LinieBuget(models.Model):
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="linii_buget")
    capitol = models.CharField("cod capitol HG", max_length=10)
    denumire = models.CharField(max_length=300)
    valoare_aprobata = models.DecimalField("buget aprobat (RON, fără TVA)", max_digits=16, decimal_places=2, default=0)
    observatii = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["proiect", "capitol"]
        unique_together = [("proiect", "capitol")]
        verbose_name = "linie buget"
        verbose_name_plural = "linii buget"

    def __str__(self):
        return f"{self.capitol} {self.denumire}"

    @property
    def valoare_curenta(self):
        return self.valoare_aprobata + suma(self.rectificari.all(), "diferenta")

    @property
    def angajat(self):
        """Contracte alocate pe linie + acte adiționale (pe linia lor sau proporțional cu alocarea contractului)."""
        total = suma(self.alocari_contracte.all(), "valoare_ron") + suma(self.acte_aditionale.all(), "diferenta_ron")
        for a in self.alocari_contracte.select_related("contract"):
            c = a.contract
            fara_linie = suma(c.acte_aditionale.filter(linie_buget__isnull=True), "diferenta_ron")
            alocat = c.alocat_ron
            if fara_linie and alocat:
                total += (fara_linie * a.valoare_ron / alocat).quantize(Decimal("0.01"))
        return total

    @property
    def facturat(self):
        return suma(self.alocari.all(), "valoare_ron")

    @property
    def platit(self):
        """Plățile se repartizează pe linii proporțional cu alocarea facturii."""
        total = ZERO
        for a in self.alocari.select_related("factura"):
            f = a.factura
            if f.valoare_ron:
                total += a.valoare_ron * f.platit_ron / f.valoare_ron
        return total.quantize(Decimal("0.01"))

    @property
    def disponibil(self):
        return self.valoare_curenta - self.angajat

    @property
    def depasit(self):
        limita = self.valoare_curenta * (1 + self.proiect.toleranta_depasire / 100)
        return self.angajat > limita or self.facturat > limita


class RectificareBuget(models.Model):
    """Rectificare de buget – doar administratorul (decizia 67)."""
    linie = models.ForeignKey(LinieBuget, on_delete=models.CASCADE, related_name="rectificari")
    diferenta = models.DecimalField("diferență (RON, +/−)", max_digits=16, decimal_places=2)
    motiv = models.TextField()
    aprobat_de = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    data = models.DateField(default=timezone.localdate)

    class Meta:
        verbose_name = "rectificare buget"
        verbose_name_plural = "rectificări buget"


# ---------------------------------------------------------------------------
# Contracte, facturi, plăți
# ---------------------------------------------------------------------------
class Contract(models.Model):
    TIP = [
        ("proiectare", "Proiectare"), ("executie", "Execuție lucrări"), ("furnizare", "Furnizare echipamente"),
        ("servicii", "Servicii / consultanță"), ("dirigentie", "Dirigenție de șantier"), ("altul", "Altul"),
    ]
    STATUS = [("activ", "În derulare"), ("finalizat", "Finalizat"), ("reziliat", "Reziliat"), ("negociere", "În negociere")]
    proiect = models.ForeignKey(Proiect, on_delete=models.PROTECT, related_name="contracte")
    tip = models.CharField(max_length=20, choices=TIP)
    numar = models.CharField("nr. contract", max_length=60)
    data = models.DateField()
    partener = models.CharField("partener (furnizor / executant)", max_length=200)
    cui_partener = models.CharField("CUI partener", max_length=20, blank=True)
    obiect = models.TextField(blank=True)
    moneda = models.CharField(max_length=3, choices=Proiect.MONEDE, default="RON")
    valoare = models.DecimalField("valoare (fără TVA)", max_digits=16, decimal_places=2)
    curs = models.DecimalField("curs EUR", max_digits=10, decimal_places=4, null=True, blank=True,
                               help_text="Gol = cursul de referință al proiectului.")
    valoare_ron = models.DecimalField(max_digits=16, decimal_places=2, editable=False, default=0)
    termen_executie = models.DateField("termen de execuție", null=True, blank=True)
    termen_plata_zile = models.PositiveIntegerField("termen plată (zile)", null=True, blank=True)
    garantie_buna_executie = models.CharField(max_length=200, blank=True)
    perioada_garantie_luni = models.PositiveIntegerField("garanție lucrări (luni)", null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="activ")
    observatii = models.TextField(blank=True)

    class Meta:
        ordering = ["proiect", "data"]
        verbose_name_plural = "contracte"

    def __str__(self):
        return f"{self.numar}/{self.data:%d.%m.%Y} – {self.partener}"

    def save(self, *args, **kwargs):
        if self.moneda == "EUR":
            if not self.curs:
                self.curs = self.proiect.curs
            self.valoare_ron = (self.valoare * self.curs).quantize(Decimal("0.01"))
        else:
            self.valoare_ron = self.valoare
        super().save(*args, **kwargs)

    @property
    def valoare_totala_ron(self):
        return self.valoare_ron + suma(self.acte_aditionale.all(), "diferenta_ron")

    @property
    def facturat_ron(self):
        return suma(self.facturi.all(), "valoare_ron")

    @property
    def rest_de_facturat(self):
        return self.valoare_totala_ron - self.facturat_ron

    @property
    def alocat_ron(self):
        return suma(self.alocari.all(), "valoare_ron")

    @property
    def nealocat_ron(self):
        return self.valoare_ron - self.alocat_ron

    @property
    def document_scanat(self):
        return self.documente.filter(categorie="contract", arhivat=False).first()


class Echipament(models.Model):
    """Echipament al investiției, legat de contractul de achiziție și de linia de buget pe care se încadrează."""
    STATUS = [("planificat", "Planificat"), ("contractat", "Contractat"), ("livrat", "Livrat"), ("montat", "Montat"),
              ("receptionat", "Recepționat / pus în funcțiune")]
    proiect = models.ForeignKey("Proiect", on_delete=models.CASCADE, related_name="echipamente")
    denumire = models.CharField("denumire echipament", max_length=255)
    specificatii = models.CharField("specificații / model", max_length=255, blank=True)
    cantitate = models.DecimalField("nr. bucăți", max_digits=12, decimal_places=2, default=1)
    um = models.CharField("U.M.", max_length=10, default="buc")
    valoare_estimata = models.DecimalField("valoare estimată în Raportul Zero (RON, fără TVA)", max_digits=16, decimal_places=2, default=0)
    valoare_unitara = models.DecimalField("preț unitar contractat (fără TVA)", max_digits=16, decimal_places=2, null=True, blank=True,
                                          help_text="Din contractul de achiziție")
    moneda = models.CharField(max_length=3, choices=[("RON", "RON"), ("EUR", "EUR")], default="RON")
    curs = models.DecimalField("curs EUR", max_digits=10, decimal_places=4, null=True, blank=True,
                               help_text="Gol = cursul contractului de achiziție sau cursul de referință al proiectului")
    valoare_ron = models.DecimalField("valoare contractată (RON)", max_digits=16, decimal_places=2, editable=False, default=0)
    contract = models.ForeignKey("Contract", null=True, blank=True, on_delete=models.SET_NULL, related_name="echipamente",
                                 verbose_name="contract de achiziție")
    linie_buget = models.ForeignKey("LinieBuget", null=True, blank=True, on_delete=models.SET_NULL, related_name="echipamente",
                                    verbose_name="linie buget (cap. HG)", help_text="Gol = linia contractului (dacă are una singură)")
    status = models.CharField(max_length=20, choices=STATUS, default="planificat")
    observatii = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["proiect", "denumire"]
        verbose_name = "echipament"
        verbose_name_plural = "echipamente"

    def __str__(self):
        return f"{self.denumire} × {self.cantitate:g}"

    @property
    def valoare_totala(self):
        return (self.valoare_unitara or 0) * (self.cantitate or 0)

    @property
    def valoare_contractata(self):
        return self.valoare_ron if self.contract_id else ZERO

    @property
    def diferenta_estimat(self):
        """Contractat − estimat în RZ (pozitiv = mai scump decât s-a estimat)."""
        return self.valoare_contractata - self.valoare_estimata if self.contract_id else None

    def save(self, *args, **kwargs):
        if self.contract_id and not self.linie_buget_id:
            alocari = list(self.contract.alocari.all())
            if len(alocari) == 1:
                self.linie_buget_id = alocari[0].linie_buget_id
        if self.contract_id and self.status == "planificat":
            self.status = "contractat"
        if self.valoare_unitara is None:
            self.valoare_ron = ZERO
        elif self.moneda == "EUR":
            if not self.curs:
                self.curs = (self.contract.curs if self.contract_id and self.contract.moneda == "EUR" and self.contract.curs
                             else self.proiect.curs)
            self.valoare_ron = (self.valoare_totala * self.curs).quantize(Decimal("0.01"))
        else:
            self.valoare_ron = Decimal(self.valoare_totala).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class ActAditional(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="acte_aditionale")
    numar = models.CharField(max_length=40)
    data = models.DateField()
    obiect = models.TextField()
    diferenta_ron = models.DecimalField("modificare valoare (RON, +/−)", max_digits=16, decimal_places=2, default=0)
    termen_nou = models.DateField("termen nou de execuție", null=True, blank=True)
    linie_buget = models.ForeignKey(LinieBuget, null=True, blank=True, on_delete=models.PROTECT, related_name="acte_aditionale",
                                    help_text="Linia pe care se angajează diferența. Gol = proporțional cu alocarea contractului.")

    class Meta:
        ordering = ["contract", "data"]
        verbose_name = "act adițional"
        verbose_name_plural = "acte adiționale"

    def __str__(self):
        return f"AA {self.numar} la {self.contract.numar}"


class ContractAlocare(models.Model):
    """Repartizarea valorii contractului pe linii de buget (angajamentul pe fiecare linie)."""
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="alocari")
    linie_buget = models.ForeignKey(LinieBuget, on_delete=models.PROTECT, related_name="alocari_contracte")
    valoare_ron = models.DecimalField("valoare alocată (RON, fără TVA)", max_digits=16, decimal_places=2)

    class Meta:
        verbose_name = "alocare contract pe linie buget"
        verbose_name_plural = "alocări contracte"

    def __str__(self):
        return f"{self.contract.numar} → {self.linie_buget.capitol}: {self.valoare_ron}"


class ConditieContract(models.Model):
    TIP = [
        ("termen_executie", "Termen de execuție"), ("termen_plata", "Termen de plată"), ("garantie", "Garanție"),
        ("penalitate", "Penalitate"), ("receptie", "Recepție"), ("avans", "Avans"), ("reziliere", "Reziliere"),
        ("obligatie", "Obligație"), ("altele", "Altele"),
    ]
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="conditii")
    tip = models.CharField(max_length=20, choices=TIP)
    descriere = models.TextField()
    termen = models.DateField(null=True, blank=True)
    valoare = models.CharField(max_length=120, blank=True)
    fragment_sursa = models.TextField("fragment din contract", blank=True)
    pagina = models.CharField(max_length=20, blank=True)
    extras_ai = models.BooleanField(default=False)
    validat_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    validat_la = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["contract", "tip"]
        verbose_name = "condiție contract"
        verbose_name_plural = "condiții contracte"


class LotExport(models.Model):
    """Fișier generat pentru importul în Charisma (facturi sau plăți aprobate)."""
    TIP = [("facturi", "Facturi"), ("plati", "Plăți aprobate")]
    tip = models.CharField(max_length=10, choices=TIP)
    numar = models.CharField(max_length=40, unique=True)
    creat_de = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    creat_la = models.DateTimeField(auto_now_add=True)
    fisier = models.FileField(upload_to="exporturi/")
    nr_inregistrari = models.PositiveIntegerField(default=0)
    total_ron = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["-creat_la"]
        verbose_name = "lot export ERP"
        verbose_name_plural = "loturi export ERP"

    def __str__(self):
        return self.numar


class Factura(models.Model):
    proiect = models.ForeignKey(Proiect, on_delete=models.PROTECT, related_name="facturi")
    contract = models.ForeignKey(Contract, null=True, blank=True, on_delete=models.PROTECT, related_name="facturi",
                                 help_text="Lăsați gol doar pentru facturi fără contract (taxe, avize, autorizații).")
    furnizor = models.CharField(max_length=200)
    cui_furnizor = models.CharField("CUI furnizor", max_length=20, blank=True)
    furnizor_societate = models.ForeignKey(Societate, null=True, blank=True, on_delete=models.PROTECT, related_name="facturi_emise",
                                           verbose_name="furnizor din grup (intragrup)",
                                           help_text="Completați dacă factura este emisă de o societate din grup.")
    numar = models.CharField("nr. factură", max_length=60)
    data = models.DateField()
    scadenta = models.DateField(null=True, blank=True)
    moneda = models.CharField(max_length=3, choices=Proiect.MONEDE, default="RON")
    valoare = models.DecimalField("valoare fără TVA", max_digits=16, decimal_places=2)
    tva = models.DecimalField("TVA", max_digits=16, decimal_places=2, null=True, blank=True, help_text="Gol = cota TVA din setările proiectului.")
    curs = models.DecimalField("curs EUR", max_digits=10, decimal_places=4, null=True, blank=True, help_text="Gol = cursul de referință al proiectului.")
    valoare_ron = models.DecimalField(max_digits=16, decimal_places=2, editable=False, default=0)
    observatii = models.CharField(max_length=300, blank=True)
    lot_export = models.ForeignKey(LotExport, null=True, blank=True, on_delete=models.SET_NULL, related_name="facturi", editable=False)

    class Meta:
        ordering = ["proiect", "data"]
        verbose_name = "factură"
        verbose_name_plural = "facturi"

    def __str__(self):
        return f"{self.furnizor} {self.numar}/{self.data:%d.%m.%Y}"

    def save(self, *args, **kwargs):
        if not self.furnizor_societate_id and self.cui_furnizor:
            self.furnizor_societate = Societate.objects.filter(cui=self.cui_furnizor.strip(), in_grup=True).first()
        if self.moneda == "EUR":
            if not self.curs:
                self.curs = self.proiect.curs
            self.valoare_ron = (self.valoare * self.curs).quantize(Decimal("0.01"))
        else:
            self.valoare_ron = self.valoare
        if self.tva is None:
            self.tva = (self.valoare * self.proiect.cota_tva / 100).quantize(Decimal("0.01"))
        nou = self._state.adding
        super().save(*args, **kwargs)
        if nou and self.contract_id and not self.alocari.exists():
            self.aloca_ca_contractul()

    def aloca_ca_contractul(self):
        """Repartizează factura pe liniile de buget în aceeași proporție ca alocarea contractului."""
        alocari = list(self.contract.alocari.all())
        total = sum((a.valoare_ron for a in alocari), ZERO)
        if not total:
            return
        rest = self.valoare_ron
        for i, a in enumerate(alocari):
            v = rest if i == len(alocari) - 1 else (self.valoare_ron * a.valoare_ron / total).quantize(Decimal("0.01"))
            FacturaAlocare.objects.create(factura=self, linie_buget_id=a.linie_buget_id, valoare_ron=v)
            rest -= v

    @property
    def intragrup(self):
        return bool(self.furnizor_societate_id)

    @property
    def alocat_ron(self):
        return suma(self.alocari.all(), "valoare_ron")

    @property
    def nealocat_ron(self):
        return self.valoare_ron - self.alocat_ron

    @property
    def platit_ron(self):
        return suma(self.plati.filter(stare="platita"), "suma_ron")

    @property
    def in_aprobare_ron(self):
        return suma(self.plati.exclude(stare__in=["platita", "respinsa"]), "suma_ron")

    @property
    def rest_de_plata(self):
        return self.valoare_ron - self.platit_ron

    @property
    def achitata(self):
        return self.valoare_ron > 0 and self.platit_ron >= self.valoare_ron


class FacturaAlocare(models.Model):
    """Repartizarea unei facturi pe linii de buget (o factură = un proiect, mai multe linii)."""
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name="alocari")
    linie_buget = models.ForeignKey(LinieBuget, on_delete=models.PROTECT, related_name="alocari")
    valoare_ron = models.DecimalField("valoare alocată (RON, fără TVA)", max_digits=16, decimal_places=2)

    class Meta:
        verbose_name = "alocare factură pe linie buget"
        verbose_name_plural = "alocări facturi"

    def __str__(self):
        return f"{self.factura.numar} → {self.linie_buget.capitol}: {self.valoare_ron}"


class Plata(models.Model):
    """Plată cu circuit de aprobare: propusă → aprobată tehnic → aprobată financiar → exportată → plătită."""
    STARI = [
        ("propusa", "Propusă"), ("aprobata_tehnic", "Aprobată tehnic"), ("aprobata", "Aprobată (de exportat)"),
        ("exportata", "Exportată în fișier"), ("platita", "Plătită"), ("respinsa", "Respinsă"),
    ]
    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="plati")
    data = models.DateField("data plății (planificată / efectivă)")
    suma_ron = models.DecimalField("sumă (RON)", max_digits=16, decimal_places=2)
    stare = models.CharField(max_length=20, choices=STARI, default="propusa", editable=False)
    document_plata = models.CharField("OP / document", max_length=80, blank=True)
    sursa_fonduri = models.CharField(max_length=80, blank=True)
    observatii = models.CharField(max_length=300, blank=True)
    propusa_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    aprobat_tehnic_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    aprobat_tehnic_la = models.DateTimeField(null=True, blank=True, editable=False)
    aprobat_financiar_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    aprobat_financiar_la = models.DateTimeField(null=True, blank=True, editable=False)
    aprobat_suplimentar_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    aprobat_suplimentar_la = models.DateTimeField(null=True, blank=True, editable=False)
    respinsa_motiv = models.CharField(max_length=300, blank=True, editable=False)
    lot_export = models.ForeignKey(LotExport, null=True, blank=True, on_delete=models.SET_NULL, related_name="plati", editable=False)
    marcata_din_factura = models.BooleanField("marcată plătită direct din factură", default=False, editable=False)

    class Meta:
        ordering = ["data"]
        verbose_name = "plată"
        verbose_name_plural = "plăți"

    def __str__(self):
        return f"{self.suma_ron} RON / {self.data} ({self.get_stare_display()})"

    @property
    def proiect(self):
        return self.factura.proiect

    @property
    def necesita_aprobare_suplimentara(self):
        prag = self.factura.proiect.prag_aprobare_suplimentara
        return bool(prag and self.suma_ron > prag)


# ---------------------------------------------------------------------------
# Urbanism și avize
# ---------------------------------------------------------------------------
class CertificatUrbanism(models.Model):
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="certificate_urbanism")
    numar = models.CharField(max_length=40)
    data = models.DateField()
    emitent = models.CharField(max_length=200, blank=True)
    scop = models.TextField(blank=True)
    valabil_pana = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "certificat de urbanism"
        verbose_name_plural = "certificate de urbanism"

    def __str__(self):
        return f"CU {self.numar}/{self.data:%d.%m.%Y}"


class Aviz(models.Model):
    STATUS = [
        ("necesar", "Necesar"), ("in_pregatire", "În pregătire"), ("depus", "Depus"),
        ("obtinut", "Obținut"), ("respins", "Respins"), ("nu_e_cazul", "Nu e cazul"),
    ]
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="avize")
    certificat = models.ForeignKey(CertificatUrbanism, null=True, blank=True, on_delete=models.SET_NULL, related_name="avize")
    denumire = models.CharField(max_length=255)
    emitent = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="necesar")
    responsabil = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    data_depunere = models.DateField(null=True, blank=True)
    termen_estimat = models.DateField(null=True, blank=True)
    numar_aviz = models.CharField(max_length=60, blank=True)
    data_aviz = models.DateField(null=True, blank=True)
    valabil_pana = models.DateField(null=True, blank=True)
    cost_ron = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    conditii = models.TextField("condiții impuse prin aviz", blank=True)
    fragment_sursa = models.TextField("fragment din CU", blank=True)
    extras_ai = models.BooleanField(default=False)

    class Meta:
        ordering = ["proiect", "denumire"]
        verbose_name_plural = "avize"

    def __str__(self):
        return self.denumire

    @property
    def expira_curand(self):
        return bool(self.valabil_pana and (self.valabil_pana - timezone.localdate()).days <= 30)


# ---------------------------------------------------------------------------
# Finanțare și obligații
# ---------------------------------------------------------------------------
class Finantare(models.Model):
    TIP = [("proprii", "Fonduri proprii"), ("bancara", "Bancară"), ("nerambursabila", "Fonduri nerambursabile"), ("leasing", "Leasing")]
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="finantari")
    tip = models.CharField(max_length=20, choices=TIP)
    finantator = models.CharField("finanțator / program", max_length=200, blank=True)
    nr_contract = models.CharField("nr. contract finanțare", max_length=80, blank=True)
    data_contract = models.DateField(null=True, blank=True)
    valoare_ron = models.DecimalField("valoare (RON)", max_digits=16, decimal_places=2, default=0)
    valoare_eligibila_ron = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    procent_nerambursabil = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    data_limita_implementare = models.DateField(null=True, blank=True)
    perioada_monitorizare_ani = models.PositiveIntegerField(null=True, blank=True)
    responsabil = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    observatii = models.TextField(blank=True)

    class Meta:
        verbose_name = "sursă de finanțare"
        verbose_name_plural = "surse de finanțare"

    def __str__(self):
        return f"{self.get_tip_display()} – {self.finantator}"


class Obligatie(models.Model):
    STATUS = [("deschisa", "Deschisă"), ("indeplinita", "Îndeplinită"), ("intarziata", "Întârziată"), ("nu_se_aplica", "Nu se aplică")]
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="obligatii")
    finantare = models.ForeignKey(Finantare, null=True, blank=True, on_delete=models.CASCADE, related_name="obligatii")
    contract = models.ForeignKey(Contract, null=True, blank=True, on_delete=models.CASCADE, related_name="obligatii")
    descriere = models.TextField()
    termen = models.DateField(null=True, blank=True)
    recurenta = models.CharField(max_length=60, blank=True, help_text="ex. lunar, anual, o singură dată")
    responsabil = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=20, choices=STATUS, default="deschisa")
    fragment_sursa = models.TextField(blank=True)
    extras_ai = models.BooleanField(default=False)

    class Meta:
        ordering = ["termen"]
        verbose_name = "obligație / termen"
        verbose_name_plural = "obligații și termene"

    def __str__(self):
        return self.descriere[:80]


# ---------------------------------------------------------------------------
# Grafic Gantt
# ---------------------------------------------------------------------------
class Activitate(models.Model):
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="activitati")
    ordine = models.PositiveIntegerField(default=0)
    denumire = models.CharField(max_length=255)
    start = models.DateField()
    sfarsit = models.DateField()
    start_planificat = models.DateField("start (plan inițial)", null=True, blank=True)
    sfarsit_planificat = models.DateField("sfârșit (plan inițial)", null=True, blank=True)
    progres = models.PositiveIntegerField(default=0)
    depinde_de = models.ManyToManyField("self", symmetrical=False, blank=True)
    responsabil = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["proiect", "ordine", "start"]
        verbose_name_plural = "activități (Gantt)"

    def __str__(self):
        return self.denumire

    def save(self, *args, **kwargs):
        if not self.start_planificat:
            self.start_planificat = self.start
        if not self.sfarsit_planificat:
            self.sfarsit_planificat = self.sfarsit
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Documente (versionate, fără ștergere definitivă)
# ---------------------------------------------------------------------------
def cale_document(instance, filename):
    baza = instance.proiect.cod if instance.proiect_id else (instance.locatie.cod if instance.locatie_id else "general")
    return f"{(baza or 'fara-cod').replace('.', '_')}/{timezone.now():%Y%m%d%H%M%S}_{filename}"


class Document(models.Model):
    CATEGORII = [
        ("raport_zero", "Raport Zero semnat"), ("cu", "Certificat de urbanism"), ("aviz", "Aviz / acord"),
        ("autorizatie", "Autorizație de construire"), ("contract", "Contract"), ("act_aditional", "Act adițional"),
        ("factura", "Factură"), ("plata", "Document plată"), ("finantare", "Contract finanțare"),
        ("proces_verbal", "Proces-verbal / recepție"), ("deviz", "Deviz / buget"), ("grafic", "Grafic execuție"),
        ("raport_progres", "Raport de progres"), ("cf", "Extras de carte funciară"), ("foto", "Fotografie"),
        ("folosinta", "Act / contract privind dreptul de folosință"), ("anexa_autoriz", "Document suport autorizație"),
        ("standard", "Certificat standard (ISO, IFS…)"), ("anexa_drept", "Anexă la actul de folosință"),
        ("cadastru", "Documentație cadastrală"), ("plan_topo", "Plan topografic"), ("anexa", "Anexă"),
        ("evaluare", "Raport de evaluare"), ("autorizatie_fct", "Autorizație de funcționare"), ("control", "Proces-verbal / notă de constatare"),
        ("altul", "Altul"),
    ]
    proiect = models.ForeignKey(Proiect, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    locatie = models.ForeignKey(Locatie, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    categorie = models.CharField(max_length=20, choices=CATEGORII)
    titlu = models.CharField(max_length=255)
    fisier = models.FileField(upload_to=cale_document)
    sha256 = models.CharField(max_length=64, editable=False, blank=True)
    versiune = models.PositiveIntegerField(default=1, editable=False)
    inlocuieste = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="inlocuit_de")
    arhivat = models.BooleanField(default=False)
    # legături opționale în lanțul de trasabilitate
    contract = models.ForeignKey(Contract, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    act_aditional = models.ForeignKey(ActAditional, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    factura = models.ForeignKey(Factura, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    plata = models.ForeignKey(Plata, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    aviz = models.ForeignKey(Aviz, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    certificat = models.ForeignKey(CertificatUrbanism, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    finantare = models.ForeignKey(Finantare, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    raport_evaluare = models.ForeignKey(RaportEvaluare, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    carte_funciara = models.ForeignKey(CarteFunciara, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    contract_folosinta = models.ForeignKey(ContractFolosinta, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    standard = models.ForeignKey(StandardCertificat, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    autorizatie = models.ForeignKey(AutorizatieFunctionare, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    control = models.ForeignKey(ControlAutoritate, null=True, blank=True, on_delete=models.PROTECT, related_name="documente")
    incarcat_de = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    incarcat_la = models.DateTimeField(auto_now_add=True)
    text_extras = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["-incarcat_la"]
        verbose_name_plural = "documente"

    def __str__(self):
        return f"{self.titlu} (v{self.versiune})"

    def save(self, *args, **kwargs):
        if self.fisier and not self.sha256:
            h = hashlib.sha256()
            for chunk in self.fisier.chunks():
                h.update(chunk)
            self.sha256 = h.hexdigest()
        if self.inlocuieste_id and self._state.adding:
            self.versiune = self.inlocuieste.versiune + 1
        super().save(*args, **kwargs)
        if self.inlocuieste_id and not self.inlocuieste.arhivat:
            Document.objects.filter(pk=self.inlocuieste_id).update(arhivat=True)

    def delete(self, *args, **kwargs):
        raise PermissionError("Documentele nu se șterg; se arhivează sau se înlocuiesc cu o versiune nouă.")


class ExtragereAI(models.Model):
    TIP = [("cu_avize", "Avize din certificat de urbanism"), ("contract", "Condiții din contract"), ("finantare", "Obligații din contract de finanțare"),
           ("autorizatie", "Date din autorizație de funcționare"), ("cf", "Date din extras de carte funciară")]
    STATUS = [("propus", "Propus de AI – de validat"), ("validat", "Validat"), ("respins", "Respins"), ("eroare", "Eroare")]
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="extrageri")
    tip = models.CharField(max_length=20, choices=TIP)
    rezultat = models.JSONField(default=dict)
    model_ai = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="propus")
    mesaj = models.TextField(blank=True)
    creat_la = models.DateTimeField(auto_now_add=True)
    validat_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    validat_la = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creat_la"]
        verbose_name = "extragere AI"
        verbose_name_plural = "extrageri AI"


# ---------------------------------------------------------------------------
# Jurnal de audit
# ---------------------------------------------------------------------------
class JurnalAudit(models.Model):
    moment = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    actiune = models.CharField(max_length=20)
    model = models.CharField(max_length=60)
    obiect_id = models.CharField(max_length=40)
    obiect = models.CharField(max_length=300)
    # fără constrângere în baza de date: jurnalul păstrează codul proiectului și după o eventuală ștergere
    proiect = models.ForeignKey(Proiect, null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="jurnal")
    modificari = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-moment"]
        verbose_name = "înregistrare jurnal"
        verbose_name_plural = "jurnal de audit"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Jurnalul de audit nu poate fi modificat.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Jurnalul de audit nu poate fi șters.")


from django.db.models.signals import post_save, post_delete  # noqa: E402


def _actualizeaza_data_evaluare(sender, instance, **kw):
    """Data ultimului raport de evaluare se ține la zi pe activ."""
    ultima = RaportEvaluare.objects.filter(locatie_id=instance.locatie_id).order_by("-data").values_list("data", flat=True).first()
    if ultima:
        Locatie.objects.filter(pk=instance.locatie_id).update(data_raport_evaluare=ultima)


post_save.connect(_actualizeaza_data_evaluare, sender=RaportEvaluare, dispatch_uid="data_evaluare_save")
post_delete.connect(_actualizeaza_data_evaluare, sender=RaportEvaluare, dispatch_uid="data_evaluare_del")


class EmailAvizRZ(models.Model):
    """Emailurile de avizare trimise pentru un Raport Zero și răspunsul dat din email."""
    proiect = models.ForeignKey(Proiect, on_delete=models.CASCADE, related_name="emailuri_aviz")
    revizie = models.PositiveIntegerField(default=0)
    catre = models.EmailField()
    aprobator = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    trimis_la = models.DateTimeField(auto_now_add=True)
    trimis_de = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    amprenta = models.CharField("amprenta conținutului trimis", max_length=64, blank=True)
    eroare = models.CharField(max_length=300, blank=True)
    salvat_local = models.BooleanField("doar salvat (email neconfigurat)", default=False)
    raspuns = models.CharField(max_length=10, blank=True, choices=[("aproba", "aprobat din email"), ("respinge", "respins din email")])
    raspuns_la = models.DateTimeField(null=True, blank=True)
    raspuns_ip = models.GenericIPAddressField(null=True, blank=True)
    motiv = models.TextField(blank=True)

    class Meta:
        ordering = ["-trimis_la"]
        verbose_name = "email de avizare RZ"
        verbose_name_plural = "emailuri de avizare RZ"

    def __str__(self):
        return f"{self.proiect.cod} rev.{self.revizie} → {self.catre}"
