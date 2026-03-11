#!/bin/bash
# show_results.sh - mbot Ergebnisse anzeigen
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
VENV_PATH=".venv/bin/activate"
RESULTS_SCRIPT="src/mbot/analysis/show_results.py"

if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi
source "$VENV_PATH"

echo -e "\n${YELLOW}Anzeigemodus waehlen:${NC}"
echo "  1) Uebersicht        (Optimizer-Ergebnisse aus Config-Dateien, kein API-Zugriff)"
echo "  2) Frischer Backtest (Aktuelle Daten von Bitget, API-Zugriff)"
echo "  3) Detail-Ansicht    (Frischer Backtest + komplette Trade-Liste)"
read -p "Auswahl (1-3) [Standard: 1]: " MODE
MODE=${MODE:-1}

if [[ ! "$MODE" =~ ^[1-3]$ ]]; then
    echo -e "${RED}Ungueltige Eingabe. Verwende Standard (1).${NC}"
    MODE=1
fi

python3 "$RESULTS_SCRIPT" --mode "$MODE"

deactivate
