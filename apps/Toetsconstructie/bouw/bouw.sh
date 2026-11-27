#!/bin/bash
# Controleert en bouwt één versie. Gebruik: bouw/bouw.sh <pad/naar/toets_A.json>
set -e
HIER="$(cd "$(dirname "$0")" && pwd)"; JSON="$1"; UIT="$(dirname "$JSON")/uit"
node "$HIER/controle.js" "$JSON" || { echo "CONTROLE NIET AKKOORD: los eerst de fouten op."; exit 1; }
node "$HIER/build.js" "$JSON" "$UIT"
V=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['meta']['versie'])" "$JSON")
OPG=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['opgaven_bestand'])" "$UIT/bouwverslag_$V.json")
python3 "$HIER/leesverder.py" "$UIT/$OPG"
