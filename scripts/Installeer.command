#!/bin/bash
# Dubbelklik: zet de nieuwste code uit de repository in je drie apps (gegevens blijven staan).
cd "$(dirname "$0")/.." || exit 1
bash scripts/installeer.sh
echo
read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
