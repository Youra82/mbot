#!/bin/bash
# run_pipeline.sh - mbot Signal-Parameter Optimierungs-Pipeline
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "          mbot Momentum Optimierungs-Pipeline"
echo -e "=======================================================${NC}"

# --- Pfade ---
VENV_PATH=".venv/bin/activate"
OPTIMIZER="src/mbot/analysis/optimizer.py"

# --- venv pruefen ---
if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden ($VENV_PATH). Bitte install.sh ausfuehren.${NC}"
    exit 1
fi
source "$VENV_PATH"
echo -e "${GREEN}Virtuelle Umgebung aktiviert.${NC}"

# --- Aufraeum-Assistent ---
echo -e "\n${YELLOW}Moechtest du alle alten, generierten Configs vor dem Start loeschen?${NC}"
read -p "Dies wird fuer einen kompletten Neustart empfohlen. (j/n) [Standard: n]: " CLEANUP_CHOICE
CLEANUP_CHOICE=${CLEANUP_CHOICE:-n}
if [[ "$CLEANUP_CHOICE" == "j" || "$CLEANUP_CHOICE" == "J" ]]; then
    echo -e "${YELLOW}Loesche alte Konfigurationen...${NC}"
    rm -f src/mbot/strategy/configs/config_*.json
    echo -e "${GREEN}Aufraeum abgeschlossen.${NC}"
else
    echo -e "${GREEN}Alte Ergebnisse werden beibehalten.${NC}"
fi

# --- Interaktive Eingabe ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH SOL): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 15m 1h): " TIMEFRAMES

echo -e "\n${BLUE}--- Empfehlung: Rueckblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"
printf "| Zeitfenster | Empfohlener Rueckblick (Tage)  |\n"
printf "+-------------+--------------------------------+\n"
printf "| 1m, 5m      | 14 - 30 Tage                   |\n"
printf "| 15m, 30m    | 30 - 90 Tage                   |\n"
printf "| 1h          | 180 - 365 Tage                 |\n"
printf "| 4h, 1d      | 365 - 730 Tage                 |\n"
printf "+-------------+--------------------------------+\n"

read -p "Startdatum (JJJJ-MM-TT) oder 'a' fuer Automatik [Standard: a]: " START_DATE_INPUT
START_DATE_INPUT=${START_DATE_INPUT:-a}

read -p "Enddatum (JJJJ-MM-TT) [Standard: Heute]: " END_DATE
END_DATE=${END_DATE:-$(date +%F)}

read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL
START_CAPITAL=${START_CAPITAL:-1000}

read -p "CPU-Kerne [Standard: -1 fuer alle]: " N_CORES
N_CORES=${N_CORES:--1}

read -p "Anzahl Trials [Standard: 200]: " N_TRIALS
N_TRIALS=${N_TRIALS:-200}

echo -e "\n${YELLOW}Waehle einen Optimierungs-Modus:${NC}"
echo "  1) Strenger Modus  (Profitabel & Sicher)"
echo "  2) 'Finde das Beste'-Modus (Max Profit)"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE
OPTIM_MODE=${OPTIM_MODE:-1}

if [ "$OPTIM_MODE" == "1" ]; then
    OPTIM_MODE_ARG="strict"
    read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}
    read -p "Min Win-Rate % [Standard: 50]: " MIN_WR; MIN_WR=${MIN_WR:-50}
    read -p "Min PnL %      [Standard: 0]:  " MIN_PNL; MIN_PNL=${MIN_PNL:-0}
else
    OPTIM_MODE_ARG="best_profit"
    read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}
    MIN_WR=0; MIN_PNL=-99999
fi

