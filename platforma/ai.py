"""AI local pentru extragerea informațiilor din documente.

Rulează integral pe serverul propriu:
  1. extragere text din PDF (pypdf); pentru scanări → OCR local cu Tesseract (limba română), dacă e instalat;
  2. model de limbaj local servit de Ollama (setarea OLLAMA_MODEL);
  3. rezultatul este doar o PROPUNERE, salvată ca ExtragereAI; utilizatorul o validează înainte să devină dată de proiect.
Dacă modelul nu e disponibil, pentru certificatul de urbanism se folosește o regulă de rezervă pe cuvinte-cheie.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
from django.conf import settings

MAX_CARACTERE = 60000


# ---------------------------------------------------------------- text / OCR
def extrage_text(cale):
    cale = Path(cale)
    text = ""
    if cale.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            pagini = PdfReader(str(cale)).pages
            text = "\n".join(f"[pagina {i + 1}]\n{p.extract_text() or ''}" for i, p in enumerate(pagini))
        except Exception:
            text = ""
        if text_util(text) < 200:  # PDF scanat (imagini fără strat de text) -> OCR
            ocr = _ocr_pdf(cale)
            if ocr:
                text = ocr
    elif cale.suffix.lower() in (".txt", ".md"):
        text = cale.read_text(errors="ignore")
    elif cale.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        text = _ocr_imagine(cale) or ""
    return text.strip()


def text_util(text):
    """Numărul de caractere reale (fără marcajele [pagina N] și spații)."""
    return len(re.sub(r"\[pagina \d+\]|\s", "", text or ""))


OCR_MACOS = Path(__file__).resolve().parent.parent / "tools" / "ocr_macos"


def _ocr_macos():
    """OCR-ul inclus în macOS (framework Vision, suportă limba română). Se compilează automat la prima folosire."""
    if OCR_MACOS.exists():
        return str(OCR_MACOS)
    sursa = OCR_MACOS.with_suffix(".swift")
    if sursa.exists() and shutil.which("swiftc"):
        try:
            subprocess.run(["swiftc", "-O", str(sursa), "-o", str(OCR_MACOS)], check=True, timeout=600, capture_output=True)
            return str(OCR_MACOS)
        except Exception:  # noqa: BLE001
            return None
    return None


def ocr_disponibil():
    return bool((shutil.which("tesseract") and shutil.which("pdftoppm")) or _ocr_macos())


def _ocr_imagine(cale):
    if not shutil.which("tesseract"):
        if _ocr_macos():
            r = subprocess.run([_ocr_macos(), str(cale)], capture_output=True, text=True, timeout=600)
            return r.stdout
        return None
    r = subprocess.run(["tesseract", str(cale), "-", "-l", "ron+eng"], capture_output=True, text=True, timeout=300)
    return r.stdout


def _ocr_pdf(cale):
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        if _ocr_macos():  # pe Mac: OCR nativ; pe serverul Linux se folosește Tesseract
            r = subprocess.run([_ocr_macos(), str(cale)], capture_output=True, text=True, timeout=1800)
            return r.stdout
        return None
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(cale), f"{d}/p"], check=True, timeout=600)
        parti = []
        for i, img in enumerate(sorted(Path(d).glob("p*.png"))):
            parti.append(f"[pagina {i + 1}]\n{_ocr_imagine(img) or ''}")
        return "\n".join(parti)


# ---------------------------------------------------------------- model local
def stare_ai():
    if settings.AI_PROVIDER != "ollama":
        return False, "AI dezactivat din setări"
    try:
        r = requests.get(f"{settings.OLLAMA_URL}/api/tags", timeout=3)
        modele = [m["name"] for m in r.json().get("models", [])]
        if settings.OLLAMA_MODEL not in modele and f"{settings.OLLAMA_MODEL}:latest" not in modele:
            return False, f"Modelul {settings.OLLAMA_MODEL} nu e instalat (ollama pull {settings.OLLAMA_MODEL})"
        return True, settings.OLLAMA_MODEL
    except Exception:
        return False, f"Serverul Ollama nu răspunde la {settings.OLLAMA_URL}"


def _context_grup(document):
    """Lista societăților grupului (proprietarii din registrul activelor), ca AI-ul să nu le confunde cu partenerul."""
    from .models import Societate
    nume = list(Societate.objects.filter(locatii__isnull=False).distinct().values_list("denumire", flat=True))
    benef = getattr(getattr(getattr(document, "proiect", None), "beneficiar", None), "denumire", "")
    return (f" Beneficiarul proiectului este {benef}." if benef else "") + " Societățile grupului: " + "; ".join(nume[:200]) + "."


def _e_din_grup(nume_firma):
    from .models import Societate
    cheie = re.sub(r"[^a-z0-9]", "", (nume_firma or "").lower().replace("srl", "").replace("sa", ""))[:12]
    if len(cheie) < 4:
        return False
    for d in Societate.objects.filter(locatii__isnull=False).values_list("denumire", flat=True).distinct():
        if cheie in re.sub(r"[^a-z0-9]", "", d.lower().replace("srl", "").replace("sa", "")):
            return True
    return False


def _intreaba(instructiuni, text):
    r = requests.post(
        f"{settings.OLLAMA_URL}/api/chat",
        json={
            "model": settings.OLLAMA_MODEL,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0, "num_ctx": 32768},
            "messages": [
                {"role": "system", "content": instructiuni},
                {"role": "user", "content": f"DOCUMENT:\n<<<\n{text[:MAX_CARACTERE]}\n>>>"},
            ],
        },
        timeout=settings.AI_TIMEOUT,
    )
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


REGULI_COMUNE = (
    "Răspunzi DOAR cu JSON valid. Nu inventa informații: extragi numai ce scrie în document. "
    "Pentru fiecare element incluzi 'fragment' = citatul exact (max 300 caractere) din document din care rezultă "
    "și 'pagina' = numărul paginii din marcajul [pagina N], dacă există. Datele calendaristice în format AAAA-LL-ZZ."
)

PROMPTURI = {
    "cu_avize": REGULI_COMUNE + (
        " Documentul este un certificat de urbanism din România. Extrage: "
        '{"certificat": {"numar": "", "data": "", "emitent": "", "scop": "", "valabil_pana": ""}, '
        '"avize": [{"denumire": "", "tip": "", "emitent": "", "fragment": "", "pagina": ""}]}. '
        "În 'avize' include avizele, acordurile, studiile și documentele cerute pentru obținerea autorizației de "
        "construire (alimentare cu apă, canalizare, energie electrică, gaze, telefonizare, salubritate, ISU, DSP, mediu/APM, "
        "DSVSA, Apele Române, drumuri, studiu geotehnic, studiu topografic etc.), câte unul pe element. În formularul CU avizele "
        "sunt de obicei într-o listă cu căsuțe: include DOAR elementele bifate / marcate (X, ☒, ✓, „da”) și ignoră căsuțele goale; "
        "dacă nu se poate distinge bifa, include elementele enumerate ca cerute. 'tip' = unul dintre: aviz, acord, studiu, autorizatie, alt."
    ),
    "contract": REGULI_COMUNE + (
        " Documentul este un contract încheiat de o societate din grupul Carmistin. 'partener' = CEALALTĂ parte: cea care "
        "furnizează / execută / prestează / proiectează / vinde (denumită de obicei PRESTATOR, EXECUTANT, FURNIZOR, PROIECTANT, "
        "VÂNZĂTOR, CONSULTANT) sau, la sponsorizări, partea care NU face parte din grup. Partenerul NU este niciodată una dintre "
        "societățile grupului din lista dată. 'cui_partener' = CUI-ul partenerului. 'valoare' = valoarea TOTALĂ a contractului fără TVA, "
        "doar cifre; dacă contractul stabilește doar prețuri unitare (ex. lei/mp, euro/oră) lasă 'valoare' gol și adaugă prețul unitar "
        "ca o condiție de tip 'altele'. 'termen_executie' = data limită de finalizare, doar dacă e o dată calendaristică; gol dacă e "
        "exprimat în zile/luni. 'tip' = proiectare|executie|furnizare|servicii|dirigentie|altul. Extrage: "
        '{"contract": {"tip": "", "numar": "", "data": "", "partener": "", "cui_partener": "", "obiect": "", "valoare": "", '
        '"moneda": "", "termen_executie": "", "termen_plata_zile": "", "garantie_buna_executie": "", "perioada_garantie_luni": ""}, '
        '"conditii": [{"tip": "termen_executie|termen_plata|garantie|penalitate|receptie|avans|reziliere|obligatie|altele", '
        '"descriere": "", "termen": "", "valoare": "", "fragment": "", "pagina": ""}]}.'
    ),
    "autorizatie": REGULI_COMUNE + (
        " Documentul este o autorizație / un act de reglementare pentru funcționarea unui obiectiv (fermă, abator, depozit etc.). "
        "'tip' = isu (securitate la incendiu) | mediu (autorizație de mediu / integrată, APM, Garda de Mediu) | sanitar_veterinar (DSVSA, ANSVSA) | "
        "sanitar (DSP, sănătate publică) | gospodarire_ape (Apele Române, ABA) | drumuri (acces drum național, CNAIR) | iscir | "
        "metrologie (BRML) | functionare (primărie) | anre (ANRE, racordare) | anif | altele. "
        "'emitent' = instituția care a emis actul (ex. Inspectoratul pentru Situații de Urgență Olt). 'data' = data emiterii. "
        "'valabil_pana' = data până la care e valabilă; dacă se dă o durată (ex. 10 ani de la emitere) calculeaz-o; gol dacă e nelimitată. "
        "'viza_anuala' = true dacă actul trebuie vizat anual. 'conditii' = obligațiile / condițiile principale impuse titularului, pe scurt. "
        "'titular' = societatea titulară a autorizației. Extrage: "
        '{"autorizatie": {"tip": "", "denumire": "", "emitent": "", "numar": "", "data": "", "valabil_pana": "", "viza_anuala": false, '
        '"titular": "", "obiectiv": "", "conditii": ""}, '
        '"conditii": [{"tip": "obligatie", "descriere": "", "termen": "", "fragment": "", "pagina": ""}]}.'
    ),
    "finantare": REGULI_COMUNE + (
        " Documentul este un contract de finanțare (credit sau fonduri nerambursabile). Extrage: "
        '{"finantare": {"finantator": "", "nr_contract": "", "data_contract": "", "valoare": "", "valoare_eligibila": "", '
        '"procent_nerambursabil": "", "data_limita_implementare": "", "perioada_monitorizare_ani": ""}, '
        '"obligatii": [{"descriere": "", "termen": "", "recurenta": "", "fragment": "", "pagina": ""}]}. '
        "La 'obligatii' include toate obligațiile beneficiarului: raportări, termene, menținerea investiției, indicatori, "
        "publicitate, asigurări, cereri de plată etc."
    ),
}

# Regulă de rezervă pentru CU (fără model AI)
AVIZE_CHEIE = [
    ("alimentare cu ap", "Aviz alimentare cu apă"), ("canalizare", "Aviz canalizare"),
    ("energie electric", "Aviz amplasament / racordare energie electrică"), ("gaze", "Aviz alimentare cu gaze naturale"),
    ("telefoniz", "Aviz telefonizare"), ("salubri", "Aviz / contract salubritate"),
    ("securitate la incendiu", "Aviz securitate la incendiu (ISU)"), ("protecția civil", "Aviz protecție civilă (ISU)"),
    ("protectia civil", "Aviz protecție civilă (ISU)"), ("sănătatea popula", "Notificare / aviz sănătatea populației (DSP)"),
    ("sanatatea popula", "Notificare / aviz sănătatea populației (DSP)"), ("mediu", "Act administrativ autoritatea de mediu (APM)"),
    ("sanitar-veterinar", "Aviz sanitar-veterinar (DSVSA)"), ("sanitar veterinar", "Aviz sanitar-veterinar (DSVSA)"),
    ("apele rom", "Aviz de gospodărire a apelor (Apele Române)"), ("gospodărire a apelor", "Aviz de gospodărire a apelor"),
    ("drumuri", "Aviz administrator drum"), ("studiu geotehnic", "Studiu geotehnic"), ("topograf", "Ridicare topografică vizată OCPI"),
    ("cultur", "Aviz Direcția Județeană pentru Cultură"), ("transporturilor", "Aviz Ministerul Transporturilor"),
    ("aeronautic", "Aviz Autoritatea Aeronautică Civilă"), ("agricultur", "Aviz Direcția Agricolă (DAJ)"),
    ("ANIF", "Aviz ANIF"), ("silvic", "Aviz Garda Forestieră / ocol silvic"), ("performanța energetic", "Studiu performanță energetică"),
]


def _rezerva_cu(text):
    gasite, vazute = [], set()
    low = text.lower()
    for cheie, denumire in AVIZE_CHEIE:
        i = low.find(cheie.lower())
        if i >= 0 and denumire not in vazute:
            vazute.add(denumire)
            gasite.append({"denumire": denumire, "emitent": "", "fragment": text[max(0, i - 80): i + 160].strip(), "pagina": ""})
    return {"certificat": {}, "avize": gasite}


def ruleaza_extragere(document, tip):
    """Creează un ExtragereAI pentru document (status 'propus' sau 'eroare')."""
    from .models import ExtragereAI

    if not document.text_extras:
        document.text_extras = extrage_text(document.fisier.path)
        type(document).objects.filter(pk=document.pk).update(text_extras=document.text_extras)
    text = document.text_extras
    if text_util(text) < 50:
        return ExtragereAI.objects.create(
            document=document, tip=tip, status="eroare",
            mesaj="Documentul este scanat (doar imagini, fără text) și pe acest calculator nu există un program de recunoaștere "
                  "a textului (OCR). Pe server se folosește Tesseract cu limba română, inclus în instalare.",
        )
    ok, info = stare_ai()
    if ok:
        try:
            rez = _intreaba(PROMPTURI[tip] + (_context_grup(document) if tip == "contract" else ""), text)
            if tip == "contract":
                reguli = _rezerva_contract(text, document)
                c = rez.get("contract") or {}
                rez["contract"] = c
                # partenerul găsit după rol („denumit în continuare PRESTATOR”) e mai sigur decât al AI-ului;
                # iar dacă AI-ul a ales o societate din grup, îl corectăm
                rc = reguli["contract"]
                if rc.get("partener") and (reguli.get("partener_din_rol") or _e_din_grup(c.get("partener"))):
                    c["partener"], c["cui_partener"] = rc["partener"], rc.get("cui_partener", c.get("cui_partener", ""))
                elif _e_din_grup(c.get("partener")):
                    c["partener"], c["cui_partener"] = "", ""
                for k, v in rc.items():  # ce n-a găsit AI-ul, completează regulile
                    if v and not c.get(k):
                        c[k] = v
                        rez.setdefault("completat_din_reguli", []).append(k)
                rez["conditii"] = [x for x in (rez.get("conditii") or reguli["conditii"]) if str(x.get("descriere", "")).strip()]
            if tip == "autorizatie":  # completează din reguli ce lipsește
                a = rez.get("autorizatie") or {}
                rez["autorizatie"] = a
                for k, v in _rezerva_autorizatie(text, document)["autorizatie"].items():
                    if v and not a.get(k):
                        a[k] = v
            return ExtragereAI.objects.create(document=document, tip=tip, rezultat=rez, model_ai=info)
        except Exception as e:  # noqa: BLE001
            info = f"Eroare model AI: {e}"
    if tip in REZERVE:
        return ExtragereAI.objects.create(
            document=document, tip=tip, rezultat=REZERVE[tip](text, document), model_ai="reguli de recunoaștere (fără AI)",
            mesaj=f"{info}. S-a folosit recunoașterea pe reguli, mai puțin precisă decât AI-ul; verificați cu atenție fiecare câmp.",
        )
    return ExtragereAI.objects.create(document=document, tip=tip, status="eroare", mesaj=info)


# ---------------------------------------------------------------- contract: regulă de rezervă
_DATA = r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})"
_SUMA = r"(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"


def _fragment(text, m, inainte=60, dupa=160):
    return " ".join(text[max(0, m.start() - inainte): m.end() + dupa].split())


def _cauta(text, model, flags=re.I | re.S):
    return re.search(model, text, flags)


def _rezerva_contract(text, document=None):
    t = text
    rez, conditii = {}, []
    partener_din_rol = False
    beneficiar = getattr(getattr(getattr(document, "proiect", None), "beneficiar", None), "denumire", "") or ""
    cui_benef = getattr(getattr(getattr(document, "proiect", None), "beneficiar", None), "cui", "") or ""

    # numărul contractului: „contract ... nr. X”, dar nu numere de legi/hotărâri/ordonanțe citate în text
    m = _cauta(t, r"contract\w*(?:\s+(?!leg|hot[ăa]r|h\.?g|o\.?u\.?g|ordon|ordin|decret|art|dispozi)[\w\-ăâîșț]+){0,5}?\s*(?:nr\.?|num[ăa]r(?:ul)?)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)")
    if m:
        rez["numar"] = m.group(1).rstrip(".")
        md = re.search(_DATA, t[m.end(): m.end() + 80])
        if md:
            rez["data"] = f"{md.group(3)}-{int(md.group(2)):02d}-{int(md.group(1)):02d}"
    if "data" not in rez:  # prima dată plauzibilă, ignorând datele din citări de legi („Legea nr. 341/19.05.1994”)
        import datetime as _dt
        for md in re.finditer(_DATA, t):
            inainte = t[max(0, md.start() - 40): md.start()].lower()
            if re.search(r"leg|hot[ăa]r|h\.g|o\.u\.g|ordon|nr\.?\s*\d*\s*/?\s*$", inainte):
                continue
            if int(md.group(3)) < _dt.date.today().year - 15:
                continue
            rez["data"] = f"{md.group(3)}-{int(md.group(2)):02d}-{int(md.group(1)):02d}"
            break

    re_cui = r"(?:CUI|C\.U\.I\.|cod\s+(?:unic\s+)?de\s+[îi]nregistrare(?:\s+fiscal[ăa])?|cod\s+fiscal|CIF)\s*[:.]?\s*((?:RO\s?)?\d{4,10})"
    ciuri = [c.replace(" ", "") for c in re.findall(re_cui, t, re.I)]
    alte = [c for c in ciuri if c.replace("RO", "") != cui_benef.replace("RO", "").strip()] if cui_benef else ciuri[1:] or ciuri
    if alte:
        rez["cui_partener"] = alte[0]

    firme = re.findall(r"((?:S\.?C\.?\s+)?[A-ZĂÂÎȘȚ][A-Za-z0-9ĂÂÎȘȚăâîșț&.\- ]{1,60}?\s(?:S\.?R\.?L\.?|S\.?A\.?|AG|GmbH|S\.?N\.?C\.?))(?=[\s,;.])", t)
    # partenerul identificat după rolul din contract („denumit în continuare PRESTATOR / EXECUTANT / ...”)
    roluri = r"PRESTATOR|EXECUTANT|FURNIZOR|PROIECTANT|V[ÂA]NZ[ĂA]TOR|CONSULTANT|ANTREPRENOR|CONTRACTANT|PRESTATORUL|EXECUTANTUL|FURNIZORUL"
    for mp in re.finditer(r"(?:^|\n)\s*(?:\d\.\s*)?((?:S\.?C\.?\s+)?[A-ZĂÂÎȘȚ][^\n,]{2,100}?(?:S\.?\s?R\.?\s?L\.?|S\.?\s?A\.?|AG|GmbH))\s*,(.{0,900}?)denumit[ăa]?\s+(?:[îi]n\s+continuare\s+)?[„\"]?(" + roluri + r")", t, re.S | re.I):
        if _e_din_grup(mp.group(1)):
            continue
        rez["partener"] = " ".join(re.sub(r"^S\.?C\.?\s+", "", mp.group(1)).split())
        partener_din_rol = True
        mc = re.search(re_cui, mp.group(2), re.I)
        if mc:
            rez["cui_partener"] = mc.group(1).replace(" ", "")
        break
    cheie_benef = re.sub(r"[^a-z0-9]", "", beneficiar.lower())[:12]
    for f in ([] if "partener" in rez else firme):
        f = " ".join(f.split())
        f = re.sub(r"^(?:[îi]ntre|[șs]i|cu|de|la|c[ăa]tre|societatea|S\.?C\.?)\s+", "", f, flags=re.I)
        f = re.sub(r"^(?:[îi]ntre|[șs]i|S\.?C\.?)\s+", "", f, flags=re.I)
        if cheie_benef and cheie_benef in re.sub(r"[^a-z0-9]", "", f.lower()):
            continue
        rez["partener"] = f
        break

    m = _cauta(t, r"obiectul\s+(?:prezentului\s+)?contract(?:ului)?\s*(?:[îi]l\s+(?:constituie|reprezint[ăa])|este|const[ăa]\s+[îi]n)\s*[:\-]?\s*(.{15,500}?)(?:\.\s|\n\n)")
    if m:
        rez["obiect"] = " ".join(m.group(1).split())

    m = _cauta(t, r"(?:valoare[a]?|pre[țţt]ul)\s+(?:total[ăa]?\s+)?(?:a\s+|al\s+)?(?:prezentului\s+)?contract(?:ului)?[^0-9]{0,80}?" + _SUMA + r"\s*(lei|ron|eur|euro)\b(?!\s*/)")
    if m:
        rez["valoare"], rez["moneda"] = m.group(1), m.group(2)
    else:  # preț unitar (ex. „7 euro/mp”): nu e valoarea contractului, dar trebuie văzut
        m = _cauta(t, r"[^.\n]{0,80}pre[țţt]ul[^.\n]{0,80}?" + _SUMA + r"\s*(lei|ron|eur|euro)\s*/\s*\w+[^.]{0,150}\.")
        if m:
            rez["moneda"] = m.group(2)
            conditii.append({"tip": "altele", "descriere": "Preț unitar (valoarea totală nu e fixată în contract): " + " ".join(m.group(0).split()),
                             "valoare": f"{m.group(1)} {m.group(2)}", "fragment": _fragment(t, m, 0, 0), "pagina": ""})

    m = _cauta(t, r"termen(?:ul)?\s+de\s+(?:execu[țţt]ie|livrare|finalizare)[^.]{0,120}?(?:(\d+)\s*(zile|luni|s[ăa]pt[ăa]m[âa]ni)|" + _DATA + ")")
    if m:
        if m.group(5):
            rez["termen_executie"] = f"{m.group(5)}-{int(m.group(4)):02d}-{int(m.group(3)):02d}"
        conditii.append({"tip": "termen_executie", "descriere": " ".join(m.group(0).split()), "fragment": _fragment(t, m), "pagina": ""})

    m = _cauta(t, r"(?:plat[ăa]|achitar)[^.]{0,120}?(?:maxim\s+)?(\d+)\s*zile")
    if m:
        rez["termen_plata_zile"] = m.group(1)
        conditii.append({"tip": "termen_plata", "descriere": f"Plata în {m.group(1)} zile", "valoare": f"{m.group(1)} zile", "fragment": _fragment(t, m), "pagina": ""})

    m = _cauta(t, r"garan[țţt]i[ae]\s+de\s+bun[ăa]\s+execu[țţt]ie[^.]{0,200}\.")
    if m:
        rez["garantie_buna_executie"] = " ".join(m.group(0).split())[:200]
        conditii.append({"tip": "garantie", "descriere": " ".join(m.group(0).split()), "fragment": _fragment(t, m, 0, 0), "pagina": ""})

    m = _cauta(t, r"perioad[ae]\s+de\s+garan[țţt]ie[^.]{0,60}?(\d+)\s*luni")
    if m:
        rez["perioada_garantie_luni"] = m.group(1)
        conditii.append({"tip": "garantie", "descriere": f"Perioada de garanție: {m.group(1)} luni", "valoare": f"{m.group(1)} luni", "fragment": _fragment(t, m), "pagina": ""})

    propozitii = [" ".join(x.split()) for x in re.split(r"(?<=[.;])\s+(?=[A-ZĂÂÎȘȚ])|\n{2,}|\n(?=Art\.)", t) if x.strip()]
    for tip_c, model in (("penalitate", r"penalit"), ("avans", r"\bavans"), ("receptie", r"recep[țţt]i[ae]\s+(?:la\s+terminare|final)"),
                         ("reziliere", r"rezilie")):
        for prop in propozitii:
            if len(prop) > 40 and re.search(model, prop, re.I) and not any(prop == c["descriere"] for c in conditii):
                prop = re.sub(r"^Art\.\s*\d+\.?\s*", "", prop)
                conditii.append({"tip": tip_c, "descriere": prop[:400], "fragment": prop[:300], "pagina": ""})
                break

    low = t.lower()
    mt = re.search(r"contract\w*\s+de\s+(prest[ăa]ri\s+(?:de\s+)?servicii|servicii|execu[țţt]ie|furnizare|proiectare|consultan[țţt][ăa]|diriginte|dirigen|sponsorizare|v[âa]nzare|[îi]nchiriere|comodat)", low)
    if mt:
        rez["tip"] = {"prest": "servicii", "servi": "servicii", "execu": "executie", "furni": "furnizare", "proie": "proiectare",
                      "consu": "servicii", "dirig": "dirigentie"}.get(mt.group(1)[:5], "altul")
    elif re.search(r"\bsponsor", low):
        rez["tip"] = "altul"
    for cheie, tip in ([] if "tip" in rez else (("proiectare", "proiectare"), ("dirigen", "dirigentie"), ("execuție", "executie"), ("executie", "executie"),
                       ("furnizare", "furnizare"), ("livrare", "furnizare"), ("prestări servicii", "servicii"), ("servicii", "servicii"))):
        if cheie in low[:3000]:
            rez["tip"] = tip
            break
    return {"contract": rez, "conditii": conditii, "partener_din_rol": partener_din_rol}


def _data_iso(md):
    return f"{md.group(3)}-{int(md.group(2)):02d}-{int(md.group(1)):02d}"


def _rezerva_autorizatie(text, document=None):
    """Recunoaștere pe reguli pentru autorizații de funcționare (când modelul AI nu e disponibil)."""
    import datetime as _dt
    t, low = text, text.lower()
    rez = {}
    for cheie, tip in ((r"situa[țţt]ii\s+de\s+urgen|securitate\s+la\s+incendiu|\bisu\b", "isu"),
                       (r"sanitar[ă-]?\s*-?\s*veterinar|dsvsa|ansvsa", "sanitar_veterinar"),
                       (r"gospod[ăa]rire\s+a\s+apelor|apele\s+rom[âa]ne|administra[țţt]ia\s+bazinal", "gospodarire_ape"),
                       (r"autoriza[țţt]i[ae]\s+(?:integrat[ăa]\s+)?de\s+mediu|protec[țţt]ia\s+mediului|\bapm\b", "mediu"),
                       (r"\biscir\b", "iscir"), (r"s[ăa]n[ăa]tate\s+public|\bdsp\b", "sanitar"),
                       (r"autoriza[țţt]i[ae]\s+de\s+func[țţt]ionare|prim[ăa]ria", "functionare")):
        if re.search(cheie, low[:4000]):
            rez["tip"] = tip
            break
    m = re.search(r"autoriza[țţt]i[ae][^\n]{0,60}?\bnr\.?\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/.]*)", t, re.I) or \
        re.search(r"\bnr\.?\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)", t, re.I)
    if m:
        rez["numar"] = m.group(1).rstrip(".")
        md = re.search(_DATA, t[m.end(): m.end() + 60])
        if md:
            rez["data"] = _data_iso(md)
    mv = re.search(r"valabil[ăa]?\s+(?:p[âa]n[ăa]\s+la|pana\s+la)\s*(?:data\s+de\s*)?" + _DATA, t, re.I)
    if mv:
        rez["valabil_pana"] = f"{mv.group(3)}-{int(mv.group(2)):02d}-{int(mv.group(1)):02d}"
    else:
        md = re.search(r"valabil(?:itate)?[ăa]?[^.\n]{0,40}?(\d+)\s*ani", t, re.I)
        if md and rez.get("data"):
            d0 = _dt.date.fromisoformat(rez["data"])
            try:
                rez["valabil_pana"] = d0.replace(year=d0.year + int(md.group(1))).isoformat()
            except ValueError:
                pass
    if re.search(r"viz(?:a|ă|at[ăa])\s+anual", low):
        rez["viza_anuala"] = True
    me = re.search(r"((?:Inspectoratul|Agen[țţt]ia|Direc[țţt]ia|Administra[țţt]ia|Prim[ăa]ria|Garda|Autoritatea|ISCIR)[^\n]{3,120})", t)
    if me:
        rez["emitent"] = " ".join(me.group(1).split())[:200]
    mc = re.search(r"(?:condi[țţt]ii|obliga[țţt]ii)[^:\n]{0,40}:\s*(.{20,600}?)(?:\n\n|$)", t, re.I | re.S)
    if mc:
        rez["conditii"] = " ".join(mc.group(1).split())
    return {"autorizatie": rez, "conditii": []}


def initial_autorizatie(rezultat):
    a = (rezultat or {}).get("autorizatie", {}) or {}
    ini = {}
    tip = str(a.get("tip", "")).lower().strip()
    from .models import TipAutorizatie
    if tip in set(TipAutorizatie.objects.values_list("cod", flat=True)) | {"altele"}:
        ini["tip"] = tip
    for k, lim in (("denumire", 255), ("emitent", 200), ("numar", 60)):
        if a.get(k):
            ini[k] = str(a[k]).strip()[:lim]
    cond = a.get("conditii")
    extra = [c.get("descriere") for c in (rezultat or {}).get("conditii", []) or [] if c.get("descriere")]
    if isinstance(cond, list):
        cond = "; ".join(str(x) for x in cond)
    texte = [str(cond).strip()] if cond else []
    texte += [f"- {x}" for x in extra if x and x not in (cond or "")]
    if texte:
        ini["conditii"] = "\n".join(texte)[:3000]
    for k in ("data", "valabil_pana"):
        d = _data(a.get(k))
        if d:
            ini[k] = d
    if str(a.get("viza_anuala", "")).lower() in ("true", "1", "da", "yes"):
        ini["viza_anuala"] = True
    if ini.get("valabil_pana"):
        import datetime as _dt
        ini["status"] = "expirata" if ini["valabil_pana"] < _dt.date.today() else "valabila"
    return ini


REZERVE = {"cu_avize": lambda text, doc=None: _rezerva_cu(text), "contract": _rezerva_contract, "autorizatie": _rezerva_autorizatie}


# ---------------------------------------------------------------- normalizare pentru formular
def _numar(v):
    if v in (None, ""):
        return None
    s_ = re.sub(r"[^\d.,]", "", str(v))
    if not s_:
        return None
    if "," in s_:
        s_ = s_.replace(".", "").replace(",", ".")
    elif s_.count(".") > 1 or re.search(r"\.\d{3}$", s_):
        s_ = s_.replace(".", "")
    try:
        from decimal import Decimal
        return Decimal(s_)
    except Exception:  # noqa: BLE001
        return None


def _data(v):
    import datetime
    if not v:
        return None
    v = str(v).strip()
    try:
        return datetime.date.fromisoformat(v[:10])
    except ValueError:
        m = re.search(_DATA, v)
        if m:
            try:
                return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
    return None


def initial_contract(rezultat):
    """Transformă rezultatul extragerii în valori inițiale pentru formularul de contract."""
    c = (rezultat or {}).get("contract", {}) or {}
    ini = {}
    tip = str(c.get("tip", "")).lower()
    tip = {"execuție": "executie", "dirigenție": "dirigentie"}.get(tip, tip)
    if tip in ("proiectare", "executie", "furnizare", "servicii", "dirigentie", "altul"):
        ini["tip"] = tip
    for k in ("numar", "partener", "cui_partener", "obiect", "garantie_buna_executie"):
        if c.get(k):
            ini[k] = str(c[k]).strip()[:200 if k != "obiect" else 2000]
    moneda = str(c.get("moneda", "")).lower()
    if moneda:
        ini["moneda"] = "EUR" if moneda.startswith("eur") or moneda == "€" else "RON"
    v = _numar(c.get("valoare"))
    if v is not None:
        ini["valoare"] = v
    for k in ("data", "termen_executie"):
        d = _data(c.get(k))
        if d:
            ini[k] = d
    for k in ("termen_plata_zile", "perioada_garantie_luni"):
        n = _numar(c.get(k))
        if n is not None:
            ini[k] = int(n)
    return ini


# ---------------------------------------------------------------- citire generică pentru orice formular
INDICII = {
    "CarteFunciara": " Documentul este un extras de carte funciară din România. 'numar' = nr. cărții funciare; 'uat' = unitatea "
                     "administrativ-teritorială; 'nr_cadastral' = nr. cadastral / topografic; 'suprafata_mp' = suprafața TERENULUI "
                     "(partea I, A); 'suprafata_constructii_mp' = SUMA suprafețelor construite la sol ale tuturor construcțiilor "
                     "(C1, C2, …) din partea I; 'proprietar_tabular' = titularul dreptului de proprietate din partea a II-a; "
                     "'sarcini' = ipoteci, interdicții, privilegii din partea a III-a (gol dacă nu există); 'data_extras' = data emiterii extrasului.",
    "RaportEvaluare": " Documentul este un raport de evaluare a unei proprietăți imobiliare (standardele ANEVAR / SEV). "
                      "'data' = data evaluării (data la care e exprimată opinia asupra valorii) sau, dacă lipsește, data raportului; "
                      "'evaluator' = firma / evaluatorul autorizat ANEVAR; 'valoare_piata' = VALOAREA DE PIAȚĂ FINALĂ (opinia finală "
                      "asupra valorii, fără TVA) – nu valoarea de garantare, nu valoarea de lichidare, nu valoarea unei singure clădiri; "
                      "'valoare_garantare' = valoarea pentru garantarea împrumutului, doar dacă e menționată separat; 'moneda' = EUR sau RON "
                      "(moneda în care e exprimată valoarea finală; dacă e dată în ambele, alege EUR); 'scop' = scopul evaluării; "
                      "'beneficiar_raport' = clientul / destinatarul raportului (ex. banca).",
    "ControlAutoritate": " Documentul este un proces-verbal / o notă de constatare emisă de o autoritate de control.",
    "ContractFolosinta": " Documentul este un act privind dreptul de folosință al unui imobil (proprietate, închiriere, superficie, concesiune, "
                         "arendă, comodat). 'chirie' = valoarea LUNARĂ a plății (dacă e anuală, împarte la 12).",
    "ActAditional": " Documentul este un act adițional la un contract.",
    "StandardCertificat": " Documentul este un certificat de conformitate cu un standard (ISO 9001, ISO 14001, ISO 45001, ISO 22000/FSSC, "
                          "IFS Food, BRCGS, GLOBALG.A.P., ISO 50001). 'organism' = organismul de certificare (ex. SRAC, TÜV, Bureau Veritas, SGS); "
                          "'domeniu' = domeniul / scopul certificat; 'nivel' = nivelul sau scorul (ex. Higher Level, Grade A).",
}


def descriere_campuri(form):
    """Lista câmpurilor unui formular Django, pentru promptul AI."""
    from django import forms as dj
    campuri = []
    for nume, f in form.fields.items():
        if nume in ("scan", "anexe", "citeste_ai", "extragere") or isinstance(f, dj.ModelMultipleChoiceField):
            continue
        if isinstance(f, dj.ModelChoiceField):
            continue
        tip = "text"
        if isinstance(f, dj.DateField):
            tip = "dată AAAA-LL-ZZ"
        elif isinstance(f, (dj.DecimalField, dj.FloatField)):
            tip = "număr"
        elif isinstance(f, dj.IntegerField):
            tip = "număr întreg"
        elif isinstance(f, dj.BooleanField):
            tip = "true/false"
        elif isinstance(f, dj.ChoiceField):
            tip = "una dintre: " + ", ".join(f"{k} ({v})" for k, v in f.choices if k)
        d = f"- {nume}: {f.label} [{tip}]"
        if f.help_text:
            d += f" – {f.help_text}"
        campuri.append(d)
    return "\n".join(campuri)


def extrage_campuri(document, form, model_nume=""):
    """Citește documentul cu AI și propune valori pentru câmpurile formularului. Întoarce (valori, mesaj)."""
    if not document.text_extras:
        document.text_extras = extrage_text(document.fisier.path)
        type(document).objects.filter(pk=document.pk).update(text_extras=document.text_extras)
    if text_util(document.text_extras) < 50:
        return {}, "Documentul nu conține text și nu a putut fi citit (OCR indisponibil)."
    ok, info = stare_ai()
    if not ok:
        return {}, f"{info}. Datele nu au putut fi citite automat."
    instr = (REGULI_COMUNE.split("Pentru fiecare element")[0] + INDICII.get(model_nume, "") +
             " Completează câmpurile de mai jos cu valorile din document. Răspunde cu un obiect JSON {nume_câmp: valoare}; "
             "pune doar câmpurile găsite în document, fără să inventezi. Numerele doar cu cifre și punct zecimal.\n" + descriere_campuri(form))
    try:
        rez = _intreaba(instr, document.text_extras)
    except Exception as e:  # noqa: BLE001
        return {}, f"Eroare la citirea cu AI: {e}"
    return normalizeaza_pentru_formular(rez if isinstance(rez, dict) else {}, form), ""


def normalizeaza_pentru_formular(valori, form):
    from django import forms as dj
    ini = {}
    for nume, v in valori.items():
        f = form.fields.get(nume)
        if f is None or v in (None, "", [], {}):
            continue
        if isinstance(f, dj.DateField):
            v = _data(v)
        elif isinstance(f, (dj.DecimalField, dj.FloatField, dj.IntegerField)):
            v = _numar(v)
            if v is not None and isinstance(f, dj.IntegerField):
                v = int(v)
        elif isinstance(f, dj.BooleanField):
            v = str(v).lower() in ("true", "1", "da", "yes")
        elif isinstance(f, dj.ChoiceField):
            chei = {str(k).lower(): k for k, _ in f.choices if k}
            etichete = {str(e).lower(): k for k, e in f.choices if k}
            sv = str(v).lower().strip()
            v = chei.get(sv) or etichete.get(sv) or next((k for e, k in etichete.items() if sv and sv in e), None)
        else:
            v = str(v).strip()
            lim = getattr(f, "max_length", None)
            if lim:
                v = v[:lim]
        if v not in (None, ""):
            ini[nume] = v
    return ini
