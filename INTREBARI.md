# Platforma Active & Investiții Carmistin — Întrebări pentru forma intermediară

Data: 07.11.2026

## Ce am constatat în materialele primite

- **Registrul locații (Excel)** are 4 foi: „Locatii Grup” (~296 rânduri, doar ~180 au cod locație), „teren investitii” (~58 rânduri, 22 cu cod), „Liste” (nomenclatoare: linie afacere, departament, tip finanțare, responsabili, status, fază proiect) și „-” (~164 obiective cu cod și status cod).
- Codurile au formate neuniforme: `BIO 03` (spațiu), `FFR 7`, `APV01`, `AVI.90.01`, `AVI 101/102`, `FFR 03 / AXL 03` (două coduri pe un rând).
- **Raportul Zero** folosește `FFR.07.01` (punct, nu spațiu) și un nr. de înregistrare separat `FFR.20240320.1` = prefix + dată + nr. ordine.
- Câmpuri Raport Zero: denumire, cod proiect, departament, beneficiar, solicitant, resp. tehnic, resp. implementare, proiectant/executant + contract (nr./dată), locație, clădire/nr. cadastral, descriere lucrări, necesar achiziții, valoare/buget (EUR), finanțare, prioritate, dată demarare/finalizare, întocmit/avizat + semnături.

## A. Structura activelor (registrul de locații)

1. Care foaie este sursa de adevăr pentru active? „Locatii Grup”, „teren investitii” și „-” se suprapun parțial — le unificăm într-un singur registru (cu un câmp „Tip”: activ / teren / obiectiv) sau rămân registre separate?
2. Ce facem cu cele ~116 rânduri fără cod locație? Le importăm ca „fără cod / de alocat”, le alocăm noi un cod propus, sau le excludem?
3. Formatul oficial al codului: `BIO 03` sau `BIO.03`? Normalizez la un singur format (propunere: `BIO.03`, iar proiectele `BIO.03.01`) și corectez automat `FFR 7` → `FFR.07`, `APV01` → `APV.01`?
4. Ce înseamnă `AVI.90.01` (pare deja un proiect) și `AVI 101/102` (3 cifre)? Pot exista peste 99 de locații pe un prefix?
5. Ce facem cu `FFR 03 / AXL 03` — o locație cu două coduri (ex. schimbare de proprietar) sau două locații? E nevoie de „cod istoric / alias”?
6. Prefixul (AVI, FFR, BAR…) corespunde societății proprietare sau utilizatoare? Vreți un nomenclator de societăți al grupului (denumire, CUI, prefix) din care să se genereze automat codul următor?
7. O locație poate avea mai multe cărți funciare / nr. cadastrale (se vede „CF 76379, 76381, …”). Le vreți ca listă separată (CF, nr. cadastral, suprafață, extras CF atașat) sau rămân text liber?
8. Câmpurile „Column1/Column2” și „Spațiu cazare” — ce reprezintă și le păstrăm?
9. „Valoare garantare (EUR)”, „Data raport evaluare”, „Sindicat/Non sindicat” — sunt date sensibile care trebuie văzute doar de anumite roluri (ex. financiar)?
10. Doriți hartă cu toate locațiile (coordonatele sunt în formate mixte: zecimal și grade/minute — le convertesc automat)?
11. Pe un activ vreți și documente atașate (extras CF, contract închiriere/concesiune, raport evaluare, poze) și istoric modificări (cine a schimbat ce și când)?
12. Statusurile activului: lista actuală este „Funcțional / Nefuncțional / Investiție / Propus-în lucru / RENT”. E completă? „RENT” e un status sau un tip de deținere (owned / concesiune / închiriat)?

## B. Raportul Zero și codificarea proiectelor

