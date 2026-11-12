from decimal import Decimal
from django import forms
from django.db.models import Max, Q
from django.contrib.auth import get_user_model

from . import models as m

User = get_user_model()
DATA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class Baza(forms.ModelForm):
    def __init__(self, *a, proiect=None, **kw):
        super().__init__(*a, **kw)
        self.proiect = proiect
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.DateInput):
                f.widget = DATA
                f.widget.is_required = f.required
            css = "form-select" if isinstance(f.widget, (forms.Select, forms.SelectMultiple)) else "form-control"
            if isinstance(f.widget, forms.CheckboxInput):
                css = "form-check-input"
            f.widget.attrs.setdefault("class", css)
            if isinstance(f.widget, forms.Textarea):
                f.widget.attrs.setdefault("rows", 3)
        if proiect is not None:
            for name, f in self.fields.items():
                qs = getattr(f, "queryset", None)
                if qs is None:
                    continue
                if qs.model is m.LinieBuget:
                    f.queryset = proiect.linii_buget.all()
                elif qs.model is m.Contract:
                    f.queryset = proiect.contracte.all()
                elif qs.model is m.Factura:
                    f.queryset = proiect.facturi.all()
                elif qs.model is m.CertificatUrbanism:
                    f.queryset = proiect.certificate_urbanism.all()
                elif qs.model is m.Finantare:
                    f.queryset = proiect.finantari.all()
                elif qs.model is m.Aviz:
                    f.queryset = proiect.avize.all()
                elif qs.model is m.Activitate:
                    f.queryset = proiect.activitati.all()
                elif qs.model is m.Plata:
                    f.queryset = m.Plata.objects.filter(factura__proiect=proiect)
                elif qs.model is m.ActAditional:
                    f.queryset = m.ActAditional.objects.filter(contract__proiect=proiect)
                elif qs.model is m.Document:
                    f.queryset = proiect.documente.filter(arhivat=False)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.proiect is not None and hasattr(obj, "proiect_id") and not obj.proiect_id:
            obj.proiect = self.proiect
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class SelectDivizie(forms.Select):
    """Opțiunile de divizie poartă linia de care aparțin (data-linie); JS-ul le filtrează după linia aleasă."""
    def __init__(self, *a, linii=None, **kw):
        super().__init__(*a, **kw)
        self.linii = linii or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opt = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            opt["attrs"]["data-linie"] = self.linii.get(index, "")
        return opt


def campuri_linie_divizie(form):
    """Linia de business din nomenclator și divizia dependentă de linie (ex. Pui → Reproducție, Incubație, Broiler)."""
    inst = form.instance
    linie_curenta = form.initial.get("linie") or getattr(inst, "linie", "") or ""
    div_curenta = form.initial.get("divizie") or getattr(inst, "divizie", "") or ""
    opt = [("", "—")] + [(o.valoare, o.valoare) for o in m.Nomenclator.optiuni("linie")]
    if linie_curenta and linie_curenta not in dict(opt):
        opt.append((linie_curenta, linie_curenta))
    form.fields["linie"] = forms.ChoiceField(choices=opt, required=False, label="Linie de business",
                                             widget=forms.Select(attrs={"class": "form-select"}))
    optiuni, linii = [("", "—")], {}
    for o in m.Nomenclator.optiuni("divizie"):
        scurt = o.valoare.split(" – ", 1)[-1]
        linii[len(optiuni)] = o.grup
        optiuni.append((scurt, scurt))
    if div_curenta and div_curenta not in [v for v, _ in optiuni]:
        linii[len(optiuni)] = linie_curenta
        optiuni.append((div_curenta, div_curenta))
    form.fields["divizie"] = forms.ChoiceField(choices=optiuni, required=False, label="Divizie",
                                               help_text="Se alege după linia de business; lista se completează din Administrare → Nomenclatoare.",
                                               widget=SelectDivizie(attrs={"class": "form-select"}, linii=linii))