# --- Automatisches Startdatum ---
FIRST_TF=$(echo $TIMEFRAMES | awk '{print $1}')
if [ "$START_DATE_INPUT" == "a" ]; then
    case "$FIRST_TF" in
        1m|5m)   LOOKBACK=30  ;;
        15m|30m) LOOKBACK=60  ;;
        1h)      LOOKBACK=365 ;;
        4h)      LOOKBACK=730 ;;
        1d)      LOOKBACK=730 ;;
        *)       LOOKBACK=90  ;;
    esac
    FINAL_START_DATE=$(date -d "$LOOKBACK days ago" +%F 2>/dev/null || date -v-${LOOKBACK}d +%F)
    echo -e "${YELLOW}Automatisches Startdatum (${LOOKBACK} Tage Rueckblick): $FINAL_START_DATE${NC}"
else
    FINAL_START_DATE=$START_DATE_INPUT
fi

echo -e "\n${BLUE}=======================================================${NC}"
echo -e "${BLUE}  Optimiere: $SYMBOLS | $TIMEFRAMES${NC}"
echo -e "${BLUE}  Zeitraum: $FINAL_START_DATE bis $END_DATE${NC}"
echo -e "${BLUE}  Kapital: $START_CAPITAL USDT | Trials: $N_TRIALS | Kerne: $N_CORES${NC}"
echo -e "${BLUE}=======================================================${NC}"

python3 "$OPTIMIZER" \
    --symbols $SYMBOLS \
    --timeframes $TIMEFRAMES \
    --start_date "$FINAL_START_DATE" \
    --end_date "$END_DATE" \
    --start_capital "$START_CAPITAL" \
    --jobs "$N_CORES" \
    --trials "$N_TRIALS" \
    --max_drawdown "$MAX_DD" \
    --min_win_rate "$MIN_WR" \
    --min_pnl "$MIN_PNL" \
    --mode "$OPTIM_MODE_ARG"

if [ $? -ne 0 ]; then
    echo -e "${RED}Fehler im Optimizer! Abbruch.${NC}"
    deactivate
    exit 1
fi

echo -e "\n${GREEN}Optimierung abgeschlossen. Zeige Ergebnisse...${NC}\n"
python3 src/mbot/analysis/show_results.py --mode 1

# --- Optional: settings.json aktualisieren ---
echo ""
echo -e "${YELLOW}───────────────────────────────────────────────────${NC}"
read -p "Sollen die optimierten Strategien in settings.json eingetragen werden? (j/n): " AUTO_UPDATE
AUTO_UPDATE="${AUTO_UPDATE//[$'\r\n ']/}"

if [[ "$AUTO_UPDATE" == "j" || "$AUTO_UPDATE" == "J" ]]; then
    echo -e "${BLUE}Aktualisiere settings.json...${NC}"
    python3 << 'EOF'
import json
import os
import re

configs_dir = os.path.join('src', 'mbot', 'strategy', 'configs')
if not os.path.exists(configs_dir):
    print("Keine Configs gefunden.")
    exit(0)

strategies = []
for fn in sorted(os.listdir(configs_dir)):
    if not fn.startswith('config_') or not fn.endswith('_momentum.json'):
        continue
    path = os.path.join(configs_dir, fn)
    try:
        with open(path) as f:
            cfg = json.load(f)
        market = cfg.get('market', {})
        symbol = market.get('symbol')
        tf     = market.get('timeframe')
        if symbol and tf:
            strategies.append({"symbol": symbol, "timeframe": tf, "active": True})
    except Exception:
        pass

with open('settings.json', 'r') as f:
    settings = json.load(f)

settings['live_trading_settings']['active_strategies'] = strategies

with open('settings.json', 'w') as f:
    json.dump(settings, f, indent=4)

print(f"  {len(strategies)} Strategie(n) in settings.json eingetragen:")
for s in strategies:
    print(f"    - {s['symbol']} ({s['timeframe']})")
EOF
    echo -e "${GREEN}settings.json erfolgreich aktualisiert!${NC}"
else
    echo -e "${YELLOW}Keine Aenderungen an settings.json vorgenommen.${NC}"
fi

deactivate
echo -e "\n${BLUE}Pipeline abgeschlossen.${NC}"
