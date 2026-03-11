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

# Fuer Modus 3: settings.json aktualisieren?
if [ "$MODE" == "3" ]; then
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${YELLOW}─────────────────────────────────────────────────${NC}"
        read -p "Sollen die optimalen Ergebnisse automatisch in settings.json eingetragen werden? (j/n): " AUTO_UPDATE
        AUTO_UPDATE="${AUTO_UPDATE//[$'\r\n ']/}"

        if [[ "$AUTO_UPDATE" == "j" || "$AUTO_UPDATE" == "J" || "$AUTO_UPDATE" == "y" || "$AUTO_UPDATE" == "Y" ]]; then
            PORTFOLIO_FILE="artifacts/results/optimal_portfolio.json"
            if [ ! -f "$PORTFOLIO_FILE" ]; then
                echo -e "${RED}Fehler: optimal_portfolio.json nicht gefunden!${NC}"
            else
                echo -e "${BLUE}Uebertrage Ergebnisse nach settings.json...${NC}"
                python3 << 'EOF'
import json, os

with open('artifacts/results/optimal_portfolio.json', 'r') as f:
    portfolio = json.load(f)

strategies = []
for entry in portfolio.get('selected_strategies', []):
    strategies.append({
        "symbol": entry['symbol'],
        "timeframe": entry['timeframe'],
        "active": True
    })

if not strategies:
    print("Kein optimales Portfolio gefunden. settings.json bleibt unveraendert.")
else:
    with open('settings.json', 'r') as f:
        settings = json.load(f)
    settings['live_trading_settings']['active_strategies'] = strategies
    with open('settings.json', 'w') as f:
        json.dump(settings, f, indent=4)
    print(f"✔ {len(strategies)} Strategie(n) wurden in settings.json eingetragen:")
    for s in strategies:
        print(f"   - {s['symbol']} ({s['timeframe']})")
EOF
                echo -e "${GREEN}✔ settings.json erfolgreich aktualisiert!${NC}"
            fi
        else
            echo -e "${YELLOW}Keine Aenderungen an settings.json vorgenommen.${NC}"
        fi
    fi
fi

# Fuer Modus 4: Erfolgsmeldung
if [ "$MODE" == "4" ]; then
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✔ Interaktive Charts wurden generiert!${NC}"
    else
        echo -e "${RED}Fehler beim Generieren der Charts.${NC}"
    fi
fi

deactivate
