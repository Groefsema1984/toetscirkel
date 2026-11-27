#!/bin/bash
# Dubbelklik om de app Toetsconstructie te starten. De browser opent vanzelf.
# De eerste keer installeert dit bestand wat nodig is en laat het u inloggen met uw Claude-abonnement.
cd "$(dirname "$0")" || exit 1
ADRES="http://127.0.0.1:5057"
export PATH="$PWD/.node/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# Geen API-sleutel doorgeven: het gebruik loopt via het abonnement, niet via betaald API-tegoed.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

stop() {
  echo
  echo "$1"
  echo
  read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten."
  exit 1
}

echo "=== Toetsconstructie ==="

# Draait de app al? Dan alleen de browser openen.
if curl -s -o /dev/null --max-time 2 "$ADRES/api/status"; then
  open "$ADRES"
  stop "De app draaide al; de browser is geopend."
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
if ! .venv/bin/python -c "import flask, pypdf, docx, matplotlib" 2>/dev/null; then
  echo "Libraries installeren (duurt een minuut) ..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --upgrade flask pypdf python-docx matplotlib \
    || stop "Installeren is mislukt. Controleer uw internetverbinding en probeer het opnieuw."
fi

# 3. Node.js voor het maken van de Word-bestanden. Ontbreekt het, dan komt er een eigen kopie in deze map (geen beheerdersrechten nodig).
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js ontbreekt; een eigen kopie wordt in deze map gezet (ongeveer 50 MB) ..."
  case "$(uname -m)" in arm64) BOUW="arm64" ;; *) BOUW="x64" ;; esac
  BASIS="https://nodejs.org/dist/latest-v22.x"
  REGEL=$(curl -fsSL "$BASIS/SHASUMS256.txt" | grep "darwin-$BOUW.tar.gz") || stop "Node.js kon niet worden opgehaald. Controleer uw internetverbinding."
  SOM=$(echo "$REGEL" | awk '{print $1}'); NAAM=$(echo "$REGEL" | awk '{print $2}')
  curl -fL --progress-bar -o ".node.tar.gz" "$BASIS/$NAAM" || stop "Het downloaden van Node.js is mislukt."
  if [ "$(shasum -a 256 .node.tar.gz | awk '{print $1}')" != "$SOM" ]; then rm -f .node.tar.gz; stop "De download van Node.js is beschadigd. Probeer het opnieuw."; fi
  rm -rf .node && mkdir .node && tar -xzf .node.tar.gz -C .node --strip-components=1 && rm -f .node.tar.gz
  command -v node >/dev/null 2>&1 || stop "Node.js installeren is mislukt."
fi

# 4. Claude Code: het programma waarmee de app Claude op de achtergrond laat werken
if ! command -v claude >/dev/null 2>&1; then
  echo
  echo "Claude Code staat nog niet op deze Mac. Dat is het officiële programma van Anthropic"
  echo "waarmee deze app Claude laat werken binnen uw abonnement."
  read -r -p "Druk op Enter om het nu te installeren (of sluit dit venster om te stoppen). "
  curl -fsSL https://claude.ai/install.sh | bash || stop "Het installeren van Claude Code is mislukt. Zie LEESMIJ.txt voor de handmatige stappen."
  hash -r
  command -v claude >/dev/null 2>&1 || stop "Claude Code is geïnstalleerd maar nog niet vindbaar. Sluit dit venster en dubbelklik opnieuw op dit bestand."
fi

# 5. Inloggen met het abonnement (alleen als dat nog niet is gebeurd)
if ! claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
  echo
  echo "U bent nog niet ingelogd bij Claude Code. Er opent zo een browservenster:"
  echo "log daar in met hetzelfde account als uw Claude-abonnement en kom daarna hier terug."
  read -r -p "Druk op Enter om in te loggen. "
  claude auth login --claudeai || stop "Het inloggen is niet gelukt. Dubbelklik opnieuw op dit bestand om het nog eens te proberen."
fi

# 6. Starten en de browser openen zodra de app reageert
(
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    sleep 1
    if curl -s -o /dev/null --max-time 1 "$ADRES/api/status"; then open "$ADRES"; break; fi
  done
) &
echo
echo "Laat dit venster open zolang u de app gebruikt. Stoppen: Ctrl+C."
.venv/bin/python app.py
echo
read -n 1 -s -r -p "Gestopt. Druk op een toets om dit venster te sluiten."
