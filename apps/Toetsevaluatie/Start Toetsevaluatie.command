#!/bin/bash
# Dubbelklik om de toetsevaluatie te starten. De eerste keer wordt alles geinstalleerd
# en wordt om je Anthropic API-sleutel gevraagd (die komt in de macOS-sleutelhanger).
cd "$(dirname "$0")" || exit 1
SLEUTELNAAM="toetsevaluatie-anthropic"

stop() {
  echo
  echo "$1"
  echo
  read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
  exit 1
}

echo "=== Toetsevaluatie ==="

# 1. Python
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  xcode-select --install 2>/dev/null
  stop "Python 3 ontbreekt op deze Mac. Kies in het venster dat nu opent 'Installeer', wacht tot dat klaar is en dubbelklik daarna opnieuw op dit bestand."
fi

# 2. Eigen Python-omgeving met de benodigde libraries (alleen de eerste keer)
if [ ! -x .venv/bin/python ]; then
  echo "Eerste keer: Python-omgeving aanmaken ..."
  python3 -m venv .venv || stop "Het aanmaken van de Python-omgeving is mislukt."
fi
if ! .venv/bin/python -c "import anthropic, watchdog, docx, pymupdf, openpyxl" 2>/dev/null; then
  echo "Libraries installeren (duurt een paar minuten) ..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --upgrade anthropic watchdog python-docx pymupdf openpyxl \
    || stop "Installeren is mislukt. Controleer je internetverbinding en probeer het opnieuw."
fi

# 3. API-sleutel uit de sleutelhanger, of de eerste keer vragen
if [ -z "$ANTHROPIC_API_KEY" ]; then
  ANTHROPIC_API_KEY=$(security find-generic-password -a "$USER" -s "$SLEUTELNAAM" -w 2>/dev/null)
fi
while [ -z "$ANTHROPIC_API_KEY" ]; do
  echo
  echo "Plak je Anthropic API-sleutel (Cmd+V) en druk op Enter."
  echo "Je ziet niets verschijnen tijdens het plakken; dat hoort zo."
  read -r -s INVOER
  # Alleen de sleutel zelf overhouden (spaties en onzichtbare tekens rond het plakken weg).
  SLEUTEL=$(printf '%s' "$INVOER" | grep -o 'sk-ant-[A-Za-z0-9_-]*' | head -n 1)
  if [ -z "$SLEUTEL" ]; then
    SLEUTEL=$(printf '%s' "$INVOER" | tr -cd 'A-Za-z0-9_-')
  fi
  if [ ${#SLEUTEL} -lt 20 ]; then
    echo
    echo "Er is geen sleutel aangekomen (${#SLEUTEL} tekens ontvangen). Kopieer de sleutel"
    echo "opnieuw in de Console en probeer het nog een keer. Stoppen kan met Ctrl+C."
    continue
  fi
  ANTHROPIC_API_KEY="$SLEUTEL"
  security add-generic-password -a "$USER" -s "$SLEUTELNAAM" -w "$ANTHROPIC_API_KEY" -U \
    && echo "Sleutel ontvangen (${#SLEUTEL} tekens) en bewaard in de sleutelhanger."
done
export ANTHROPIC_API_KEY

# 4. Starten
echo
.venv/bin/python toets_evaluator.py "$@"
CODE=$?
if [ $CODE -eq 3 ]; then
  security delete-generic-password -a "$USER" -s "$SLEUTELNAAM" >/dev/null 2>&1
  stop "De API-sleutel is geweigerd en is uit de sleutelhanger gehaald. Dubbelklik opnieuw en plak een geldige sleutel."
fi
echo
read -n 1 -s -r -p "Gestopt. Druk op een toets om dit venster te sluiten."
