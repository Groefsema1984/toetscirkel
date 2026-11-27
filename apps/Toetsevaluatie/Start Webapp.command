#!/bin/bash
# Dubbelklik om de webapp Toetsevaluatie te starten. De browser opent vanzelf.
cd "$(dirname "$0")" || exit 1
SLEUTELNAAM="toetsevaluatie-anthropic"
ADRES="http://127.0.0.1:5055"

stop() {
  echo
  echo "$1"
  echo
  read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
  exit 1
}

echo "=== Toetsevaluatie webapp ==="

# Draait de app al? Is het de nieuwste versie, dan alleen de browser openen; anders de oude stoppen.
if curl -s -o /dev/null --max-time 2 "$ADRES/"; then
  DRAAIT=$(curl -s --max-time 2 "$ADRES/api/versie" | grep -o '"versie": *"[0-9a-f]*"' | grep -o '[0-9a-f]\{12\}')
  NU=""
  [ -x .venv/bin/python ] && NU=$(.venv/bin/python -c "import sys; sys.path.insert(0, 'webapp'); from versie import code_versie; print(code_versie())" 2>/dev/null)
  if [ -n "$NU" ] && [ "$DRAAIT" = "$NU" ]; then
    open "$ADRES"
    stop "De app draaide al (nieuwste versie); de browser is geopend."
  fi
  echo "Er draait nog een oudere versie van de Toetsevaluatie. Die wordt nu gestopt ..."
  PIDS=$(lsof -ti tcp:5055 -sTCP:LISTEN 2>/dev/null)
  [ -n "$PIDS" ] && kill $PIDS 2>/dev/null
  for i in $(seq 1 10); do
    curl -s -o /dev/null --max-time 1 "$ADRES/" || break
    sleep 1
  done
  curl -s -o /dev/null --max-time 1 "$ADRES/" && stop "De oude versie kon niet worden gestopt. Sluit zijn Terminal-venster en dubbelklik opnieuw."
  echo "Gestopt. De nieuwe versie start nu (een analyse die liep, kun je in de app hervatten)."
fi

# 1. Python
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  xcode-select --install 2>/dev/null
  stop "Python 3 ontbreekt op deze Mac. Kies in het venster dat nu opent 'Installeer', wacht tot dat klaar is en dubbelklik daarna opnieuw op dit bestand."
fi

# 2. Eigen Python-omgeving met de benodigde libraries (alleen als er iets ontbreekt)
if [ ! -x .venv/bin/python ]; then
  echo "Eerste keer: Python-omgeving aanmaken ..."
  python3 -m venv .venv || stop "Het aanmaken van de Python-omgeving is mislukt."
fi
if ! .venv/bin/python -c "import anthropic, watchdog, docx, pymupdf, openpyxl, flask" 2>/dev/null; then
  echo "Libraries installeren (duurt een minuut) ..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --upgrade anthropic watchdog python-docx pymupdf openpyxl flask \
    || stop "Installeren is mislukt. Controleer je internetverbinding en probeer het opnieuw."
fi

# 3. Claude Code (je abonnement): daarmee kijkt de app na. API-tegoed alleen als jij daar in de app op klikt.
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] || [ -x "$HOME/.claude/local/claude" ]; then
  echo "Claude Code gevonden: de app werkt via je Claude-abonnement. API-tegoed alleen na jouw toestemming in de app."
else
  echo "Claude Code niet gevonden. Start één keer Toetsconstructie om het te installeren; tot die tijd vraagt de app"
  echo "bij elke stap of je API-tegoed wilt gebruiken."
fi

# 4. API-sleutel uit de sleutelhanger (alleen nodig als je ooit toestemming geeft voor API-tegoed)
if [ -z "$ANTHROPIC_API_KEY" ]; then
  ANTHROPIC_API_KEY=$(security find-generic-password -a "$USER" -s "$SLEUTELNAAM" -w 2>/dev/null)
fi
while [ -z "$ANTHROPIC_API_KEY" ]; do
  echo
  echo "Plak je Anthropic API-sleutel (Cmd+V) en druk op Enter."
  echo "Je ziet niets verschijnen tijdens het plakken; dat hoort zo."
  read -r -s INVOER
  SLEUTEL=$(printf '%s' "$INVOER" | grep -o 'sk-ant-[A-Za-z0-9_-]*' | head -n 1)
  if [ -z "$SLEUTEL" ]; then
    SLEUTEL=$(printf '%s' "$INVOER" | tr -cd 'A-Za-z0-9_-')
  fi
  if [ ${#SLEUTEL} -lt 20 ]; then
    echo
    echo "Er is geen sleutel aangekomen (${#SLEUTEL} tekens ontvangen). Probeer het nog een keer. Stoppen kan met Ctrl+C."
    continue
  fi
  ANTHROPIC_API_KEY="$SLEUTEL"
  security add-generic-password -a "$USER" -s "$SLEUTELNAAM" -w "$ANTHROPIC_API_KEY" -U \
    && echo "Sleutel bewaard in de sleutelhanger."
done
export ANTHROPIC_API_KEY

# 5. Starten en de browser openen zodra de app reageert
(
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    sleep 1
    if curl -s -o /dev/null --max-time 1 "$ADRES/"; then open "$ADRES"; break; fi
  done
) &
echo
echo "Laat dit venster open zolang je de app gebruikt. Stoppen: Ctrl+C."
.venv/bin/python webapp/app.py
echo
read -n 1 -s -r -p "Gestopt. Druk op een toets om dit venster te sluiten."
