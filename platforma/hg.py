"""Structura devizului general conform HG 907/2016 (capitole și subcapitole)."""

STRUCTURA_HG = [
    ("1", "Cheltuieli pentru obținerea și amenajarea terenului", [
        ("1.1", "Obținerea terenului"),
        ("1.2", "Amenajarea terenului"),
        ("1.3", "Amenajări pentru protecția mediului și aducerea terenului la starea inițială"),
        ("1.4", "Cheltuieli pentru relocarea/protecția utilităților"),
    ]),
    ("2", "Cheltuieli pentru asigurarea utilităților necesare obiectivului de investiții", [
        ("2.1", "Cheltuieli pentru asigurarea utilităților necesare obiectivului"),
    ]),
    ("3", "Cheltuieli pentru proiectare și asistență tehnică", [
        ("3.1", "Studii (teren, impact asupra mediului, alte studii specifice)"),
        ("3.2", "Documentații-suport și cheltuieli pentru obținerea de avize, acorduri și autorizații"),
        ("3.3", "Expertizare tehnică"),
        ("3.4", "Certificarea performanței energetice și auditul energetic al clădirilor"),
        ("3.5", "Proiectare (temă, SF/DALI, DTAC, verificare, PT și DE)"),
        ("3.6", "Organizarea procedurilor de achiziție"),
        ("3.7", "Consultanță (management de proiect, audit financiar)"),
        ("3.8", "Asistență tehnică (proiectant, dirigenție de șantier)"),
    ]),
    ("4", "Cheltuieli pentru investiția de bază", [
        ("4.1", "Construcții și instalații"),
        ("4.2", "Montaj utilaje, echipamente tehnologice și funcționale"),
        ("4.3", "Utilaje, echipamente tehnologice și funcționale care necesită montaj"),
        ("4.4", "Utilaje, echipamente care nu necesită montaj și echipamente de transport"),
        ("4.5", "Dotări"),
        ("4.6", "Active necorporale"),
    ]),
    ("5", "Alte cheltuieli", [
        ("5.1", "Organizare de șantier"),
        ("5.2", "Comisioane, cote, taxe, costul creditului"),
        ("5.3", "Cheltuieli diverse și neprevăzute"),
        ("5.4", "Cheltuieli pentru informare și publicitate"),
    ]),
    ("6", "Cheltuieli pentru probe tehnologice și teste", [
        ("6.1", "Pregătirea personalului de exploatare"),
        ("6.2", "Probe tehnologice și teste"),
    ]),
]

CAPITOLE = {c: d for c, d, _ in STRUCTURA_HG}
SUBCAPITOLE = [(cod, den) for _, _, subs in STRUCTURA_HG for cod, den in subs]


def capitol_din(cod):
    return cod.split(".")[0]


def creeaza_linii(proiect):
    from .models import LinieBuget
    for cod, den in SUBCAPITOLE:
        LinieBuget.objects.get_or_create(proiect=proiect, capitol=cod, defaults={"denumire": den})


# Liniile de buget care trebuie justificate în Raportul Zero printr-o listă de echipamente / dotări
LINII_ECHIPAMENTE = ("4.3", "4.4", "4.5")
