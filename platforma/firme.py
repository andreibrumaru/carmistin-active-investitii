"""Curățarea bazei de firme: firmele grupului (cu codul lor, ex. Avicarvil SRL → AVI) separate de proprietarii
din afara grupului de la care se închiriază active (baza „proprietari externi”).

Folosit din Administrare → Societăți (acțiuni pe firmele bifate)."""
import re
import unicodedata

from django.db import transaction
from django.db.models import Q

from .coduri import urmatorul_cod_locatie

_FORME = r"\b(s\.?\s?r\.?\s?l\.?|s\.?\s?a\.?|s\.?\s?c\.?|scm|ifn|srl|sa)\b"
_PUBLIC = re.compile(r"\b(uat|primaria|orasul|oras|comuna|municipiul|consiliul|servicii publice|piete|edilitara|uniunea)\b", re.I)


def cheie(nume):
    """Numele firmei fără diacritice, formă juridică și semne: „Avicarvil S.R.L.” → „avicarvil”."""
    t = unicodedata.normalize("NFD", nume or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(_FORME, " ", t)
    return re.sub(r"[^a-z0-9]", "", t)


def tip_proprietar(nume):
    if _PUBLIC.search(nume or ""):
        return "uat"
    if re.search(_FORME, (nume or "").lower()) or re.search(r"\b(company|impex|com|atcom|holding|group|ouai|cooperativa|asociatia|international|agro|platinum|farm|farms|food)\b", (nume or "").lower()):
        return "pj"
    # 2–4 cuvinte fără formă juridică: aproape sigur persoană fizică (ex. „Tarbau Ana”)
    return "pf" if 2 <= len((nume or "").split()) <= 6 else "pj"


def firma_grup_dupa_nume(nume):
    """Firma din grup cu același nume (ignorând forma juridică și diacriticele)."""
    from .models import Societate
    k = cheie(nume)
    if len(k) < 3:
        return None
    for s in Societate.objects.filter(in_grup=True).order_by("-prefix_cod", "denumire"):
        if cheie(s.denumire) == k:
            return s
    return None


ProprietarExtern_TIP = [("pj", "Persoană juridică"), ("pf", "Persoană fizică"), ("uat", "Instituție publică / UAT")]


def propunere(soc):
    """(e_externa, motiv) — o sugestie, decizia rămâne a administratorului."""
    loc = list(soc.locatii.all())
    if not loc:
        return False, "fără active"
    k = cheie(soc.denumire)
    folosite_de_altii = [l for l in loc if l.utilizator and cheie(l.utilizator) != k]
    inchiriate = all(l.detinere in ("altul", "inchiriat", "") for l in loc)
    if inchiriate and all((l.grup or "").lower() == "non grup" for l in loc) and folosite_de_altii:
        tip = dict(ProprietarExtern_TIP).get(tip_proprietar(soc.denumire), "")
        return True, f"{tip.lower()} – activele sunt folosite de {folosite_de_altii[0].utilizator}"
    return False, ""


def chirias_din_grup(utilizator, exclude=None):
    """Firma din grup care folosește activul. Dacă nu există încă în listă, se adaugă (fără cod) –
    administratorul îi completează apoi codul sau o unește cu firma corectă."""
    from .models import Societate
    if len(cheie(utilizator)) < 3 or (exclude and cheie(utilizator) == cheie(exclude.denumire)):
        return None
    return firma_grup_dupa_nume(utilizator) or Societate.objects.create(denumire=utilizator.strip()[:200], in_grup=True)


@transaction.atomic
def muta_in_externi(soc, user=None):
    """Mută o firmă din baza grupului în baza proprietarilor externi. Activele ei devin închirieri externe;
    firma din grup care le folosește (coloana „utilizator”) devine chiriașul. Întoarce (proprietar_extern, nr_active)."""
    from .models import ProprietarExtern
    if soc.proiecte.exists():
        raise ValueError(f"{soc} este beneficiar în proiecte de investiții – nu poate fi mutată.")
    pe, _ = ProprietarExtern.objects.get_or_create(
        denumire=soc.denumire[:200], defaults={"tip": tip_proprietar(soc.denumire), "cui": soc.cui,
                                              "observatii": "Mutat din lista firmelor grupului."})
    n = 0
    for l in soc.locatii.all():
        chirias = chirias_din_grup(l.utilizator, soc)
        l.inchiriere_externa = True
        l.proprietar_extern = pe
        l.proprietar = chirias
        if l.detinere in ("", "owned"):
            l.detinere = "inchiriat"
        l.save()
        n += 1
    soc.facturi_emise.update(furnizor_societate=None)
    soc.delete()
    return pe, n


@transaction.atomic
def muta_in_grup(pe):
    """Operația inversă: proprietarul extern intră în lista firmelor grupului, activele lui nu mai sunt închirieri."""
    from .models import Societate
    soc = firma_grup_dupa_nume(pe.denumire) or Societate.objects.filter(denumire=pe.denumire).first()
    if not soc:
        soc = Societate.objects.create(denumire=pe.denumire, cui=pe.cui, in_grup=True)
    soc.in_grup = True
    soc.save(update_fields=["in_grup"])
    n = 0
    for l in pe.locatii.all():
        l.inchiriere_externa = False
        l.proprietar_extern = None
        l.proprietar = soc
        if l.detinere == "inchiriat":
            l.detinere = "owned"
        l.save()
        n += 1
    pe.delete()
    return soc, n


@transaction.atomic
def uneste(principala, altele):
    """Unește firmele duplicate (ex. „Pajo Holding” + „Pajo Holding SRL”) în prima: activele, proiectele și facturile
    trec la ea, celelalte se șterg. Codul și CUI-ul lipsă se preiau de la duplicate."""
    n = 0
    for s in altele:
        if s.pk == principala.pk:
            continue
        n += s.locatii.update(proprietar=principala)
        s.proiecte.update(beneficiar=principala)
        s.facturi_emise.update(furnizor_societate=principala)
        for camp in ("cui", "prefix_cod", "cod_erp"):
            if not getattr(principala, camp) and getattr(s, camp):
                setattr(principala, camp, getattr(s, camp))
        s.delete()
    principala.save()
    return n


def aloca_coduri(soc):
    """Activele firmei fără cod primesc codul firmei + număr următor (ex. AVI.54). Codurile existente nu se schimbă."""
    if not soc.prefix_cod:
        return []
    noi = []
    for l in soc.locatii.filter(Q(cod__isnull=True) | Q(cod="")).order_by("nr_registru", "pk"):
        l.cod = urmatorul_cod_locatie(soc.prefix_cod)
        l.save(update_fields=["cod"])
        noi.append(l.cod)
    return noi
