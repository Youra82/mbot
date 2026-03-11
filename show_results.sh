#!/bin/bash
# show_results.sh - mbot Backtest-Ergebnisse anzeigen
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
echo "  1) Zusammenfassung (Uebersicht aller Strategien)"
echo "  2) Detail-Ansicht  (inkl. Trade-Liste)"
read -p "Auswahl (1-2) [Standard: 1]: " MODE
MODE=${MODE:-1}

if [ "$MODE" == "2" ]; then
    python3 "$RESULTS_SCRIPT" --detail
else
    python3 "$RESULTS_SCRIPT"
fi

deactivate
