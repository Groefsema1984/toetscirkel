# ToetsCirkel

Toetsconstructie, Toetsevaluatie en Oefentool samengevoegd tot één lerend systeem:
construeren → evalueren → op maat oefenen → bijsturen.

Het plan staat in het document *ToetsCirkel: architectuur- en migratieplan*. Deze repository volgt de fases uit dat plan.

## Starten

Dubbelklik `scripts/Start ToetsCirkel.command`. De browser opent op http://127.0.0.1:5050 met één menu:

| Adres | Onderdeel |
|---|---|
| `/` | startpagina |
| `/construeren/` | Toetsconstructie |
| `/evalueren/` | Toetsevaluatie |
| `/oefenen/` | Oefentool |

De losse startbestanden van de drie apps werken nog, maar zijn niet meer nodig.

## Stand: fase 2b (leerdoelcodes bij constructie)

- In het matrijsscherm van Construeren staat een kolom **Code** met de vaste leerdoelcodes uit het register
  (havo en vwo delen één register, mavo heeft een eigen). Claude vult die codes zelf in als er een precies past
  (`bouw/leerdoelen.py`); verzinnen mag niet.
- De codes komen in het correctievoorschrift (regel `LEERDOELCODES:` onder de toetsmatrijs). De Toetsevaluatie
  las die regel al voor oefentoetsen; nu werkt het ook voor gebouwde toetsen. Codes die niet in het register staan,
  worden genegeerd; die vragen koppelt de evaluatie zoals vroeger.
- De vragenbank bewaart de code per vraag, dus de itembank kent hem ook vóór de afname.

## Fase 2a (één database, één itembank)

- **Eén database.** Bij de eerste start zet ToetsCirkel de tabellen van de Oefentool over naar de centrale database
  (`~/Documents/Toetsevaluatie/toetsevaluatie.db`, naam en plek ongewijzigd). Eerst komt er een back-up, daarna wordt
  per tabel gecontroleerd of alle rijen zijn overgekomen. Het oude bestand blijft bewaard als `oefentool.voor_fase2.db`.
  Gaat er iets mis, dan blijft alles zoals het was en meldt de startpagina dat.
- **Eén itembank** (`/itembank`): alle vragen uit de vragenbank van Toetsconstructie, de toetsmatrijzen van afgenomen
  toetsen (met de echte p-waarde) en de oefenopgaven. Een gebouwde toets wordt gekoppeld aan zijn evaluatie
  (via de itemanalyse, of anders via AT-nummer, vak, niveau en soort), zodat een gebouwde vraag de leerdoelcode en
  p-waarde van na de afname krijgt.
- Code: `cirkel/kern/database.py` en `cirkel/kern/itembank.py`. In de apps is alleen `Oefentool/oefentool/instellingen.py`
  aangepast (welke database).
- De back-up neemt van de kennisbank alleen json, txt en md mee; de examen-pdf's kostten per back-up zo'n 60 MB.

## Fase 1 (één app)

`cirkel/` laadt de drie bestaande apps in één programma en zet ze elk onder hun eigen adres. De code van de apps is
niet gewijzigd: `cirkel/koppeling.py` houdt hun vaste adressen (zoals `/api/status`) binnen de juiste app en zet de
menubalk bovenaan. Lukt het laden van één app niet, dan starten de andere gewoon en toont de startpagina de fout.

## Fase 0 (veilig fundament)

De drie apps staan ongewijzigd in `apps/`. Ze draaien nog zoals altijd vanuit `~/Documents/<app>`; deze repository is
vanaf nu de plek waar de code wordt bijgehouden.

```
cirkel/
  server.py           start de drie apps samen op poort 5050
  koppeling.py        adressen binnen elke app houden, menubalk
  portaal.py          startpagina, itembank-pagina en doorverwijzen
  kern/               gedeelde database en itembank
apps/
  Toetsconstructie/   lokale Flask-app (poort 5057), Claude Code via abonnement, Word-bouwmotor, kennisbank
  Toetsevaluatie/     lokale Flask-app (poort 5055), scans nakijken, analyse, leerprofielen
  Oefentool/          lokale Flask-app (poort 5056), oefenmateriaal, itembank, leerloop
scripts/
  Start ToetsCirkel.command  ToetsCirkel starten (dubbelklik)
  Backup.command      back-up van databases en kennisbank (dubbelklik)
  Installeer.command  code uit deze repository in de apps zetten, gegevens blijven staan (dubbelklik)
  Gouden toets.command  gouden toets vastleggen of controleren (dubbelklik)
tests/
  gouden_toets.py     momentopname van een afgeronde toets maken en vergelijken
  test_fase0.py       tests met verzonnen gegevens
  test_fase1.py       de drie apps samen op één adres
  test_fase2.py       één database, itembank, gouden toets blijft gelijk
  test_fase2b.py      leerdoelcode van register via matrijs en correctievoorschrift naar de evaluatie
docs/
  AFSPRAKEN.md        wat wel/niet in Git, privacy, toetscode, werkwijze
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Belangrijk

Er staan geen leerlinggegevens in deze repository en dat blijft zo. Zie `docs/AFSPRAKEN.md`.
