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

## Stand: fase 3 (automatisch vanaf een scan)

- ToetsCirkel bewaakt de OneDrive-map **ToetsCirkel Scans** (`cirkel/scanwacht.py`, herkennen in `cirkel/kern/scans.py`).
  Een scan die een minuut niet verandert, wordt herkend aan:
  1. een map met zelf een correctievoorschrift erin (de mapnaam is de toetsnaam, zoals vroeger);
  2. het AT-nummer, de klas en de versie in de bestandsnaam of de tekstlaag, bijv. `AT5.21 5H versie A.pdf`;
  3. anders leest Claude de eerste pagina's (AT-nummer, versie, leerlingnummers; geen namen). De klas volgt uit de
     klassenlijsten, de toets uit de gebouwde toetsen van Construeren en de afnamedatum.
- Herkend: de analyse van Evalueren start met het correctievoorschrift van de gebouwde toets; de scan gaat naar
  `Verwerkt/`. Bestaat de analyse al (tweede klas, vergeten blad), dan worden de pagina's bijgescand.
  Niet zeker: de scan gaat naar `Twijfel/` met een `.txt` met de reden. Er wordt nooit geraden.
- Staat er na het nakijken niets open (wiens werk, verschil docent en AI, onleesbaar), dan wordt de toets vanzelf
  definitief gemaakt. Anders staat op de startpagina "Wacht op jou" met een link naar Valideren; na je laatste besluit
  gaat de keten vanzelf verder. Handmatig uploaden blijft werken (en publiceert niet vanzelf).
- Toetsen met versie A en B: scan de versies apart, met de versie in de bestandsnaam. Ze krijgen elk een eigen
  analyse (`_vA`, `_vB` in de toetsnaam).
- Logboek: `/scans`. Uitzetten: `TOETSCIRKEL_GEEN_SCANS=1`; andere map: `TOETSCIRKEL_SCANS`.

## Fase 2b (leerdoelcodes bij constructie)

- In het matrijsscherm van Construeren staat een kolom **Code** met de vaste leerdoelcodes uit het register
  (havo en vwo delen één register, mavo heeft een eigen). Claude vult die codes zelf in als er een precies past
  (`bouw/leerdoelen.py`); verzinnen mag niet.
- De codes komen in het correctievoorschrift (regel `LEERDOELCODES:` onder de toetsmatrijs). De Toetsevaluatie
  las die regel al voor oefentoetsen; nu werkt het ook voor gebouwde toetsen. Codes die niet in het register staan,
  worden genegeerd; die vragen koppelt de evaluatie zoals vroeger.
- De vragenbank bewaart de code per vraag, dus de itembank kent hem ook vóór de afname.
- **Bestaande matrijzen** krijgen bij het starten hun codes aangevuld (`cirkel/kern/codes.py`): eerst uit het
  nakijken (alleen koppelingen die 'bevestigd' of 'auto' zijn), daarna legt Claude (abonnement) de rest naast het
  register. Alleen zekerheid 'hoog' wordt ingevuld; de twijfelgevallen staan op `/codes`. Een bestaande code blijft
  staan, elke wijziging krijgt een kopie in `versies/` met `_toetscirkel_` in de naam. Uitzetten:
  `TOETSCIRKEL_GEEN_CLAUDE=1`.

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
  test_codes.py       codes aanvullen in bestaande matrijzen (nakijken en Claude, nep-Claude)
  test_scans.py       scan herkennen, analyse starten, Twijfel/, automatisch definitief
docs/
  AFSPRAKEN.md        wat wel/niet in Git, privacy, toetscode, werkwijze
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Belangrijk

Er staan geen leerlinggegevens in deze repository en dat blijft zo. Zie `docs/AFSPRAKEN.md`.
