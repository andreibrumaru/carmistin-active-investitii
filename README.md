# Carmistin – Platforma Active & Investiții (versiune demo)

Aplicație web internă pentru registrul activelor grupului, Rapoartele Zero și urmărirea completă a proiectelor de investiții.
Tehnologie: Python / Django 4.2, PostgreSQL (SQLite pentru demo local), Bootstrap, AI local prin Ollama.

## Module

| Modul | Ce face |
|---|---|
| **Registrul activelor** | Toate locațiile importate din `REGISTRU LOCATII 2026 ACTIVE.xlsx` (350 de înregistrări, 119 fără cod). Filtre, hartă, export Excel, documente pe activ. Doar administratorul modifică. |
| **Rapoarte Zero** | Registru vizibil tuturor. Codul proiectului se generează automat (`BIO.03` → `BIO.03.01`, `.02`…) împreună cu nr. de înregistrare (`BIO.20261107.1`). Circuit: ciornă → trimis → avizat. După avizare, RZ e „înghețat”; modificările se fac prin revizii numerotate, cu motiv și istoric. Tipărire / PDF. |
| **Registrul investițiilor** | Proiectele pe care utilizatorul are drept să le vadă: buget, angajat, facturat, plătit, execuție financiară și fizică, termene. Export Excel. |
| **Fișa proiectului** | Taburi: Prezentare, Buget HG 907/2016, Contracte (acte adiționale, condiții), Facturi și plăți, Certificat de urbanism și avize, Finanțare și obligații, Grafic Gantt (import Excel / MS Project), Documente (versionate), Acces, Istoric. |
| **AI local** | Extrage avizele din certificatul de urbanism, condițiile din contracte și obligațiile din contractele de finanțare. Rezultatul e o propunere: devine dată de proiect doar după validarea omului, cu fragmentul sursă păstrat. |
| **Setări proiect (configurator)** | Curs de referință EUR fix pe proiect, centru de cost (implicit = codul proiectului), cont contabil, cotă TVA, toleranță de depășire, aprobatori de plăți și prag de aprobare suplimentară, buget estimat pe ani. Compania din grup (CUI, cod Charisma) vine de pe proiect. |
| **Facturi și plăți** | Factura aparține unui proiect și se repartizează pe una sau mai multe linii de buget. Facturile intragrup sunt marcate. Plățile trec prin circuitul: propusă (PM) → aprobată tehnic (coordonator) → aprobată financiar → aprobare suplimentară peste prag → exportată → plătită. |
| **Export Charisma** | Fluxul este doar aplicație → ERP. Se generează loturi Excel numerotate (`FAC-…` pentru facturi, `PLT-…` pentru plăți aprobate), cu societate, CUI, cod Charisma, centru de cost, cont și linie de buget. După export, documentele nu mai pot fi modificate. |
| **Buget anual** | Dashboard centralizat: buget estimat vs. facturat vs. plătit, pe ani, pe proiecte și pe societăți, cu filtre pe societate și linie de afacere. |
| **Trasabilitate** | Jurnal de audit automat pentru orice creare / modificare / ștergere (cine, când, IP, valoare veche → nouă). Documentele nu se șterg, se înlocuiesc cu versiuni noi; fiecare fișier are amprentă SHA-256. |

### Roluri și acces
- **Administrator**: tot, inclusiv registrul activelor și acordarea de drepturi.
- **Management**: vede tot, doar citire.
- **Coordonator**: proiectele lui și ale PM-ilor din subordine (setat în profilul PM-ului); avizează Rapoartele Zero.
- **Project manager**: proiectele lui.
- **Acces explicit**: pe fiecare proiect se pot adăuga utilizatori cu drept de citire sau editare.
- **Responsabil financiar**: vede toate proiectele, introduce facturi, aprobă financiar plățile și generează exporturile Charisma.
- Registrul activelor și registrul Rapoartelor Zero sunt vizibile tuturor utilizatorilor autentificați.
- **Bugetul**: îl editează echipa proiectului până la blocare (coordonatorul sau adminul îl aprobă și îl blochează). După blocare se modifică doar prin rectificări făcute de administrator, cu motiv.

### Lanțul banilor
Raport Zero (buget aprobat) → linii de buget pe capitolele HG → contracte (+ acte adiționale) alocate pe linii → facturi legate de contract → plăți.
Fiecare linie arată: aprobat, rectificat, angajat, facturat, plătit, disponibil; depășirile apar pe tabloul de bord.
Bugetul se ține în RON. Documentele în EUR se convertesc la cursul de referință fixat pe proiect (implicit cursul BNR de la data Raportului Zero; cursurile BNR se actualizează de la curs.bnr.ro).

## Pornire locală (demo)

```bash
python3 -m venv .venv && .venv/bin/pip install Django~=4.2 openpyxl pypdf requests
./initializare_demo.sh "/Users/andrei_work/Desktop/ABC/REGISTRU  LOCATII  2026 ACTIVE.xlsx" /Users/andrei_work/Desktop/ABC
.venv/bin/python manage.py runserver 8000
```

Utilizatori demo (parola `Demo2026!`, doar pentru demo): `admin`, `management`, `coordonator`, `alin.ancuta` (PM FFR.07.01),
`octavian.paltanea` (PM GFF.01.08), `financiar` (acces explicit la BIO.03.01), `vizitator`.

Proiectele demo: **FFR.07.01** și **GFF.01.08** au datele reale din Rapoartele Zero (cu PDF-ul semnat atașat).
Bugetele, contractele, facturile, avizele și graficele lor, precum și proiectul **BIO.03.01**, sunt **date demonstrative**
(marcate în observații) și trebuie înlocuite cu datele reale.

## Pentru colegi (dezvoltare în paralel)

