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