13. Codul proiectului = cod locație + nr. ordine (`FFR.07.01`, `.02`…). Aplicația îl generează automat la crearea Raportului Zero — confirmați?
14. Nr. de înregistrare document (`FFR.20240320.1`) se generează și el automat (prefix + dată + nr. ordine zilnic)? Contorul e pe prefix sau global?
15. Un proiect poate viza mai multe locații simultan? Dacă da, sub ce cod se înregistrează?
16. Fluxul Raportului Zero: cine îl inițiază (solicitant/PM), cine îl avizează (în exemple — dvs.)? Vreți aprobare în aplicație (circuit Întocmit → Avizat) și generare PDF cu antetul Carmistin, sau doar încărcarea PDF-ului semnat scanat?
17. Rapoartele zero existente (câte sunt aproximativ?) le importăm din PDF-uri sau dintr-un Excel? Există un registru al investițiilor în Excel pe care să-l preiau?
18. Dacă un Raport Zero se modifică (buget, termen), se emite o revizie (`FFR.07.01 rev.1`) sau un raport nou?

## C. Registrul investițiilor

19. Statusurile și fazele: folosesc listele din foaia „Liste” (Status: În lucru / Finalizat / Inactiv / Contractare; Fază: În scriere / Proiect depus / Inactiv / Propus-în lucru / Obținere avize și autorizații / Execuție / Finalizat)? Le combinăm sau rămân două câmpuri separate?
20. Ce coloane vreți în vizualizarea centralizată (tabel)? Propunere: cod, denumire, locație, societate beneficiară, linie afacere, coordonator, PM, buget, executat %, finanțare, fază, termen, semafor întârziere.
21. Ce rapoarte/exporturi sunt necesare (Excel, PDF)? Pe linie de afacere, pe societate, pe coordonator, pe sursă de finanțare, pe an?
22. „Prioritate” — valorile sunt doar mică/medie/mare?

## D. Buget și execuție bugetară (structura HG)

23. Ce HG aveți în vedere — HG 907/2016 (devizul general cu capitolele 1–6: obținere teren, utilități, proiectare și asistență tehnică, investiția de bază, alte cheltuieli, probe tehnologice)? Folosim capitolele + subcapitolele standard?
24. Bugetul se urmărește doar pe capitole sau și pe obiecte / categorii de lucrări?
25. Moneda: EUR (ca în rapoartele zero), RON sau ambele cu curs BNR la data facturii? TVA inclus sau separat?
26. Execuția bugetară: din ce se alimentează — facturi introduse manual, import din ERP/contabilitate (ce sistem folosiți? ex. Charisma, SAP, WinMentor), sau situații de lucrări încărcate de PM?
27. Ce înseamnă „stadiu de execuție” pentru dvs.: angajat (contractat) vs. plătit vs. realizat fizic (%)? Toate trei?
28. Se lucrează cu bugete revizuite (buget inițial / buget actualizat) și aprobare pentru depășiri?

## E. Certificat de urbanism și avize

29. Lista de avize din CU: o extrage AI-ul automat din PDF-ul CU (recomandat, cu validare de către PM), sau se bifează manual dintr-o listă standard?
30. CU-urile sunt de regulă PDF-uri scanate (necesită OCR) sau documente digitale?
31. Ce câmpuri urmărim per aviz: emitent, nr./dată cerere, nr./dată aviz, valabilitate, cost/taxă, status (necesar / depus / obținut / respins / expirat), responsabil, document atașat?
32. Vreți alerte la expirarea CU-ului / avizelor și la termene de depunere (email, notificare în aplicație)?
33. Pe lângă CU și avize — urmărim și Autorizația de construire, DTAC, PT, recepții (proces-verbal începere lucrări, recepție la terminare / finală), ca etape ale proiectului?

## F. Finanțare

34. Tipuri de finanțare: Fonduri proprii / Bancară / Nerambursabile (din „Liste”) — se pot combina pe același proiect (ex. 60% grant + 40% credit)?
35. Pentru fonduri nerambursabile: ce câmpuri? Program (PNRR, AFIR/PNS, POIM etc.), nr. contract de finanțare, valoare eligibilă/neeligibilă, cofinanțare, cereri de plată/rambursare, termene de raportare, perioadă de monitorizare ex-post.
36. Lista obligațiilor din contractul de finanțare: o extrage AI-ul din contract (cu validare) și le transformă în sarcini cu termen?
37. Pentru finanțarea bancară: urmărim tragerile, garanțiile (legătură cu „Valoare garantare” din active?), covenants?
38. Responsabilul de finanțare (lista din „Liste”) are rol separat în aplicație, cu acces la mai multe proiecte?