class LocatieForm(Baza):
    """Locație: întâi firma proprietară, apoi acronimul (unic pe firmă) și codul propus automat PREFIX.NN."""
    data_schimbare_stare = forms.DateField(label="Data schimbării categoriei", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                           help_text="Implicit azi")
    motiv_schimbare_stare = forms.CharField(label="Motivul schimbării categoriei", required=False,
                                            widget=forms.Textarea(attrs={"rows": 2}),
                                            help_text="Recomandat; rămâne în istoricul activului.")
    prefix = forms.CharField(label="Acronim firmă (prefix cod)", max_length=5, required=False,
                             help_text="Propus automat după firmă; rămâne alocat firmei pentru toate locațiile ei.")

    class Meta:
        model = m.Locatie
        exclude = ["sursa_import", "cod_original", "tip"]  # tipul vechi e înlocuit de categoria activului

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["proprietar"].required = True
        self.fields["proprietar"].label = "Firma din grup care deține locația"
        self.fields["proprietar"].help_text = ""
        actual = self.instance.proprietar_id if self.instance.pk else None
        self.fields["proprietar"].queryset = m.Societate.objects.filter(Q(in_grup=True) | Q(pk=actual)).order_by("denumire")
        actual_ext = self.instance.proprietar_extern_id if self.instance.pk else None
        self.fields["proprietar_extern"].queryset = m.ProprietarExtern.objects.filter(Q(activ=True) | Q(pk=actual_ext)).order_by("denumire")
        self.fields["proprietar_extern"].label = "Proprietar extern (din afara grupului)"
        self.fields["inchiriere_externa"].label = "ÎNCHIRIERE EXTERNĂ – activ închiriat de la un proprietar din afara grupului"
        self.fields["inchiriere_externa"].help_text = ""
        self.fields["prefix"].widget.attrs.update({"class": "form-control text-uppercase", "autocomplete": "off"})
        self.fields["cod"].help_text = "Generat automat: ACRONIM.NN (următorul număr liber al firmei). Îl puteți modifica."
        if self.instance.pk and self.instance.cod:
            self.fields["prefix"].initial = self.instance.cod.split(".")[0]
        elif self.instance.pk and self.instance.proprietar_id:
            self.fields["prefix"].initial = self.instance.proprietar.prefix_cod
        campuri_linie_divizie(self)
        primele = ["inchiriere_externa", "proprietar_extern", "proprietar", "prefix", "cod", "contract_inchiriere", "chirie_lunara",
                   "moneda_chirie", "inchiriere_expira", "denumire", "stare_activ", "data_schimbare_stare", "motiv_schimbare_stare",
                   "utilizator", "linie", "divizie"]
        self.fields["utilizator"].label = "Utilizator (firma care exploatează activul)"
        self.fields["stare_activ"].widget.attrs["data-initial"] = self.instance.stare_activ if self.instance.pk else ""
        self.order_fields(primele + [x for x in self.fields if x not in primele])

    def clean(self):
        from .coduri import valideaza_prefix
        d = super().clean()
        if d.get("inchiriere_externa"):
            if not d.get("proprietar_extern"):
                self.add_error("proprietar_extern", "La închirierea externă alegeți proprietarul extern (sau cereți administratorului să-l definească).")
            d["detinere"] = d.get("detinere") if d.get("detinere") in ("inchiriat", "concesiune") else "inchiriat"
        else:
            d["proprietar_extern"] = None
            for c in ("contract_inchiriere", "chirie_lunara", "inchiriere_expira"):
                d[c] = "" if c == "contract_inchiriere" else None
        soc, prefix, cod = d.get("proprietar"), (d.get("prefix") or "").strip().upper(), (d.get("cod") or "").strip().upper()
        if not soc:
            return d
        if soc.prefix_cod and prefix and prefix != soc.prefix_cod.upper() and not (self.instance.pk and self.instance.cod):
            self.add_error("prefix", f"Firma {soc} are deja acronimul {soc.prefix_cod}; codurile locațiilor ei încep cu {soc.prefix_cod}.")
        prefix = prefix or soc.prefix_cod.upper()
        if not prefix:
            self.add_error("prefix", "Introduceți acronimul firmei.")
            return d
        if not soc.prefix_cod:
            eroare = valideaza_prefix(prefix, soc.pk)
            if eroare:
                self.add_error("prefix", eroare)
        if cod and not cod.startswith(prefix + "."):
            self.add_error("cod", f"Codul trebuie să înceapă cu acronimul firmei: {prefix}.NN")
        d["prefix"], d["cod"] = prefix, cod or None
        return d


