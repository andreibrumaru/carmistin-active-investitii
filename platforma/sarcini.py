"""Sarcinile fiecărui cont, structurate pe proiecte, generate din alocări:
  • etapele / subetapele unde persoana este responsabil (nefinalizate);
  • zilnic, raportul de șantier pentru proiectele în execuție unde persoana este „Responsabil teren” în echipă.
"""
import datetime

from django.utils import timezone

from . import models as m
from . import status_investitie as stinv

ORIZONT_ZILE = 7  # „în curând”: termen în următoarele 7 zile


def _zile_lucratoare_fara_raport(proiect, user, azi, zile=7):
    """Zilele lucrătoare din ultima săptămână (fără azi) în care persoana nu a emis raport de șantier."""
    emise = set(m.RaportActivitate.objects.filter(proiect=proiect, autor=user, stare="emis",
                                                  data__gte=azi - datetime.timedelta(days=zile)).values_list("data", flat=True))
    lipsa = []
    for i in range(1, zile + 1):
        d = azi - datetime.timedelta(days=i)
        if d.weekday() < 5 and d not in emise:
            lipsa.append(d)
    return lipsa


def sarcini_utilizator(user, azi=None):
    """Lista proiectelor cu sarcinile utilizatorului: [{proiect, roluri, sarcini: [...], intarziate, azi}] + totaluri."""
    azi = azi or timezone.localdate()
    membri = list(m.MembruEchipa.objects.filter(user=user).select_related("proiect"))
    pe_proiect = {}

    def grup(p):
        return pe_proiect.setdefault(p.pk, {"proiect": p, "roluri": [], "sarcini": []})
    for mb in membri:
        g = grup(mb.proiect)
        if mb.get_categorie_display() not in g["roluri"]:
            g["roluri"].append(mb.get_categorie_display())

    # 1. raportul zilnic de șantier – pentru „Responsabil teren”, pe proiectele în execuție
    for mb in membri:
        p = mb.proiect
        if mb.categorie != "teren" or p.status != stinv.IN_EXECUTIE:
            continue
        if azi.weekday() >= 5 and not m.RaportActivitate.objects.filter(proiect=p, autor=user, data=azi).exists():
            continue  # sâmbăta și duminica raportul nu e obligatoriu (apare doar dacă a fost început)
        if any(s["tip"] == "raport" for s in grup(p)["sarcini"]):
            continue
        r = m.RaportActivitate.objects.filter(proiect=p, autor=user, data=azi).order_by("-stare").first()
        stare = "gata" if r and r.stare == "emis" else "ciorna" if r else "de_facut"
        grup(p)["sarcini"].append({
            "tip": "raport", "titlu": f"Raport de șantier – {azi:%d.%m.%Y}", "stare": stare, "raport": r,
            "termen": azi, "intarziata": False, "azi": stare != "gata",
            "lipsa": _zile_lucratoare_fara_raport(p, user, azi),
        })

    # 2. etapele și subetapele alocate (nefinalizate)
    for e in (m.EtapaProiect.objects.filter(responsabil__user=user).exclude(status="finalizata")
              .select_related("proiect", "parinte").order_by("proiect", "final_plan", "ordine")):
        intarziata = bool(e.final_plan and e.final_plan < azi)
        grup(e.proiect)["sarcini"].append({
            "tip": "etapa", "etapa": e, "titlu": e.denumire, "parinte": e.parinte.denumire if e.parinte_id else "",
            "stare": e.status, "termen": e.final_plan, "intarziata": intarziata,
            "azi": bool(e.final_plan and e.final_plan == azi),
            "curand": bool(e.final_plan and azi <= e.final_plan <= azi + datetime.timedelta(days=ORIZONT_ZILE)),
        })

    # 3. sarcinile alocate de responsabilul proiectului (deschise)
    for s in (m.SarcinaAlocata.objects.filter(responsabili=user, stare="deschisa")
              .select_related("proiect", "creat_de").prefetch_related("responsabili")):
        grup(s.proiect)["sarcini"].append({
            "tip": "sarcina", "obj": s, "titlu": s.descriere, "stare": s.stare, "termen": s.termen, "intarziata": bool(s.intarziata),
            "azi": bool(s.termen and s.termen == azi),
            "curand": bool(s.termen and azi <= s.termen <= azi + datetime.timedelta(days=ORIZONT_ZILE)),
        })

    grupuri = [g for g in pe_proiect.values() if g["sarcini"]]
    from .permisiuni import proiecte_vizibile
    vizibile = set(proiecte_vizibile(user).filter(pk__in=[g["proiect"].pk for g in grupuri]).values_list("pk", flat=True))
    for g in grupuri:  # sarcinile alocate unui coleg din afara echipei: vede sarcina, dar nu are acces la fișa proiectului
        g["vizibil"] = g["proiect"].pk in vizibile
    for g in grupuri:
        g["sarcini"].sort(key=lambda s: (s["tip"] != "raport", not s["intarziata"], s["termen"] or datetime.date.max))
        g["intarziate"] = sum(1 for s in g["sarcini"] if s["intarziata"])
        g["de_azi"] = sum(1 for s in g["sarcini"] if s["azi"])
        g["deschise"] = sum(1 for s in g["sarcini"] if s["stare"] != "gata")
    grupuri.sort(key=lambda g: (-g["intarziate"], -g["de_azi"], g["proiect"].cod))
    toate = [s for g in grupuri for s in g["sarcini"]]
    return {
        "grupuri": grupuri,
        "total": sum(1 for s in toate if s["stare"] != "gata"),
        "intarziate": sum(1 for s in toate if s["intarziata"]),
        "azi": sum(1 for s in toate if s["azi"]),
        "curand": sum(1 for s in toate if s.get("curand") and not s["azi"]),
        "rapoarte_azi": sum(1 for s in toate if s["tip"] == "raport" and s["stare"] != "gata"),
    }


def nr_sarcini_urgente(user):
    """Pentru insigna din meniu: sarcinile de azi + cele întârziate (fără să construiască toată lista)."""
    azi = timezone.localdate()
    etape = m.EtapaProiect.objects.filter(responsabil__user=user, final_plan__lte=azi).exclude(status="finalizata").count()
    teren = m.MembruEchipa.objects.filter(user=user, categorie="teren", proiect__status=stinv.IN_EXECUTIE).values_list("proiect_id", flat=True).distinct()
    if azi.weekday() >= 5:
        teren = []
    cu_raport = set(m.RaportActivitate.objects.filter(autor=user, data=azi, stare="emis", proiect_id__in=list(teren)).values_list("proiect_id", flat=True))
    alocate = m.SarcinaAlocata.objects.filter(responsabili=user, stare="deschisa", termen__lte=azi).count()
    return etape + alocate + sum(1 for x in set(teren) if x not in cu_raport)