## G. Grafic Gantt

39. Importul de grafic: din ce format — MS Project (.mpp/.xml), Excel, Primavera? Sau construim graficul direct în aplicație?
40. Gantt-ul trebuie să urmărească planificat vs. realizat (baseline) și să lege sarcinile de avize/contracte?

## H. Contracte și AI

41. Ce tipuri de contracte: proiectare, execuție, furnizare echipamente, consultanță, finanțare? Mai multe contracte pe proiect, cu acte adiționale?
42. Ce condiții vreți extrase automat: părți, obiect, valoare, termen de execuție, termen de plată, garanții de bună execuție/avans, penalități, recepții, perioadă de garanție, clauze de reziliere? Altele?
43. Contractele pot fi și în limba engleză (ex. furnizori externi ca INNO AG)?
44. Confidențialitate: este acceptabil ca documentele să fie procesate de un serviciu AI în cloud (Claude API, cu politica de nepăstrare a datelor), sau AI-ul trebuie să ruleze exclusiv pe serverul propriu? (Impactează mult costul și calitatea.)
45. Ce poate face utilizatorul cu rezultatul AI: doar vizualizare, sau valorile extrase devin date ale proiectului (termene în Gantt, alerte, valori în buget) după confirmare?

## I. Utilizatori, roluri, acces

46. Roluri propuse: Administrator, Management (vede tot, read-only), Coordonator (vede proiectele lui), Project Manager (editează proiectele lui), Responsabil finanțare, Responsabil activ, Vizualizator. Corespunde realității? Lipsește vreun rol (financiar, juridic, achiziții, auditor)?
47. Un coordonator vede automat toate proiectele PM-ilor din subordine, sau i se alocă proiecte individual?
48. Accesul se poate restrânge și pe societate / linie de afacere (ex. cineva vede doar „Porc”)?
49. Cine poate crea Raport Zero / proiecte noi și cine poate modifica activele (doar administratorul)?
50. Autentificare: cont propriu (email + parolă) sau Single Sign-On cu Microsoft 365 / Google Workspace (domeniul carmistin.ro)? Autentificare în doi pași?
51. Câți utilizatori estimați (aprox.) și câte proiecte active simultan?

## J. Infrastructură și tehnic

52. Serverul propriu: ce sistem de operare (Linux / Windows Server)? Docker este disponibil? Cine administrează serverul (IT intern)?
53. Aplicația va fi accesibilă doar din rețeaua internă/VPN sau din internet (cu domeniu propriu, ex. investitii.carmistin.ro și certificat SSL)?
54. Stocarea documentelor: pe disc local al serverului, pe NAS, sau există SharePoint/OneDrive unde trebuie să rămână fișierele?
55. Backup: există politică de backup pe server sau o includem în aplicație?
56. Limba interfeței: doar română sau și engleză?
57. Utilizare de pe mobil (PM pe șantier încarcă poze/documente) este necesară în faza intermediară?
58. Integrări dorite (acum sau mai târziu): ERP/contabilitate, email, calendar, semnătură electronică?

## K. Prioritizare pentru forma intermediară

59. Care module sunt obligatorii în prima versiune? Propunere de etapizare:
    - **Etapa 1:** registrul activelor (import Excel + editare), Raport Zero + codificare, registrul investițiilor, fișa proiectului cu documente, roluri și acces.
    - **Etapa 2:** buget HG + execuție, avize din CU, finanțare și obligații.
    - **Etapa 3:** Gantt, AI pe contracte și CU, alerte, rapoarte avansate.
