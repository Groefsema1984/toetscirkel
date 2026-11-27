#!/bin/bash
# Dubbelklik: leg de gouden toets vast (eerste keer) of controleer hem (daarna).
cd "$(dirname "$0")/.." || exit 1
TOETS="${1:-AT5.11_SE_5H_Economie}"
if [ -f "$HOME/Documents/ToetsCirkel_data/gouden/$TOETS.json" ]; then
  python3 tests/gouden_toets.py controleren "$TOETS"
else
  echo "Nog geen momentopname van $TOETS: die wordt nu vastgelegd."
  python3 tests/gouden_toets.py vastleggen "$TOETS"
fi
echo
read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
