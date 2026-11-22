"""Setări personale ale contului: mărimea afișării (reținută pe cont, se aplică pe orice calculator)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from . import models as m

# variantele propuse, cu recomandarea de monitor / calculator pentru fiecare
VARIANTE = [
    (85, "Compact", "Laptopuri mici (11–13\"), ecrane 1280×800 sau 1366×768. Încap mai multe coloane în registre."),
    (90, "Mai mic", "Laptopuri 14–15\" (1440×900, 1536×864) sau Windows cu scalare 125–150%."),
    (100, "Recomandat", "Mărimea actuală a aplicației. Potrivită pentru majoritatea monitoarelor 22–24\" (1920×1080) și MacBook-uri."),
    (110, "Mărit", "Monitoare 24–27\" privite de la distanță sau 2560×1440 cu scalare 100%."),
    (125, "Mare", "Monitoare mari 27–32\" (2560×1440, 4K fără scalare) sau lectură mai comodă."),
    (140, "Foarte mare", "Ecrane 4K mari, TV în sala de ședințe sau pentru o lizibilitate maximă."),
]


@login_required
def cont_afisare(request):
    profil, _ = m.Profil.objects.get_or_create(user=request.user)
    permise = {v for v, _ in m.Profil.SCARI_AFISARE}
    if request.method == "POST":
        try:
            v = int(request.POST.get("scara", "100"))
        except ValueError:
            v = 100
        if v not in permise:
            v = 100
        profil.scara_afisare = v
        profil.save(update_fields=["scara_afisare"])
        messages.success(request, "Mărimea afișării a fost salvată pe contul dumneavoastră: "
                         + dict(m.Profil.SCARI_AFISARE)[v].replace(" – ", " (") + (")" if v else "") + ". Se aplică pe orice calculator pe care vă conectați.")
        return redirect("cont_afisare")
    return render(request, "platforma/cont_afisare.html", {"variante": VARIANTE, "curent": profil.scara_afisare})


@login_required
def setari_aplicatie(request):
    """Setările aplicației (administrator): un singur loc, în tema aplicației, pentru toată configurarea."""
    from django.contrib.auth.models import User
    from django.core.exceptions import PermissionDenied
    from django.urls import reverse
    from .permisiuni import e_admin
    if not e_admin(request.user):
        raise PermissionDenied("Setările aplicației le gestionează administratorul.")
    a = lambda model: reverse(f"admin:{model}_changelist")  # noqa: E731
    sectiuni = [
        ("Utilizatori și acces", [
            {"titlu": "Utilizatori și roluri", "iconita": "bi-person-gear", "url": reverse("admin:auth_user_changelist"), "nr": User.objects.filter(is_active=True).count(),
             "desc": "Conturi, rol în aplicație (administrator, coordonator, PM, financiar…), parole, activare."},
            {"titlu": "Echipe de proiect", "iconita": "bi-people", "url": reverse("echipe_admin"), "nr": None,
             "desc": "Echipa fiecărui proiect, pe categorii; proiectele fără echipă."},
        ]),
        ("Nomenclatoare și firme", [
            {"titlu": "Nomenclatoare", "iconita": "bi-list-ul", "url": a("platforma_nomenclator"), "nr": m.Nomenclator.objects.count(),
             "desc": "Linii de business, divizii, departamente, faze de proiect, tipuri de finanțare, categorii de documente."},
            {"titlu": "Societăți (firmele grupului)", "iconita": "bi-building", "url": a("platforma_societate"), "nr": m.Societate.objects.count(),
             "desc": "Denumire, CUI, cod Charisma, acronimul pentru codurile activelor."},
            {"titlu": "Proprietari externi", "iconita": "bi-house-door", "url": a("platforma_proprietarextern"), "nr": m.ProprietarExtern.objects.count(),
             "desc": "Proprietarii din afara grupului de la care se închiriază active."},
            {"titlu": "Registrul partenerilor", "iconita": "bi-person-vcard", "url": reverse("parteneri"), "nr": m.Partener.objects.count(),
             "desc": "Furnizori și prestatori (date ANAF după CUI), folosiți la contracte și facturi."},
        ]),
        ("Active și autorizații", [
            {"titlu": "Tipuri de autorizații", "iconita": "bi-patch-check", "url": a("platforma_tipautorizatie"), "nr": m.TipAutorizatie.objects.count(),
             "desc": "Lista standard de autorizații de funcționare urmărite pe active, cu responsabili."},
            {"titlu": "Coduri de active retrase", "iconita": "bi-x-octagon", "url": a("platforma_codlocatieretras"), "nr": m.CodLocatieRetras.objects.count(),
             "desc": "Codurile activelor reziliate și șterse, care nu se mai realocă."},
        ]),
        ("Fluxuri de aprobare", [
            {"titlu": "Fluxul plăților", "iconita": "bi-cash-stack", "url": reverse("setari_plati"), "nr": None,
             "desc": "Administratorul care aprobă solicitările de plată și adresa de email a trezoreriei."},
        ]),
        ("Control", [
            {"titlu": "Jurnal de audit", "iconita": "bi-clock-history", "url": reverse("jurnal"), "nr": None,
             "desc": "Toate modificările din aplicație, cu autor, dată și valorile vechi / noi."},
            {"titlu": "Ștergere proiect", "iconita": "bi-shield-lock", "url": reverse("stergere_proiect"), "nr": None,
             "desc": "Ștergerea completă a unui proiect, cu arhivă (doar administrator)."},
            {"titlu": "Setări afișare (contul meu)", "iconita": "bi-display", "url": reverse("cont_afisare"), "nr": None,
             "desc": "Mărimea în care se afișează aplicația pe contul dumneavoastră."},
        ]),
    ]
    return render(request, "platforma/setari_aplicatie.html", {"sectiuni": sectiuni})


@login_required
def setari_plati(request):
    """Fluxul plăților: 1. solicitarea se aprobă de administratorul ales; 2. centralizarea + facturile pleacă la trezorerie."""
    from django.contrib import messages
    from django.contrib.auth.models import User
    from django.core.exceptions import PermissionDenied
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from .permisiuni import e_admin
    from .solicitari import aprobator_plati
    if not e_admin(request.user):
        raise PermissionDenied("Setările aplicației le gestionează administratorul.")
    admini = [u for u in User.objects.filter(is_active=True).select_related("profil").order_by("first_name", "username") if e_admin(u)]
    if request.method == "POST":
        uid = request.POST.get("aprobator", "")
        emailuri = [e.strip() for e in request.POST.get("trezorerie", "").replace(";", ",").split(",") if e.strip()]
        gresite = []
        for e in emailuri:
            try:
                validate_email(e)
            except ValidationError:
                gresite.append(e)
        if gresite:
            messages.error(request, "Adrese de email invalide: " + ", ".join(gresite))
        else:
            m.SetareAplicatie.seteaza("plati_aprobator_id", uid if uid in {str(u.pk) for u in admini} else "")
            m.SetareAplicatie.seteaza("plati_email_trezorerie", ", ".join(emailuri))
            messages.success(request, "Setările fluxului de plăți au fost salvate.")
            return redirect("setari_plati")
    apr = aprobator_plati()
    return render(request, "platforma/setari_plati.html", {
        "admini": admini, "aprobator_ales": m.SetareAplicatie.get("plati_aprobator_id"), "aprobator_efectiv": apr,
        "trezorerie": m.SetareAplicatie.get("plati_email_trezorerie"),
    })