```bash
git clone <adresa-repository> carmistin-active && cd carmistin-active
./instaleaza.sh                              # .venv, pachete, .env, bază goală
.venv/bin/python manage.py createsuperuser
./porneste.sh 8001                           # http://localhost:8001
```

- Baza de date (`date/`) și `.env` **nu sunt în Git**: fiecare lucrează pe baza lui locală. O copie a bazei reale
  se transmite separat, pe un canal intern, și se pune în `date/db.sqlite3`.
- Lucrați pe ramuri proprii (`git switch -c nume/funcționalitate`) și uniți prin Pull Request pe GitHub.
- După `git pull` rulați `.venv/bin/python manage.py migrate` (pot apărea migrații noi de la colegi).
  Dacă doi oameni creează migrații în același timp, `manage.py makemigrations --merge` le unește.

## Protecția datelor (demo local)
- `initializare_demo.sh` **nu mai șterge** o bază existentă. Resetarea se face doar intenționat, cu `--reseteaza`, și numai după un backup automat în `date/backup/`.
- `./backup.sh` salvează baza și documentele în `date/backup/<data-ora>/` (se păstrează ultimele 30 de copii).
- `./testeaza_pe_copie.sh script.py` rulează teste pe o copie temporară a bazei; datele introduse de utilizatori nu sunt atinse.

## Instalare pe serverul propriu

1. Pe server (Linux cu Docker): copiați folderul, apoi `cp .env.example .env` și completați valorile.
2. `docker compose up -d` pornește PostgreSQL, aplicația (gunicorn), nginx și Ollama.
3. Import inițial:
   ```bash
   docker compose exec web python manage.py import_registru "/app/import/REGISTRU LOCATII 2026 ACTIVE.xlsx"
   docker compose exec web python manage.py actualizeaza_curs --an 2026
   ```
4. Model AI: `docker compose exec ollama ollama pull qwen2.5:14b` (sau un model mai mare dacă există GPU; setați `OLLAMA_MODEL`).
5. Curs BNR zilnic (cron pe server): `docker compose exec web python manage.py actualizeaza_curs`.
6. Autentificarea pe domeniu se activează completând `LDAP_*` în `.env`. Grupurile AD se mapează pe roluri (`AD_GRUP_*`).
   Un cont de administrator local de rezervă: `docker compose exec web python manage.py createsuperuser`.
7. Backup zilnic (cron), de exemplu:
   `docker compose exec -T db pg_dump -U carmistin carmistin | gzip > /backup/carmistin-$(date +%F).sql.gz`
   plus copierea volumului `fisiere` (documente). Păstrați copiile și în afara serverului.

Pentru rețeaua internă fără internet: Bootstrap, Leaflet și frappe-gantt se încarcă acum din CDN. Se pot descărca local în `platforma/static/`, iar harta are nevoie de un server de tile-uri intern sau de acces la OpenStreetMap.

## AI pe serverul propriu

- Text din PDF: `pypdf`. Pentru scanări, OCR local: pe server Tesseract cu limba română (inclus în imaginea Docker); pe Mac, recunoașterea de text inclusă în macOS (`tools/ocr_macos.swift`, compilat automat la prima folosire).
- Pe acest Mac (demo): Ollama în `~/Applications/Ollama.app`, model `qwen2.5:14b` (9 GB); `porneste.sh` pornește Ollama odată cu aplicația. Un contract scanat de 18 pagini se citește în ~1 minut (OCR + AI).
- Model: Ollama cu un model open-weight. Fără GPU merge un model de 7–14 miliarde de parametri (lent). Recomandat pentru producție: GPU de 48 GB sau mai mult și un model de 30–70 de miliarde de parametri.
- **Contracte:** „Încarcă contract (citire AI)” – se încarcă scanul, AI-ul citește tipul, numărul, data, partenerul, CUI-ul, obiectul, valoarea, moneda, termenele și garanțiile și precompletează formularul (câmpurile marcate „AI”), plus lista de condiții (avans, penalități, recepție, reziliere) cu fragmentul sursă. Nimic nu se salvează fără verificarea utilizatorului.
- Fără model disponibil, avizele din CU și datele contractelor se recunosc pe reguli (mai puțin precis, doar pentru PDF-uri cu text); contractele scanate ca imagine au nevoie de OCR (inclus în instalarea Docker).
- Prompturile sunt în `platforma/ai.py`; modelul se schimbă din `.env`, fără modificări de cod.

## Structura codului

```
config/settings.py         setări (DB, LDAP, AI, curs)
platforma/models.py        modelul de date complet
platforma/permisiuni.py    regulile de acces
platforma/audit.py         jurnalul de audit automat
platforma/hg.py            structura devizului general HG 907/2016
platforma/coduri.py        normalizare și generare coduri
platforma/ai.py            OCR + AI local + regula de rezervă
platforma/views.py         paginile aplicației
platforma/management/commands/  import_registru, date_demo, actualizeaza_curs
```

## De clarificat / pașii următori
- Întrebările deschise 71–95 din `INTREBARI.md`, în special modelul de fișier de import Charisma (91), circuitul și pragurile de aprobare (92) și formatul fișierului de plată (93).
- Comunicarea directă cu Charisma (import automat, comenzi) – dezvoltare viitoare.
- Curățarea registrului: 119 locații fără cod; 135 de societăți, dintre care unele sunt variante ale aceleiași firme (ex. „Ferma Francesti SRL (superficie…)”). Se pot unifica din Administrare → Societăți.
- Formatul codurilor a fost normalizat la `PREFIX.NN` (ex. `FFR 7` → `FFR.07`); codul original din Excel se păstrează pe fiecare locație.
- Notificări pe email (avize care expiră, termene, RZ de avizat).
- Integrarea cu ERP-ul pentru preluarea automată a facturilor și plăților.
