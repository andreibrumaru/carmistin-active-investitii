"""Denumiri scurte pentru avizele / studiile extrase din certificatul de urbanism (textul din CU e adesea foarte lung).
Denumirea completă se păstrează în Aviz.denumire_completa."""
import re
import unicodedata

# (cuvinte care trebuie să apară toate în denumire – fără diacritice, litere mici) → denumirea scurtă
REGULI = [
    (("documentati", "autoriz", "constru"), "DTAC"),
    (("documentati", "tehnic"), "DTAC"),
    (("documentati", "organiz", "santier"), "DTOE"),
    (("certificat", "urbanism"), "CU (copie)"),
    (("punct", "vedere", "mediu"), "Punct de vedere APM"),
    (("act", "administrativ", "mediu"), "Act administrativ APM"),
    (("acord", "mediu"), "Acord de mediu"),
    (("aviz", "mediu"), "Aviz de mediu"),
    (("dovada", "titlu"), "Dovada titlului (act proprietate)"),
    (("extras", "carte", "funciar"), "Extras CF"),
    (("studi", "geotehnic"), "Studiu geotehnic"),
    (("studi", "topo"), "Ridicare topografică"),
    (("ridicare", "topo"), "Ridicare topografică"),
    (("plan", "situatie"), "Plan de situație (OCPI)"),
    (("ocpi",), "Aviz OCPI"),
    (("studii", "specialitate"), "Studii de specialitate"),
    (("studiu", "specialitate"), "Studii de specialitate"),
    (("alimentare", "apa"), "Aviz alimentare cu apă"),
    (("canalizare",), "Aviz canalizare"),
    (("gospodarire", "apelor"), "Aviz gospodărirea apelor"),
    (("energie", "electric"), "Aviz energie electrică"),
    (("gaz",), "Aviz gaze naturale"),
    (("termic",), "Aviz alimentare termică"),
    (("telefon",), "Aviz telefonie"),
    (("telecomunicat",), "Aviz telecomunicații"),
    (("salubr",), "Aviz salubritate"),
    (("incendi",), "Aviz ISU (securitate la incendiu)"),
    (("isu",), "Aviz ISU (securitate la incendiu)"),
    (("protectia civila",), "Aviz protecție civilă"),
    (("notificare", "dsp"), "Notificare DSP"),
    (("notificare", "sanatat"), "Notificare DSP"),
    (("sanatatea populatiei",), "Aviz DSP (sănătatea populației)"),
    (("dsp",), "Aviz DSP (sănătatea populației)"),
    (("sanitar", "veterinar"), "Aviz DSVSA"),
    (("dsvsa",), "Aviz DSVSA"),
    (("politi", "rutier"), "Aviz Poliția rutieră"),
    (("drum",), "Aviz administrator drum"),
    (("cultur",), "Aviz Direcția de cultură"),
    (("aviatie",), "Aviz AACR (aviație civilă)"),
    (("agricultur",), "Aviz Direcția agricolă"),
    (("silvic",), "Aviz silvic"),
]
LUNGIME_MAX = 60


def _simplu(text):
    t = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def prescurteaza(denumire):
    """Denumirea scurtă propusă (sau cea originală, ușor curățată, dacă nu se potrivește nicio regulă)."""
    original = re.sub(r"\s+", " ", (denumire or "").strip(" .;,-•"))
    if not original:
        return original
    s = _simplu(original)
    for cuvinte, scurt in REGULI:
        if all(c in s for c in cuvinte):
            return scurt
    scurt = original[0].upper() + original[1:]
    if len(scurt) > LUNGIME_MAX:
        scurt = scurt[:LUNGIME_MAX].rsplit(" ", 1)[0].rstrip(" ,;/") + "…"
    return scurt