60. Aveți un termen dorit pentru forma intermediară și un proiect-pilot pe care să testăm (ex. GFF.01.08 – Fermă pilot Potcoava)?
61. Ne puteți trimite câte un exemplu de: certificat de urbanism, contract de execuție, contract de finanțare, deviz general, grafic Gantt existent?

---

# Decizii confirmate (runda 1)

| Subiect | Decizie |
|---|---|
| AI | Preferință: rulare pe serverul propriu (model local) |
| Acces rețea | Doar din rețeaua proprie / VPN |
| Autentificare | Domain controller (Active Directory / LDAP) |
| Vizibilitate | Registrul activelor și Rapoartele Zero: vizibile tuturor. Datele proiectelor: pe bază de drepturi |
| Roluri | Admin vede tot; coordonatorul vede proiectele PM-ilor săi; acces suplimentar pe proiect, la unul sau mai mulți utilizatori |
| Buget | Structura HG 907/2016, în RON, cu conversie în EUR |
| Versiunea intermediară | Demo de lucru care include toate cele 3 etape |

# Runda 2 — trasabilitate (bani, contracte, rapoarte, investiții)

## L. Lanțul banilor
62. Lanțul pe care îl urmărim: Raport Zero (buget aprobat) → buget HG detaliat → angajament (contract / comandă) → factură → plată → recepție → capitalizare ca mijloc fix. Vreți toate verigile sau ne oprim la plată?
63. Facturile se introduc în aplicație sau vin din ERP? Care este ERP-ul / programul de contabilitate (Charisma, SAP, WinMentor, Saga etc.) și există export / API / acces la baza de date?
64. O factură poate fi împărțită pe mai multe proiecte sau pe mai multe capitole de buget? Un contract poate acoperi mai multe proiecte?
65. Beneficiarul din Raportul Zero (societatea) este întotdeauna și plătitorul? Există refacturări intra-grup care trebuie urmărite?
66. Vreți un circuit de aprobare a plăților în aplicație (PM propune → coordonator → financiar aprobă), sau aplicația doar înregistrează ce s-a plătit?
67. Se blochează sau se semnalează o factură/un contract care depășește bugetul pe capitol? Cine poate aproba depășirea?
68. Cursul RON/EUR: cursul BNR de la data facturii, de la data plății, cursul din Raportul Zero (fix) sau un curs mediu lunar? Raportul Zero rămâne în EUR (ca acum) sau trece în RON?
69. Se folosesc centre de cost / coduri de investiție în contabilitate? Codul proiectului (ex. FFR.07.01) poate deveni cheia comună cu contabilitatea?
70. Există un buget anual de investiții (CAPEX) al grupului, aprobat pe societăți / linii de afacere, cu care trebuie comparate proiectele?

## M. Trasabilitatea contractelor
71. Registrul contractelor: e unul central pe grup sau pe fiecare societate? Numerotarea contractelor e internă (vreți ca aplicația să o genereze)?
72. Actele adiționale modifică valoarea/termenele — păstrăm istoricul complet (valoare inițială vs. actualizată)?
73. Urmărim garanțiile (de bună execuție, de avans, scrisori bancare) cu date de expirare și eliberare?
74. Urmărim penalitățile calculate/aplicate și disputele?
75. Achiziția: urmărim și etapa de oferte (ex. minim 3 oferte, comparație, decizie de selecție) înainte de contract? Există o procedură internă de achiziții pe care să o reproducem?

