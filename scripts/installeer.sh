#!/bin/bash
# Zet de code uit deze repository in de drie app-mappen in ~/Documents.
# - Alleen bestanden die in Git staan worden gekopieerd; gegevens (databases, scans, toetsen,
#   klassenlijsten, instellingen) worden nooit aangeraakt en er wordt niets verwijderd.
# - Eerst een back-up van de gegevens en van de huidige code, zodat je altijd terug kunt.
set -euo pipefail
cd "$(dirname "$0")/.."
DATA="${TOETSCIRKEL_DATA:-$HOME/Documents/ToetsCirkel_data}"
STEMPEL=$(date +%Y%m%d_%H%M%S)

python3 scripts/backup.py

for APP in Toetsconstructie Toetsevaluatie Oefentool; do
  DOEL="$HOME/Documents/$APP"
  [ -d "$DOEL" ] || { echo "Map $DOEL ontbreekt; $APP overgeslagen."; continue; }
  OUD="$DATA/code_voor_installatie/$STEMPEL/$APP"
  mkdir -p "$OUD"
  git ls-files -z "apps/$APP" | while IFS= read -r -d '' F; do
    REL="${F#apps/$APP/}"
    if [ -f "$DOEL/$REL" ]; then mkdir -p "$OUD/$(dirname "$REL")"; cp -p "$DOEL/$REL" "$OUD/$REL"; fi
    mkdir -p "$DOEL/$(dirname "$REL")"
    cp -p "$F" "$DOEL/$REL"
  done
  echo "$APP bijgewerkt (vorige code bewaard in $OUD)."
done
echo "Klaar. Start de apps opnieuw om de nieuwe code te gebruiken."
