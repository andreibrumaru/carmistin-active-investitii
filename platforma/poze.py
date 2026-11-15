"""Fotografii: redimensionare automată la încărcare, ca să se deschidă imediat.

Propunerea aplicată:
  • imaginea principală: latura mare de cel mult 1920 px, JPEG calitate 82 (progresiv) – suficient pentru ecran
    complet și zoom moderat; o poză de telefon de 4–8 MB ajunge la ~250–600 KB;
  • miniatura pentru galerii și rapoarte: latura mare 480 px, JPEG calitate 72 – ~25–50 KB;
  • orientarea din EXIF se aplică pe pixeli (pozele nu mai apar rotite), restul metadatelor (inclusiv GPS) se elimină.
Browserul redimensionează deja pozele înainte de trimitere (static/platforma/poze.js), deci și încărcarea e rapidă;
serverul garantează rezultatul și pentru fișierele venite altfel. Fișierele care nu pot fi citite ca imagine
(ex. HEIC fără suport) se păstrează neschimbate.
"""
from io import BytesIO

from django.core.files.base import ContentFile

LATURA_MAX, CALITATE = 1920, 82
LATURA_MINI, CALITATE_MINI = 480, 72


def _jpeg(im, latura, calitate):
    from PIL import Image
    im = im.copy()
    im.thumbnail((latura, latura), Image.LANCZOS)
    buf = BytesIO()
    im.save(buf, "JPEG", quality=calitate, optimize=True, progressive=True)
    return buf.getvalue()


def pregateste(fisier):
    """(fișier principal, miniatură sau None) pentru o fotografie încărcată."""
    try:
        from PIL import Image, ImageOps
        fisier.seek(0)
        original = fisier.read()
        im = Image.open(BytesIO(original))
        if getattr(im, "is_animated", False):
            fisier.seek(0)
            return fisier, None
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            fundal = Image.new("RGB", im.size, (255, 255, 255))
            fundal.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = fundal
    except Exception:
        fisier.seek(0)
        return fisier, None
    baza = fisier.name.rsplit("/", 1)[-1].rsplit(".", 1)[0][:80] or "poza"
    principal = _jpeg(im, LATURA_MAX, CALITATE)
    mini = _jpeg(im, LATURA_MINI, CALITATE_MINI)
    return ContentFile(principal, name=f"{baza}.jpg"), ContentFile(mini, name=f"{baza}_mini.jpg")


def salveaza(fisier, categorie="foto", **campuri):
    """Creează documentul-fotografie (implicit categoria „foto”) cu imaginea redimensionată și miniatura ei."""
    from .models import Document
    principal, mini = pregateste(fisier)
    d = Document(categorie=categorie, fisier=principal, **campuri)
    if mini is not None:
        d.miniatura = mini
    d.save()
    return d
