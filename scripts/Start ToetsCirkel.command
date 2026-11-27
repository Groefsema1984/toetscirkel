#!/bin/bash
# Dubbelklik om ToetsCirkel te starten: construeren, evalueren en oefenen in één app op http://127.0.0.1:5050
# De losse apps hoeven niet meer apart te worden gestart. Hun gegevens blijven waar ze altijd stonden.
cd "$(dirname "$0")/.." || exit 1
ADRES="http://127.0.0.1:5050"
SLEUTELNAAM="toetsevaluatie-anthropic"      # dezelfde sleutel als de Toetsevaluatie en de Oefentool
DOCS="$HOME/Documents"
export PATH="$DOCS/Toetsconstructie/.node/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

stop() {
  echo
  echo "$1"
  echo
  read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
  exit 1
}

echo "=== ToetsCirkel ==="

# Draait ToetsCirkel al? Dan alleen de browser openen.
if curl -s -o /dev/null --max-time 2 "$ADRES/api/toetscirkel"; then
  open "$ADRES"
  stop "ToetsCirkel draaide al; de browser is geopend."
fi

# Losse apps die nog draaien eerst stoppen, anders werken twee programma's tegelijk aan dezelfde gegevens.
for POORT in 5055 5056 5057; do
  PIDS=$(lsof -ti tcp:$POORT -sTCP:LISTEN 2>/dev/null)
  if [ -n "$PIDS" ]; then
    echo "Een losse app op poort $POORT wordt gestopt (ToetsCirkel neemt het over) ..."
    kill $PIDS 2>/dev/null
  fi
done

# 1. Python
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  xcode-select --install 2>/dev/null
  stop "Python 3 ontbreekt op deze Mac. Kies in het venster dat nu opent 'Installeer' en dubbelklik daarna opnieuw op dit bestand."
fi

# 2. Python-omgeving: de bestaande van de Oefentool hergebruiken (die heeft al bijna alles), zodat er geen
#    honderden MB extra nodig zijn. Alleen wat ontbreekt wordt erbij gezet.
PY=""
for KANDIDAAT in "$DOCS/Oefentool/.venv/bin/python" "$DOCS/Toetsevaluatie/.venv/bin/python" "$DOCS/Toetsconstructie/.venv/bin/python" ".venv/bin/python"; do
  if [ -x "$KANDIDAAT" ]; then PY="$KANDIDAAT"; break; fi
done
if [ -z "$PY" ]; then
  echo "Eerste keer: Python-omgeving aanmaken ..."
  python3 -m venv .venv || stop "Het aanmaken van de Python-omgeving is mislukt."
  PY=".venv/bin/python"
fi
if ! "$PY" -c "import flask, pypdf, docx, matplotlib, anthropic, watchdog, pymupdf, openpyxl, pptx" 2>/dev/null; then
  echo "Ontbrekende onderdelen installeren ..."
  "$PY" -m pip install --quiet flask pypdf python-docx matplotlib anthropic watchdog pymupdf openpyxl python-pptx \
    || stop "Installeren is mislukt. Controleer je internetverbinding en of er nog wat schijfruimte vrij is."
fi

# 3. Bouwmotor voor Word-bestanden van de Oefentool (zoals Start Oefentool.command al deed)
if [ ! -d "$DOCS/Oefentool/bouw/node_modules/docx" ] && [ -d "$DOCS/Toetsconstructie/bouw/node_modules" ]; then
  cp -R "$DOCS/Toetsconstructie/bouw/node_modules" "$DOCS/Oefentool/bouw/" 2>/dev/null
fi

# 4. Claude Code (abonnement) en eventueel de API-sleutel uit de sleutelhanger (alleen gebruikt na jouw toestemming in de app)
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
  echo "Claude Code gevonden: ToetsCirkel werkt via je Claude-abonnement."
else
  echo "Claude Code niet gevonden. Start één keer Toetsconstructie om het te installeren."
fi
if [ -z "$ANTHROPIC_API_KEY" ]; then
  ANTHROPIC_API_KEY=$(security find-generic-password -a "$USER" -s "$SLEUTELNAAM" -w 2>/dev/null)
  [ -n "$ANTHROPIC_API_KEY" ] && export ANTHROPIC_API_KEY
fi

# 5. Starten en de browser openen zodra ToetsCirkel reageert
(
  for i in $(seq 1 30); do
    sleep 1
    if curl -s -o /dev/null --max-time 1 "$ADRES/api/toetscirkel"; then open "$ADRES"; break; fi
  done
) &
echo
echo "Laat dit venster open zolang je ToetsCirkel gebruikt. Stoppen: Ctrl+C."
"$PY" -m cirkel.server
echo
read -n 1 -s -r -p "Gestopt. Druk op een toets om dit venster te sluiten."
