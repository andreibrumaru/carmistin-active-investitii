"""PDF-ul unui raport zilnic de activitate (generat la fiecare deschidere, din datele raportului și fotografiile lui)."""
import os
from io import BytesIO

from django.conf import settings
from django.utils import timezone

# fonturi cu diacritice românești (ă â î ș ț): primul găsit pe sistem; altfel fontul de bază, fără diacritice
FONTURI = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]
VERDE = (11, 90, 70)
GRI = (100, 112, 109)
FARA_DIACRITICE = str.maketrans("ăâîșşțţĂÂÎȘŞȚŢ„”–—…", "aaissttAAISSTT\"\"--.")


def _pdf_cls():
    from fpdf import FPDF

    class Pdf(FPDF):
        subsol = ""

        def footer(self):
            self.set_y(-12)
            self.set_font(self.font_baza, "", 7.5)
            self.set_text_color(*GRI)
            self.cell(0, 5, self.t(f"{self.subsol} · pagina {self.page_no()}/{{nb}}"), align="C")

        def t(self, s):
            s = str(s or "")
            return s if self.unicode else s.translate(FARA_DIACRITICE).encode("latin-1", "replace").decode("latin-1")
    return Pdf


def genereaza(r):
    """Bytes PDF pentru raportul de activitate r."""
    Pdf = _pdf_cls()
    p = r.proiect
    pdf = Pdf(format="A4")
    pdf.set_margins(15, 14, 15)
    pdf.set_auto_page_break(True, 16)
    pdf.unicode = False
    pdf.font_baza = "helvetica"
    for normal, bold in FONTURI:
        if os.path.exists(normal) and os.path.exists(bold):
            pdf.add_font("baza", "", normal)
            pdf.add_font("baza", "B", bold)
            pdf.font_baza, pdf.unicode = "baza", True
            break
    F, t = pdf.font_baza, pdf.t
    acum = timezone.localtime()
    pdf.subsol = f"Raport de șantier {r.cod_raport} · {p.cod} din {r.data:%d.%m.%Y} · generat din Carmistin – Active & Investiții la {acum:%d.%m.%Y %H:%M}"
    pdf.add_page()

    # antet
    logo = os.path.join(settings.BASE_DIR, "platforma", "static", "platforma", "logo-carmistin.png")
    if os.path.exists(logo):
        pdf.image(logo, x=15, y=12, h=15)
    pdf.set_xy(80, 13)
    pdf.set_font(F, "B", 15)
    pdf.set_text_color(*VERDE)
    pdf.cell(115, 7, t("RAPORT DE ȘANTIER"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(80)
    pdf.set_font(F, "", 9)
    pdf.set_text_color(*GRI)
    pdf.cell(115, 5, t(f"Nr. {p.cod}/{r.cod_raport} · data raportului {r.data:%d.%m.%Y}"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(31)
    pdf.set_draw_color(*VERDE)
    pdf.set_line_width(0.6)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(4)

    # date de identificare
    autor = (r.autor.get_full_name() or r.autor.username) if r.autor else "—"
    etapa = "—"
    if r.etapa:
        etapa = f"{r.etapa.denumire} ({r.etapa.get_status_display().lower()}, {r.etapa.progres}%)"
    randuri = [
        ("Proiect", f"{p.cod} · {p.denumire}"),
        ("Beneficiar", str(p.beneficiar)),
        ("Locație / activ", f"{p.locatie.cod} · {p.locatie.denumire}" if p.locatie_id else "fără activ alocat"),
        ("Data raportului", f"{r.data:%d.%m.%Y}"),
        ("Întocmit de", f"{autor}" + (f" · emis la {timezone.localtime(r.emis_la):%d.%m.%Y %H:%M}" if r.emis_la else " · CIORNĂ (neemis)")),
    ]
    if r.total_muncitori:
        randuri.append(("Personal în șantier", f"{r.total_muncitori}"))
    if r.meteo:
        randuri.append(("Vreme", r.meteo))
    pdf.set_text_color(30, 40, 38)
    pdf.set_draw_color(225, 232, 230)
    pdf.set_line_width(0.2)
    for eticheta, valoare in randuri:
        pdf.set_font(F, "B", 9)
        pdf.set_fill_color(240, 246, 244)
        y = pdf.get_y()
        pdf.multi_cell(45, 6, t(eticheta), fill=True, border=0, new_x="RIGHT", new_y="TOP")
        pdf.set_font(F, "", 9)
        pdf.multi_cell(135, 6, t(valoare), border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(225, 232, 230)
        if pdf.get_y() < y + 6:
            pdf.set_y(y + 6)
    pdf.ln(4)

    def sectiune(titlu, text, culoare=VERDE):
        if not text:
            return
        pdf.set_font(F, "B", 10.5)
        pdf.set_text_color(*culoare)
        pdf.cell(0, 7, t(titlu), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*culoare)
        pdf.set_line_width(0.3)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(1.5)
        pdf.set_font(F, "", 10)
        pdf.set_text_color(30, 40, 38)
        pdf.multi_cell(0, 5.4, t(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    def titlu_sectiune(titlu, culoare=VERDE):
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.set_font(F, "B", 10.5)
        pdf.set_text_color(*culoare)
        pdf.cell(0, 7, t(titlu), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*culoare)
        pdf.set_line_width(0.3)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(1.5)
        pdf.set_text_color(30, 40, 38)

    def tabel(titlu, capete, latimi, randuri_t, total=None):
        if not randuri_t:
            return
        titlu_sectiune(titlu)
        pdf.set_font(F, "B", 8.5)
        pdf.set_fill_color(240, 246, 244)
        for c, w in zip(capete, latimi):
            pdf.cell(w, 6, t(c), fill=True, align="R" if c.startswith("Nr") or c in ("Cantitate",) else "L")
        pdf.ln(6)
        pdf.set_font(F, "", 9)
        pdf.set_draw_color(225, 232, 230)
        for rand in randuri_t + ([total] if total else []):
            if rand is total:
                pdf.set_font(F, "B", 9)
            for i, (v, w) in enumerate(zip(rand, latimi)):
                pdf.cell(w, 6, t(str(v))[:90], border="B", align="R" if capete[i].startswith("Nr") or capete[i] == "Cantitate" else "L")
            pdf.ln(6)
        pdf.ln(3)

    tabel("Personal în șantier", ["Firmă / echipă", "Nr. persoane"], [140, 40],
          [[c.get("firma", ""), c.get("muncitori", "")] for c in r.constructori or []],
          ["Total", r.total_muncitori] if len(r.constructori or []) > 1 else None)
    if r.lista_activitati:
        titlu_sectiune("Activități realizate")
        pdf.set_font(F, "", 10)
        for a in r.lista_activitati:
            y = pdf.get_y()
            pdf.set_fill_color(*VERDE)
            pdf.ellipse(17, y + 2.1, 1.6, 1.6, style="F")
            pdf.set_x(21)
            pdf.multi_cell(174, 5.4, t(a), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    avize = {d.pk: d for d in r.poze.all() if d.categorie == "aviz_insotire" and not d.arhivat}
    tabel("Materiale aprovizionate", ["Material", "Cantitate", "UM", "Furnizor / aviz", "Avize atașate"], [60, 22, 15, 55, 28],
          [[x.get("denumire", ""), x.get("cantitate", ""), x.get("um", ""), x.get("furnizor", ""),
            (f"{len([i for i in x.get('avize', []) if i in avize])} fișier(e)" if [i for i in x.get("avize", []) if i in avize] else "—")]
           for x in r.materiale or []])
    tabel("Utilaje pe șantier", ["Utilaj", "Nr.", "Observații"], [75, 20, 85],
          [[x.get("denumire", ""), x.get("numar", ""), x.get("observatii", "")] for x in r.utilaje or []])

    def sectiune(titlu, text, culoare=VERDE):
        if not text:
            return
        titlu_sectiune(titlu, culoare)
        pdf.set_font(F, "", 10)
        pdf.multi_cell(0, 5.4, t(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    sectiune("Probleme / blocaje", r.probleme, (138, 83, 0))
    sectiune("Pașii următori", r.pasi_urmatori)

    from PIL import Image

    def deseneaza_poze(poze, eticheta):
        lat, spatiu, h_max = 87.0, 6.0, 66.0
        coloana, y_rand, h_rand = 0, pdf.get_y(), 0
        for i, d in enumerate(poze, start=1):
            try:
                with d.fisier.open("rb") as fh:
                    im = Image.open(BytesIO(fh.read()))
                    im.load()
            except Exception:
                continue
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            lw, lh = lat, lat * h / w
            if lh > h_max:
                lh, lw = h_max, h_max * w / h
            if coloana == 0:
                if y_rand + lh + 8 > pdf.h - 18:
                    pdf.add_page()
                    y_rand = pdf.get_y()
                h_rand = 0
            pdf.image(im, x=15 + coloana * (lat + spatiu) + (lat - lw) / 2, y=y_rand, w=lw, h=lh)
            pdf.set_xy(15 + coloana * (lat + spatiu), y_rand + lh + 0.8)
            pdf.set_font(F, "", 7.5)
            pdf.set_text_color(*GRI)
            pdf.cell(lat, 4, t(f"{eticheta} {i}"), align="C")
            h_rand = max(h_rand, lh + 7)
            coloana = (coloana + 1) % 2
            if coloana == 0:
                y_rand += h_rand
        if coloana == 1:
            y_rand += h_rand
        pdf.set_y(y_rand)
        pdf.set_text_color(30, 40, 38)

    snag = list(r.snag.all())
    if snag:
        titlu_sectiune(f"Snag list ({len(snag)})", (138, 83, 0))
        for i, sn in enumerate(snag, start=1):
            if pdf.get_y() > pdf.h - 30:
                pdf.add_page()
            pdf.set_font(F, "B", 9.5)
            pdf.multi_cell(0, 5.2, t(f"{i}. {sn.descriere}"), new_x="LMARGIN", new_y="NEXT")
            detalii = " · ".join(x for x in (f"zonă: {sn.zona}" if sn.zona else "", f"responsabil: {sn.responsabil}" if sn.responsabil else "",
                                             f"termen: {sn.termen:%d.%m.%Y}" if sn.termen else "") if x)
            if detalii:
                pdf.set_font(F, "", 8.5)
                pdf.set_text_color(*GRI)
                pdf.multi_cell(0, 4.6, t(detalii), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(30, 40, 38)
            poze_sn = sorted((d for d in sn.poze.all() if not d.arhivat), key=lambda d: d.pk)
            if poze_sn:
                pdf.ln(1)
                deseneaza_poze(poze_sn, f"Snag {i} · foto")
            pdf.ln(2)

    # fotografii generale (cele din snag list apar la punctul lor)
    poze = sorted((d for d in r.poze.all() if not d.arhivat and not d.punct_snag_id and d.categorie == "foto"), key=lambda d: d.pk)
    if poze:
        titlu_sectiune(f"Fotografii ({len(poze)})")
        pdf.ln(0.5)
        deseneaza_poze(poze, "Foto")

    return bytes(pdf.output())