class LocatieSelect(forms.Select):
    """Fiecare opțiune poartă beneficiarul (proprietarul din registrul activelor) și următorul cod de proiect."""
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opt = super().create_option(name, value, label, selected, index, subindex, attrs)
        loc = getattr(value, "instance", None)
        if loc is not None:
            opt["attrs"]["data-beneficiar"] = loc.proprietar_id or ""
            opt["attrs"]["data-urmator"] = f"{loc.cod}.{(loc.max_ordine or 0) + 1:02d}"
        return opt


class RaportZeroForm(Baza):
    beneficiar = forms.ModelChoiceField(
        queryset=m.Societate.objects.none(), label="Beneficiar",
        help_text="Alegeți întâi beneficiarul; lista de locații se filtrează după proprietarul din registrul activelor.",
    )
    locatie = forms.ModelChoiceField(
        queryset=m.Locatie.objects.none(), label="Locație / activ", widget=LocatieSelect,
        help_text="Codul proiectului se generează automat: COD_LOCAȚIE.NN",
    )

    class Meta:
        model = m.Proiect
        fields = [
            "beneficiar", "locatie", "denumire", "departament", "linie", "divizie", "solicitant", "responsabil_tehnic",
            "responsabil_implementare", "obiectiv", "cladire", "descriere_lucrari", "necesar_achizitii", "valoare_rz",
            "moneda_rz", "finantare", "prioritate", "data_demarare", "data_finalizare", "intocmit_de", "avizat_de",
            "coordonator", "pm",
        ]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        locatii = (m.Locatie.objects.exclude(cod__isnull=True).exclude(proprietar__isnull=True)
                   .annotate(max_ordine=Max("proiecte__nr_ordine")).order_by("cod"))
        self.fields["locatie"].queryset = locatii
        self.fields["beneficiar"].queryset = m.Societate.objects.filter(locatii__in=locatii).distinct().order_by("denumire")
        for c in ("beneficiar", "locatie"):
            self.fields[c].widget.attrs["class"] = "form-select"
        if self.instance.pk:  # codul proiectului e legat de locație: nu se mai schimbă după creare
            self.fields["locatie"].disabled = True
            self.fields["beneficiar"].disabled = True
        campuri_linie_divizie(self)
        for camp, tip in (("departament", "departament"), ("finantare", "tip_finantare")):
            opt = [("", "—")] + [(o.valoare, o.valoare) for o in m.Nomenclator.optiuni(tip)]
            val = self.initial.get(camp) or getattr(self.instance, camp, "")
            if val and val not in dict(opt):
                opt.append((val, val))
            self.fields[camp] = forms.ChoiceField(choices=opt, required=False, label=self.fields[camp].label,
                                                  widget=forms.Select(attrs={"class": "form-select"}))
        self.fields["coordonator"].queryset = User.objects.filter(profil__rol__in=["coordonator", "admin"])
        self.fields["pm"].queryset = User.objects.filter(profil__rol__in=["pm", "coordonator"])
        # Valoarea RZ se calculează din bugetul HG de mai jos; se scrie de mână doar dacă bugetul e gol.
        v = self.fields["valoare_rz"]
        if self.is_bound and self.total_buget_post():
            v.required = False  # se calculează la salvare din bugetul HG
        v.help_text = "Se completează automat din totalul bugetului HG de mai jos (convertit în moneda RZ)."

    def total_buget_post(self):
        """Totalul bugetului HG trimis în formular (RON), sau 0 dacă e gol / invalid."""
        from .views import _numar_ro
        tot = Decimal(0)
        for k, val in self.data.items():
            if k.startswith("buget_") and str(val).strip():
                try:
                    tot += _numar_ro(val)
                except ValueError:
                    pass
        return tot

    def clean(self):
        date = super().clean()
        ben, loc = date.get("beneficiar"), date.get("locatie")
        if ben and loc and loc.proprietar_id != ben.pk:
            self.add_error("locatie", f"Locația {loc.cod} aparține societății {loc.proprietar}, nu beneficiarului ales.")
        return date


