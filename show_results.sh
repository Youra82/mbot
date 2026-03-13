#!/bin/bash
# show_results.sh - mbot Ergebnis-Analyse
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

echo -e "\n${YELLOW}Waehle einen Analyse-Modus:${NC}"
echo "  1) Einzel-Analyse (jede Strategie wird isoliert getestet)"
echo "  2) Manuelle Portfolio-Simulation (du waehlst das Team)"
echo "  3) Automatische Portfolio-Optimierung (der Bot waehlt das beste Team)"
echo "  4) Interaktive Charts (Candlestick + Trade-Signale mit Entry/Exit Marker)"
read -p "Auswahl (1-4) [Standard: 1]: " MODE

if [[ ! "$MODE" =~ ^[1-4]?$ ]]; then
    echo -e "${RED}Ungueltige Eingabe! Verwende Standard (1).${NC}"
    MODE=1
fi
MODE=${MODE:-1}

# Max Drawdown nur fuer Modus 3
TARGET_MAX_DD=30
if [ "$MODE" == "3" ]; then
    read -p "Gewuenschter maximaler Drawdown in % fuer die Optimierung [Standard: 30]: " DD_INPUT
    if [[ "$DD_INPUT" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        TARGET_MAX_DD=$DD_INPUT
    else
        echo "Ungueltige Eingabe, verwende Standard: ${TARGET_MAX_DD}%"
    fi
fi

if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: '$RESULTS_SCRIPT' nicht gefunden.${NC}"
    deactivate
    exit 1
fi

python3 "$RESULTS_SCRIPT" --mode "$MODE" --target_max_drawdown "$TARGET_MAX_DD"

deactivate
