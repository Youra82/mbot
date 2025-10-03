#!/bin/bash

echo "--- Starte die Einrichtung des Trading Bots ---"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
CODE_DIR="$SCRIPT_DIR/code"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

echo "Projektverzeichnis ist: $SCRIPT_DIR"

echo "Aktualisiere den Server..."
sudo apt-get update -y > /dev/null
echo "Installiere pip und venv..."
sudo apt-get install python3-pip python3-venv -y > /dev/null

echo "Installiere virtuelle Umgebung und Pakete..."
cd "$CODE_DIR"
if [ ! -d ".venv" ]; then
    echo "Erstelle Python Virtual Environment in '$CODE_DIR/.venv'..."
    python3 -m venv .venv
fi

echo "Aktiviere Umgebung und installiere Anforderungen..."
source .venv/bin/activate
pip install -r "$REQUIREMENTS_FILE"
deactivate

echo -e "\n--------------------------------------------------------"
echo -e "✔ Installation erfolgreich abgeschlossen!"
echo -e "Die virtuelle Umgebung ist jetzt in '$CODE_DIR/.venv' bereit."
echo -e "--------------------------------------------------------"
