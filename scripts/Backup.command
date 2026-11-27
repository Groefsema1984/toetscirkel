#!/bin/bash
# Dubbelklik: back-up van de databases en het geheugen (kennisbank, leerregels) van de drie apps.
cd "$(dirname "$0")/.." || exit 1
python3 scripts/backup.py
echo
read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