## N. Trasabilitatea rapoartelor și documentelor
76. Raportul Zero aprobat devine „înghețat”? Orice modificare ulterioară (buget, termen, responsabil) se face prin revizie numerotată (rev.1, rev.2) cu motiv și aprobare?
77. Aprobările (întocmit / avizat) le facem în aplicație (cu nume, dată, oră — echivalentul semnăturii) sau se încarcă în continuare PDF-ul semnat de mână? Ambele?
78. PM-ii trebuie să emită rapoarte periodice de progres (lunar / săptămânal)? Cu ce conținut — stadiu fizic, stadiu financiar, riscuri, poze din șantier?
79. Ce rapoarte consolidate primește managementul și cu ce frecvență? Aveți un model actual (Excel/PPT) pe care să-l reproduc?
80. Documentele încărcate: se pot șterge sau doar se înlocuiesc cu versiune nouă (istoricul rămâne)? Recomand: nicio ștergere definitivă, doar arhivare.
81. Jurnalul de audit (cine a văzut / modificat / aprobat ce și când): trebuie să poată fi consultat de cine — doar admin sau și de un auditor?
82. Există o registratură (nr. de înregistrare intrare/ieșire documente) cu care trebuie corelată aplicația?

## O. Închiderea investiției și legătura cu activele
83. La finalizare: urmărim recepția la terminare, punerea în funcțiune, recepția finală și raportul final al investiției (buget vs. realizat, abateri)?
84. Valoarea finală a investiției se reflectă înapoi pe activ (locație)? Ex. istoricul investițiilor pe locația BIO 03 cu valoarea cumulată.
85. O investiție poate crea o locație nouă (ex. teren cumpărat, fermă nouă)? Codul nou de locație îl generează aplicația?
86. Proiectele cu fonduri nerambursabile: perioada de monitorizare ex-post (3–5 ani) se urmărește pe activ după închiderea proiectului?

## P. Demo și infrastructură
87. Domain controller: Active Directory on-premise? Există grupuri AD pe care să le mapăm pe roluri (ex. „INV-Coordonatori”)? Cine din IT ne poate oferi un cont de serviciu LDAP pentru test?
88. Demo-ul îl folosim cu date reale (registrul Excel, rapoartele zero) sau cu date anonimizate până ajunge pe serverul propriu?
89. Echipa IT are preferințe de tehnologie pentru mentenanță (ex. .NET, Java, Node.js, Python) și pentru baza de date (PostgreSQL, SQL Server)?
90. Serverul pentru AI: există sau se poate achiziționa un server cu placă video (GPU)? Ce buget orientativ? (vezi recomandarea AI)

# Decizii confirmate (runda 2, întrebările 62–70)

| # | Decizie |
|---|---|
| 62 | Facturile se introduc în aplicație; aplicația generează un fișier de import pentru ERP. În faza actuală: doar fișierul de export. |
| 63 | ERP = Charisma. Comunicarea directă (import automat, comenzi în ERP) = dezvoltare viitoare. Fluxul este doar aplicație → ERP. |
| 64 | O factură aparține unui singur proiect, dar se poate repartiza pe mai multe linii de buget. |
| 65 | Există facturări intragrup (trebuie marcate și urmărite). |
| 66 | Fișierul de plată se exportă doar pe bază de aprobare (flux de aprobare). |
| 67 | Rectificarea bugetului o face administratorul. |
| 68 | Fiecare proiect are un curs de referință fix; fiecare proiect are o zonă de setări (configurator) cu toate datele de intrare. |
| 69 | Fiecare cod de proiect deschide un centru de cost în contabilitate; compania (societatea din grup) trebuie definită pe proiect. |
| 70 | Dashboard centralizat cu bugetele estimate și realizate, anual. |

## Întrebări deschise rezultate
91. Formatul fișierului de import Charisma: aveți un model (coloane, separator, CSV/XLS)? Până atunci exportăm un Excel/CSV generic cu: societate (CUI), centru de cost, furnizor (CUI), nr./dată factură, valoare, TVA, monedă, curs, linie buget, cont.
92. Circuitul de aprobare a plăților: PM propune → coordonator aprobă → financiar aprobă → export. Corect? Există praguri valorice (ex. peste X RON aprobă și directorul general)?
93. Fișierul de plată: e pentru Charisma sau pentru banca (format OP / MT101 / CSV bancă)? Ce bancă?
94. Bugetul anual estimat: îl introduce PM-ul pe ani la crearea proiectului, sau rezultă din graficul Gantt?
95. „Realizat” anual = facturat în anul respectiv sau plătit în anul respectiv?
