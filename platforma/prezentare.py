"""Tab-ul „Prezentare” al proiectului (varianta C): rezumat cu indicatori în stânga, cronologia proiectului în dreapta."""
from decimal import Decimal

from django.apps import apps
from django.utils import timezone

from . import models as m

# categoria fiecărui tip de eveniment din jurnal (după denumirea modelului) – pentru filtrele cronologiei
CATEGORII = {
    "buget": ("Buget", "bi-cash-coin", {"linie buget", "rectificare buget", "factură", "plată", "contract", "act adițional",
                                         "alocare factură pe linie buget", "alocare contract pe linie buget", "buget anual estimat",
                                         "condiție contract", "echipament", "sursă de finanțare", "obligație / termen", "cerere de tragere"}),
    "autorizare": ("Autorizare", "bi-patch-check", {"aviz", "certificat de urbanism"}),
    "santier": ("Șantier", "bi-cone-striped", {"raport de șantier", "raport de activitate", "etapă proiect", "punct snag list", "activitate"}),
    "documente": ("Documente", "bi-file-earmark", {"document"}),
    "proiect": ("Proiect", "bi-kanban", {"proiect de investiții", "revizie Raport Zero", "cerere de schimbare a statusului", "membru echipă proiect",
                                         "sarcină alocată", "acces proiect", "email de avizare RZ", "proiect – alocare activ"}),
}
EXCLUSE = {"folder documente", "notificare", "ștergere proiect", "proiect (definitiv, 2FA)"}
VERBE = {"creare": "adăugat", "modificare": "modificat", "stergere": "șters"}
CAMPURI_IMPORTANTE = ("status", "stare", "stare_rz", "denumire", "progres", "final_real", "valoare_ron", "suma_ron", "numar", "revizie_rz")


def _modele():
    rez = {}
    for model in apps.get_app_config("platforma").get_models():
        rez[str(model._meta.verbose_name)] = model
    return rez


def _valoare(v):
    if v in (None, ""):
        return "—"
    s = str(v)
    if s in ("True", "False"):
        return "da" if s == "True" else "nu"
    return s if len(s) <= 40 else s[:38] + "…"


def cronologie(p, limita=400):
    modele = _modele()
    categ = {model: cheie for cheie, (_, _, nume) in CATEGORII.items() for model in nume}
    evenimente = []
    for j in p.jurnal.select_related("user").order_by("-moment")[:limita]:
        if j.model in EXCLUSE:
            continue
        cat = categ.get(j.model, "proiect")
        model = modele.get(j.model)
        detalii = []
        if j.actiune == "modificare" and isinstance(j.modificari, dict):
            chei = sorted(j.modificari, key=lambda k: (k not in CAMPURI_IMPORTANTE, k))
            for k in chei:
                if k.endswith("_id") or k in ("id", "sha256", "text_extras", "modificat_la", "detalii", "curba_consum"):
                    continue
                try:
                    eticheta = str(model._meta.get_field(k).verbose_name) if model else k
                except Exception:
                    eticheta = k
                v = j.modificari[k]
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    detalii.append(f"{eticheta}: {_valoare(v[0])} → {_valoare(v[1])}")
                if len(detalii) >= 2:
                    break
        evenimente.append({"moment": j.moment, "user": (j.user.get_full_name() or j.user.username) if j.user else "sistem",
                           "cat": cat, "iconita": CATEGORII[cat][1], "model": j.model, "verb": VERBE.get(j.actiune, j.actiune),
                           "actiune": j.actiune, "obiect": j.obiect, "detalii": detalii})
    # evenimentele identice consecutive (ex. zeci de linii de buget salvate odată) se grupează
    grupate = []
    for e in evenimente:
        ult = grupate[-1] if grupate else None
        if ult and ult["model"] == e["model"] and ult["actiune"] == e["actiune"] and ult["user"] == e["user"] \
                and abs((ult["moment"] - e["moment"]).total_seconds()) < 120:
            ult["nr"] += 1
            continue
        grupate.append({**e, "nr": 1})
    return grupate[:80]


def indicatori(p):
    """Bulinele de stare (verde / galben / roșu / gri) și cifrele de buget din rezumat."""
    azi = timezone.localdate()
    buget = p.buget_actualizat_ron or p.buget_rz_ron or Decimal(0)
    fact, ang, plat = p.facturat_ron, p.angajat_ron, p.platit_ron
    pct = lambda x: round(float(x) / float(buget) * 100) if buget else 0  # noqa: E731
    b = {"buget": buget, "facturat": fact, "angajat": ang, "platit": plat,
         "pct_facturat": pct(fact), "pct_angajat": pct(ang), "pct_platit": pct(plat)}
    ind = []
    avize = list(p.avize.all())
    cu = p.certificate_urbanism.first()
    if cu:
        exp = cu.valabil_pana and cu.valabil_pana < azi
        ind.append(("r" if exp else "g", f"CU {'expirat' if exp else 'obținut'}", "avize"))
    elif p.tip_autorizare == "cu_ac" or avize:
        ind.append(("s", "CU neîncărcat", "avize"))
    if p.tip_autorizare == "cu_ac":
        ac = [a for a in avize if a.tip == "autorizatie" or ("autoriza" in a.denumire.lower() and "constru" in a.denumire.lower())
              or a.denumire.strip().upper() in ("AC", "A.C.")]
        are_doc = p.documente.filter(categorie="autorizatie", arhivat=False).exists()
        if are_doc or any(a.status == "obtinut" for a in ac):
            ind.append(("g", "AC obținut", "avize"))
        elif ac:
            ind.append(("a", f"AC {ac[0].get_status_display().lower()}", "avize"))
        else:
            ind.append(("r", "AC neobținut", "avize"))
    if avize:
        ob = sum(1 for a in avize if a.status == "obtinut")
        ind.append(("g" if ob == len(avize) else "a", f"Avize {ob}/{len(avize)} obținute", "avize"))
    etape = list(p.etape.all())
    if etape:
        princ = [e for e in etape if not e.parinte_id]
        gata = sum(1 for e in princ if e.status == "finalizata")
        intarz = sum(1 for e in etape if e.intarziata)
        ind.append(("g" if not intarz else "a", f"Etape {gata}/{len(princ)} finalizate", "monitorizare"))
        if intarz:
            ind.append(("r", f"{intarz} etap{'ă' if intarz == 1 else 'e'} întârziat{'ă' if intarz == 1 else 'e'}", "monitorizare"))
    if p.status == "În execuție" and p.echipa.filter(categorie="teren").exists() and azi.weekday() < 5:
        azi_ok = p.rapoarte_activitate.filter(data=azi, stare="emis").exists()
        ind.append(("g" if azi_ok else "a", "Raport de șantier azi" if azi_ok else "Fără raport de șantier azi", "monitorizare"))
    snag = m.PunctSnag.objects.filter(raport__proiect=p, raport__stare="emis", rezolvat=False).count()
    if snag:
        ind.append(("a", f"Snag list: {snag} deschise", "monitorizare"))
    if p.fara_termen:
        ind.append(("s", "Termen FT", None))
    elif p.data_finalizare:
        ind.append(("r" if p.intarziat else "g", f"Termen {p.data_finalizare:%d.%m.%Y}" + (" depășit" if p.intarziat else ""), None))
    return {"buget_pr": b, "indicatori": ind}


def context_prezentare(p):
    ev = cronologie(p)
    return {**indicatori(p), "cronologie": ev,
            "cronologie_categorii": [(k, v[0], v[1], sum(1 for e in ev if e["cat"] == k)) for k, v in CATEGORII.items()]}