class SocietateNouaForm(forms.ModelForm):
    class Meta:
        model = m.Societate
        fields = ["denumire", "cui", "cod_erp", "in_grup", "prefix_cod"]
        labels = {"prefix_cod": "Acronim (prefix pentru codurile locațiilor)"}

    def clean_prefix_cod(self):
        from .coduri import valideaza_prefix
        p = (self.cleaned_data.get("prefix_cod") or "").strip().upper()
        eroare = valideaza_prefix(p)
        if eroare:
            raise forms.ValidationError(eroare)
        return p


class UrmarireProiectForm(Baza):
    class Meta:
        model = m.Proiect
        fields = ["status", "faza", "progres_fizic", "coordonator", "pm", "data_finalizare_reala", "observatii"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for camp, tip in (("status", "status_proiect"), ("faza", "faza_proiect")):
            opt = [(o.valoare, o.valoare) for o in m.Nomenclator.optiuni(tip)]
            val = getattr(self.instance, camp, "")
            if val and val not in dict(opt):
                opt.append((val, val))
            self.fields[camp] = forms.ChoiceField(choices=opt, label=self.fields[camp].label,
                                                  widget=forms.Select(attrs={"class": "form-select"}))


def fabrica(model, campuri="__all__", exclude=("proiect",)):
    meta = type("Meta", (), {"model": model, "fields": campuri} if campuri != "__all__" else {"model": model, "exclude": list(exclude)})
    return type(f"{model.__name__}Form", (Baza,), {"Meta": meta})


LinieBugetForm = fabrica(m.LinieBuget)
RectificareForm = fabrica(m.RectificareBuget, ["linie", "diferenta", "motiv", "data"])
class CuScan:
    """Adaugă câmpul pentru documentul scanat; vederea îl salvează ca Document legat de obiect."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["scan"] = forms.FileField(
            required=False, label=getattr(self, "eticheta_scan", "Document scanat (PDF)"),
            widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".pdf,image/*"}),
            help_text="Se arhivează cu amprentă SHA-256; o încărcare nouă devine versiune nouă, cea veche rămâne în istoric.",
        )
        self.fields["citeste_ai"] = forms.BooleanField(
            required=False, initial=True, label="Completează automat datele din document (citire AI)",
            widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            help_text="Bifat: imediat ce alegeți fișierul, AI-ul îl citește și completează câmpurile goale, pentru verificare înainte de salvare.",
        )
        self.order_fields(["scan", "citeste_ai"] + [f for f in self.fields if f not in ("scan", "citeste_ai")])


ContractForm = type("ContractForm", (CuScan, fabrica(m.Contract)), {})
ActAditionalForm = type("ActAditionalForm", (CuScan, fabrica(m.ActAditional)), {})
AlocareContractForm = fabrica(m.ContractAlocare, ["contract", "linie_buget", "valoare_ron"])
# Denumirea, cantitatea, linia și valoarea estimată vin din Raportul Zero; contractul și prețul se stabilesc la bifarea pe contract.
EchipamentForm = fabrica(m.Echipament, ["specificatii", "status", "observatii"])
ConditieForm = fabrica(m.ConditieContract, ["contract", "tip", "descriere", "termen", "valoare", "fragment_sursa", "pagina"])
FacturaForm = fabrica(m.Factura, ["contract", "furnizor", "cui_furnizor", "furnizor_societate", "numar", "data", "scadenta", "moneda",
                                  "valoare", "tva", "curs", "observatii"])
_FacturaBaza = FacturaForm


class FacturaForm(_FacturaBaza):
    """Factura + bifa „Plătită”: la bifare, factura apare ca achitată în buget."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        f = self.instance if self.instance and self.instance.pk else None
        self.fields["platita"] = forms.BooleanField(
            required=False, label="Plătită", initial=bool(f and f.achitata),
            widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            help_text="Bifat: factura se înregistrează ca achitată integral și apare ca plătită în buget.",
        )
        self.fields["data_platii"] = forms.DateField(required=False, label="Data plății", widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
                                                     help_text="Gol = data de azi")
        if f and f.platit_ron and not f.plati.filter(marcata_din_factura=True).exists():
            self.fields["platita"].disabled = True
            self.fields["platita"].help_text = "Plățile acestei facturi sunt înregistrate prin circuitul de aprobare."


AlocareForm = fabrica(m.FacturaAlocare, ["factura", "linie_buget", "valoare_ron"])
FacturaForm.base_fields["contract"].empty_label = "— fără contract (taxe, avize, autorizații) —"
PlataForm = fabrica(m.Plata, ["factura", "data", "suma_ron", "sursa_fonduri", "observatii"])


class SetariProiectForm(Baza):
    class Meta:
        model = m.Proiect
        fields = ["centru_cost", "cont_contabil", "cota_tva", "toleranta_depasire", "aprobator_tehnic",
                  "aprobator_financiar", "prag_aprobare_suplimentara", "aprobator_suplimentar"]

    def __init__(self, *a, blocat=False, **kw):
        super().__init__(*a, **kw)
        self.fields["aprobator_financiar"].queryset = User.objects.filter(profil__rol__in=["financiar", "admin"])
        if blocat:  # după blocarea bugetului, cursul și centrul de cost le schimbă doar administratorul
            for c in ("centru_cost", "cont_contabil"):
                self.fields[c].disabled = True
                self.fields[c].help_text = "Blocat odată cu bugetul (modificabil doar de administrator)."
CertificatForm = fabrica(m.CertificatUrbanism)
AvizForm = fabrica(m.Aviz, ["certificat", "denumire", "emitent", "status", "responsabil", "data_depunere", "termen_estimat",
                            "numar_aviz", "data_aviz", "valabil_pana", "cost_ron", "conditii"])
FinantareForm = fabrica(m.Finantare)
ObligatieForm = fabrica(m.Obligatie, ["finantare", "contract", "descriere", "termen", "recurenta", "responsabil", "status"])
ActivitateForm = fabrica(m.Activitate, ["ordine", "denumire", "start", "sfarsit", "progres", "depinde_de", "responsabil"])
AccesForm = fabrica(m.AccesProiect)


class DocumentForm(Baza):
    class Meta:
        model = m.Document
        fields = ["categorie", "titlu", "fisier", "inlocuieste", "contract", "act_aditional", "factura", "plata", "aviz",
                  "certificat", "finantare"]
        labels = {"inlocuieste": "versiune nouă a documentului"}


class DocumentLocatieForm(Baza):
    class Meta:
        model = m.Document
        fields = ["categorie", "titlu", "fisier"]


def fabrica_loc(model):
    meta = type("Meta", (), {"model": model, "exclude": ["locatie"]})
    return type(f"{model.__name__}Form", (CuScan, type(f"{model.__name__}Baza", (Baza,), {"Meta": meta})), {})


RaportEvaluareForm = fabrica_loc(m.RaportEvaluare)
CarteFunciaraForm = fabrica_loc(m.CarteFunciara)
CarteFunciaraForm.eticheta_scan = "Extras de carte funciară (PDF)"
class FisiereMultiple(forms.ClearableFileInput):
    allow_multiple_selected = True


class CampFisiereMultiple(forms.FileField):
    """Acceptă mai multe fișiere într-un singur câmp (vederea le citește cu request.FILES.getlist)."""
    def clean(self, data, initial=None):
        unic = super().clean
        if isinstance(data, (list, tuple)):
            return [unic(d, initial) for d in data if d]
        return unic(data, initial)


class ContractFolosintaForm(fabrica_loc(m.ContractFolosinta)):
    eticheta_scan = "Actul / contractul principal (PDF)"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["anexe"] = CampFisiereMultiple(
            required=False, label="Anexe (puteți selecta mai multe fișiere)",
            widget=FisiereMultiple(attrs={"class": "form-control", "multiple": True}),
            help_text="Anexele actului (planuri, procese-verbale de predare, acte adiționale, extrase etc.); se adaugă în sublista actului.",
        )
        ordine = ["scan", "citeste_ai", "tip", "denumire_act", "numar", "data", "contraparte", "folosit_de", "data_inceput", "data_sfarsit",
                  "chirie", "moneda", "anexe", "observatii"]
        self.order_fields(ordine)
class AutorizatieForm(fabrica_loc(m.AutorizatieFunctionare)):
    eticheta_scan = "Autorizația scanată (PDF)"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        opt = [(t.cod, t.denumire) for t in m.TipAutorizatie.objects.filter(activ=True)] + [("altele", "Altă autorizație")]
        val = self.initial.get("tip") or getattr(self.instance, "tip", "")
        if val and val not in dict(opt):
            opt.append((val, val))
        self.fields["tip"] = forms.ChoiceField(choices=opt, label="Tip autorizație", widget=forms.Select(attrs={"class": "form-select"}))
        self.order_fields(["scan", "citeste_ai"] + [f for f in self.fields if f not in ("scan", "citeste_ai")])


StandardForm = fabrica_loc(m.StandardCertificat)
StandardForm.eticheta_scan = "Certificatul scanat (PDF)"
ControlForm = fabrica_loc(m.ControlAutoritate)

FORMULARE_LOCATIE = {
    "cf": (CarteFunciaraForm, "Carte funciară", "prezentare#carti-funciare"),
    "folosinta": (ContractFolosintaForm, "Drept de folosință", "prezentare#folosinta"),
    "evaluare": (RaportEvaluareForm, "Raport de evaluare", "evaluari"),
    "autorizatie": (AutorizatieForm, "Autorizație de funcționare", "autorizatii"),
    "standard": (StandardForm, "Standard certificat", "standarde"),
    "control": (ControlForm, "Proces-verbal / notă de constatare", "controale"),
}


class ImportGanttForm(forms.Form):
    fisier = forms.FileField(help_text="Excel (.xlsx) cu coloanele: Activitate, Start, Sfârșit, Progres, Responsabil — sau MS Project XML (.xml)")
    inlocuieste = forms.BooleanField(required=False, initial=True, label="Înlocuiește activitățile existente")


FORMULARE = {
    "linie_buget": (LinieBugetForm, "Linie buget"),
    "rectificare": (RectificareForm, "Rectificare buget"),
    "contract": (ContractForm, "Contract"),
    "act_aditional": (ActAditionalForm, "Act adițional"),
    "conditie": (ConditieForm, "Condiție contract"),
    "factura": (FacturaForm, "Factură"),
    "plata": (PlataForm, "Propunere de plată"),
    "alocare": (AlocareForm, "Alocare factură pe linie de buget"),
    "alocare_contract": (AlocareContractForm, "Alocare contract pe linie de buget"),
    "echipament": (EchipamentForm, "Echipament"),
    "certificat": (CertificatForm, "Certificat de urbanism"),
    "aviz": (AvizForm, "Aviz"),
    "finantare": (FinantareForm, "Sursă de finanțare"),
    "obligatie": (ObligatieForm, "Obligație / termen"),
    "activitate": (ActivitateForm, "Activitate grafic"),
    "acces": (AccesForm, "Acces utilizator"),
    "document": (DocumentForm, "Document"),
}


class ProprietarExternForm(forms.ModelForm):
    class Meta:
        model = m.ProprietarExtern
        fields = ["denumire", "tip", "cui", "adresa", "persoana_contact", "telefon", "email"]
