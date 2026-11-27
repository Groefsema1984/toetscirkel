# ToetsCirkel

Toetsconstructie, Toetsevaluatie en Oefentool samengevoegd tot één lerend systeem:
construeren → evalueren → op maat oefenen → bijsturen.

Het plan staat in het document *ToetsCirkel: architectuur- en migratieplan*. Deze repository volgt de fases uit dat plan.

## Stand: fase 0 (veilig fundament)

De drie apps staan ongewijzigd in `apps/`. Ze draaien nog zoals altijd vanuit `~/Documents/<app>`; deze repository is
vanaf nu de plek waar de code wordt bijgehouden.

```
apps/
  Toetsconstructie/   lokale Flask-app (poort 5057), Claude Code via abonnement, Word-bouwmotor, kennisbank
  Toetsevaluatie/     lokale Flask-app (poort 5055), scans nakijken, analyse, leerprofielen
  Oefentool/          lokale Flask-app (poort 5056), oefenmateriaal, itembank, leerloop
scripts/
  Backup.command      back-up van databases en kennisbank (dubbelklik)
  Installeer.command  code uit deze repository in de apps zetten, gegevens blijven staan (dubbelklik)
  Gouden toets.command  gouden toets vastleggen of controleren (dubbelklik)
tests/
  gouden_toets.py     momentopname van een afgeronde toets maken en vergelijken
  test_fase0.py       tests met verzonnen gegevens
docs/
  AFSPRAKEN.md        wat wel/niet in Git, privacy, toetscode, werkwijze
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Belangrijk

Er staan geen leerlinggegevens in deze repository en dat blijft zo. Zie `docs/AFSPRAKEN.md`.
