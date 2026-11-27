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

## Stand: fase 1 (één app)

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
  portaal.py          startpagina en doorverwijzen
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
docs/
  AFSPRAKEN.md        wat wel/niet in Git, privacy, toetscode, werkwijze
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Belangrijk

Er staan geen leerlinggegevens in deze repository en dat blijft zo. Zie `docs/AFSPRAKEN.md`.
