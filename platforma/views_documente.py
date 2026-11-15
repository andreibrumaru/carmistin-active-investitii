"""Managementul documentelor proiectului: foldere / subfoldere, încărcare multiplă, versiuni, mutare, căutare."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from . import models as m
from .permisiuni import poate_edita, poate_raporta, proiecte_vizibile

# structura standard creată la prima deschidere; documentele existente se încadrează după categorie
STRUCTURA = [
    ("01 Raport Zero și aprobare", "raport_zero,oferta"),
    ("02 Studii și proiectare", "studiu,deviz,grafic"),
    ("03 Certificat de urbanism și avize", "cu,aviz"),
    ("04 Autorizații", "autorizatie"),
    ("05 Contracte", "contract,act_aditional"),
    ("06 Facturi și plăți", "factura,plata"),
    ("07 Finanțare", "finantare,cerere_tragere,raport_progres"),
    ("08 Execuție și fotografii", "foto,proces_verbal,aviz_insotire"),
    ("09 Recepție și închidere", "pv_inchidere"),
    ("10 Altele", "*"),
]
EXTENSII = {"pdf": "bi-file-earmark-pdf text-danger", "doc": "bi-file-earmark-word text-primary", "docx": "bi-file-earmark-word text-primary",
            "xls": "bi-file-earmark-excel text-success", "xlsx": "bi-file-earmark-excel text-success", "csv": "bi-file-earmark-spreadsheet text-success",
            "jpg": "bi-file-earmark-image text-info", "jpeg": "bi-file-earmark-image text-info", "png": "bi-file-earmark-image text-info",
            "heic": "bi-file-earmark-image text-info", "webp": "bi-file-earmark-image text-info", "dwg": "bi-file-earmark-ruled text-warning",
            "zip": "bi-file-earmark-zip text-secondary", "rar": "bi-file-earmark-zip text-secondary"}


def asigura_structura(p, user=None):
    """Creează structura standard de foldere (o singură dată) și încadrează documentele fără folder."""
    if not p.foldere.exists():
        with transaction.atomic():
            for i, (nume, cat) in enumerate(STRUCTURA, start=1):
                m.FolderDocumente.objects.create(proiect=p, nume=nume, ordine=i, categorii=f",{cat},", creat_de=user)
    fara = list(p.documente.filter(folder__isnull=True))
    if fara:
        foldere = list(p.foldere.exclude(categorii=""))
        rezerva = next((f for f in foldere if ",*," in f.categorii), None)
        for d in fara:
            f = next((f for f in foldere if f",{d.categorie}," in f.categorii), rezerva)
            if f:
                m.Document.objects.filter(pk=d.pk).update(folder=f)


def _proiect(request, pk):
    p = get_object_or_404(m.Proiect, pk=pk)
    if not proiecte_vizibile(request.user).filter(pk=p.pk).exists():
        raise PermissionDenied("Nu aveți drept de acces la acest proiect.")
    return p


def _inapoi(p, folder=None):
    return redirect(f"/proiecte/{p.pk}/?tab=documente" + (f"&folder={folder.pk if hasattr(folder, 'pk') else folder}" if folder else ""))


def _marime(n):
    if n is None:
        return ""
    return f"{n / 1048576:.1f} MB".replace(".", ",") if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def context_documente(request, p):
    u, g = request.user, request.GET
    asigura_structura(p, u)
    foldere = list(p.foldere.all())
    docs = list(p.documente.select_related("incarcat_de", "contract", "factura", "aviz", "certificat", "finantare"))
    activi = [d for d in docs if not d.arhivat]
    directe = {}
    for d in activi:
        directe[d.folder_id] = directe.get(d.folder_id, 0) + 1
    copii = {}
    for f in foldere:
        copii.setdefault(f.parinte_id, []).append(f)

    def total(f):
        return directe.get(f.pk, 0) + sum(total(c) for c in copii.get(f.pk, []))

    def arbore(parinte, nivel):
        rez = []
        for f in copii.get(parinte, []):
            rez.append({"f": f, "nivel": nivel, "nr": total(f), "are_copii": bool(copii.get(f.pk)), "indent": "\u00a0\u00a0\u00a0" * nivel})
            rez += arbore(f.pk, nivel + 1)
        return rez
    curent = next((f for f in foldere if str(f.pk) == g.get("folder")), None)
    cale, x = [], curent
    while x is not None:
        cale.insert(0, x)
        x = next((f for f in foldere if f.pk == x.parinte_id), None)
    q = (g.get("q") or "").strip() if g.get("tab") == "documente" else ""
    arhivate = g.get("arhivate") == "1"
    if q:
        lista = [d for d in docs if (arhivate or not d.arhivat) and q.lower() in (d.titlu + " " + d.fisier.name).lower()]
    else:
        lista = [d for d in docs if d.folder_id == (curent.pk if curent else None) and (arhivate or not d.arhivat)]
    lista.sort(key=lambda d: (d.titlu.lower(), -d.versiune))
    nume_folder = {f.pk: f for f in foldere}
    for d in lista:
        ext = d.fisier.name.rsplit(".", 1)[-1].lower() if "." in d.fisier.name else ""
        d.iconita = EXTENSII.get(ext, "bi-file-earmark text-secondary")
        d.extensie = ext.upper()
        try:
            d.marime = _marime(d.fisier.size)
        except Exception:
            d.marime = "lipsă fișier"
        d.folder_obj = nume_folder.get(d.folder_id)
    return {
        "dm_arbore": arbore(None, 0), "dm_curent": curent, "dm_cale": cale, "dm_q": q, "dm_arhivate": arhivate,
        "dm_subfoldere": [{"f": f, "nr": total(f)} for f in copii.get(curent.pk if curent else None, [])] if not q else [],
        "dm_documente": lista, "dm_total": len(activi), "documente_arhivate": sum(1 for d in docs if d.arhivat),
        "dm_toate_folderele": arbore(None, 0),
        "dm_poate_incarca": poate_raporta(u, p), "dm_poate_organiza": poate_edita(u, p),
    }


def _exista(p, parinte, nume, exceptie=None):
    """Nume de folder deja folosit la același nivel (fără deosebire de majuscule, inclusiv pentru diacritice – SQLite nu le compară)."""
    return any(f.nume.casefold() == nume.casefold() for f in p.foldere.filter(parinte=parinte).exclude(pk=getattr(exceptie, "pk", None)))


def _folder(p, pk):
    return get_object_or_404(m.FolderDocumente, pk=pk, proiect=p) if pk else None


@login_required
@require_POST
def folder_nou(request, pk):
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied("Folderele le organizează cine are drept de editare pe proiect.")
    parinte = _folder(p, request.POST.get("parinte"))
    nume = request.POST.get("nume", "").strip()[:120]
    if not nume:
        messages.error(request, "Scrieți numele folderului.")
    elif _exista(p, parinte, nume):
        messages.error(request, f"Există deja folderul „{nume}” aici.")
    else:
        ordine = (p.foldere.filter(parinte=parinte).aggregate(m=Max("ordine"))["m"] or 0) + 1
        f = m.FolderDocumente.objects.create(proiect=p, parinte=parinte, nume=nume, ordine=ordine, creat_de=request.user)
        messages.success(request, f"Folderul „{f.cale}” a fost creat.")
    return _inapoi(p, parinte)


@login_required
@require_POST
def folder_modifica(request, pk, fpk):
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    f = _folder(p, fpk)
    if request.POST.get("actiune") == "sterge":
        if f.subfoldere.exists() or f.documente.exists():
            messages.error(request, f"Folderul „{f.nume}” nu e gol: mutați întâi documentele și subfolderele (inclusiv versiunile arhivate).")
            return _inapoi(p, f)
        parinte = f.parinte
        f.delete()
        messages.success(request, f"Folderul „{f.nume}” a fost șters.")
        return _inapoi(p, parinte)
    nume = request.POST.get("nume", "").strip()[:120]
    if nume and nume != f.nume:
        if _exista(p, f.parinte, nume, f):
            messages.error(request, f"Există deja folderul „{nume}” aici.")
        else:
            f.nume = nume
            f.save(update_fields=["nume"])
            messages.success(request, f"Folder redenumit: „{nume}”.")
    return _inapoi(p, f)


@login_required
@require_POST
def documente_incarca(request, pk):
    p = _proiect(request, pk)
    if not poate_raporta(request.user, p):
        raise PermissionDenied("Documentele le încarcă responsabilii și membrii echipei proiectului.")
    f = _folder(p, request.POST.get("folder"))
    fisiere = request.FILES.getlist("fisiere")[:50]
    if not fisiere:
        messages.error(request, "Alegeți cel puțin un fișier.")
        return _inapoi(p, f)
    cat, x = None, f  # categoria documentelor: a folderului sau, la subfoldere, a primului părinte care are una
    while x is not None and cat is None:
        cat = next((c for c in x.categorii.strip(",").split(",") if c and c != "*"), None)
        x = x.parinte
    imagini = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
    noi = versiuni = 0
    with transaction.atomic():
        for x in fisiere:
            titlu = x.name.rsplit(".", 1)[0].replace("_", " ").strip()[:255] or x.name[:255]
            veche = p.documente.filter(folder=f, titlu=titlu, arhivat=False).order_by("-versiune").first()
            categorie = veche.categorie if veche else (cat or ("foto" if x.name.lower().endswith(imagini) else "altul"))
            m.Document.objects.create(proiect=p, folder=f, categorie=categorie, titlu=titlu, fisier=x,
                                      incarcat_de=request.user, inlocuieste=veche)
            if veche:
                versiuni += 1
            else:
                noi += 1
    parti = []
    if noi:
        parti.append(f"{noi} document{'e' if noi != 1 else ''} încărcat{'e' if noi != 1 else ''}")
    if versiuni:
        parti.append(f"{versiuni} versiun{'i' if versiuni != 1 else 'e'} nou{'ă' if versiuni == 1 else 'i'} (cea veche a fost arhivată)")
    messages.success(request, " · ".join(parti) + (f" în „{f.cale}”." if f else "."))
    return _inapoi(p, f)


@login_required
@require_POST
def documente_muta(request, pk):
    p = _proiect(request, pk)
    if not poate_edita(request.user, p):
        raise PermissionDenied
    tinta = _folder(p, request.POST.get("tinta"))
    ids = [int(x) for x in request.POST.getlist("doc") if x.isdigit()]
    docs = list(p.documente.filter(pk__in=ids))
    if not docs or tinta is None:
        messages.error(request, "Bifați documentele și alegeți folderul în care se mută.")
        return _inapoi(p, request.POST.get("folder"))
    # versiunile anterioare ale documentelor se mută împreună cu ele
    lant = set()
    for d in docs:
        x = d
        while x is not None and x.pk not in lant:
            lant.add(x.pk)
            x = x.inlocuieste
    m.Document.objects.filter(pk__in=lant).update(folder=tinta)
    messages.success(request, f"{len(docs)} document{'e' if len(docs) != 1 else ''} mutat{'e' if len(docs) != 1 else ''} în „{tinta.cale}”.")
    return _inapoi(p, request.POST.get("folder"))
