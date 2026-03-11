#!/bin/bash
# run_pipeline.sh - mbot Backtest-Pipeline
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "          mbot Momentum Backtest-Pipeline"
echo -e "=======================================================${NC}"

# --- Pfade ---
VENV_PATH=".venv/bin/activate"
BACKTEST_SCRIPT="src/mbot/analysis/run_backtest_cli.py"
SHOW_SCRIPT="src/mbot/analysis/show_results.py"

# --- venv pruefen ---
if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden ($VENV_PATH). Bitte install.sh ausfuehren.${NC}"
    exit 1
fi
source "$VENV_PATH"
echo -e "${GREEN}Virtuelle Umgebung aktiviert.${NC}"

# --- Interaktive Eingabe ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH SOL): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 15m 1h): " TIMEFRAMES

echo -e "\n${BLUE}--- Empfehlung: Rueckblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"
printf "| Zeitfenster | Empfohlener Rueckblick (Tage)  |\n"
printf "+-------------+--------------------------------+\n"
printf "| 1m, 5m      | 7 - 30 Tage                    |\n"
printf "| 15m, 30m    | 30 - 90 Tage                   |\n"
printf "| 1h, 4h      | 180 - 365 Tage                 |\n"
printf "| 1d          | 365 - 730 Tage                 |\n"
printf "+-------------+--------------------------------+\n"

read -p "Startdatum (JJJJ-MM-TT) oder 'a' fuer Automatik [Standard: a]: " START_DATE_INPUT
START_DATE_INPUT=${START_DATE_INPUT:-a}

read -p "Enddatum (JJJJ-MM-TT) [Standard: Heute]: " END_DATE
END_DATE=${END_DATE:-$(date +%F)}

read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL
START_CAPITAL=${START_CAPITAL:-1000}

# --- Automatisches Startdatum ---
FINAL_START_DATE=""
if [ "$START_DATE_INPUT" == "a" ]; then
    # Erster Zeitframe bestimmt den Rueckblick
    FIRST_TF=$(echo $TIMEFRAMES | awk '{print $1}')
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
echo -e "${BLUE}  Starte Backtest: $SYMBOLS | $TIMEFRAMES${NC}"
echo -e "${BLUE}  Zeitraum: $FINAL_START_DATE bis $END_DATE${NC}"
echo -e "${BLUE}  Startkapital: $START_CAPITAL USDT${NC}"
echo -e "${BLUE}=======================================================${NC}"

python3 "$BACKTEST_SCRIPT" \
    --symbols $SYMBOLS \
    --timeframes $TIMEFRAMES \
    --start_date "$FINAL_START_DATE" \
    --end_date "$END_DATE" \
    --start_capital "$START_CAPITAL"

if [ $? -ne 0 ]; then
    echo -e "${RED}Fehler beim Backtest! Abbruch.${NC}"
    deactivate
    exit 1
fi

echo -e "\n${GREEN}Backtest abgeschlossen. Zeige Ergebnisse...${NC}\n"
python3 "$SHOW_SCRIPT"

# --- Optional: settings.json aktualisieren ---
echo ""
echo -e "${YELLOW}───────────────────────────────────────────────────${NC}"
read -p "Sollen die getesteten Symbole/Timeframes in settings.json eingetragen werden? (j/n): " AUTO_UPDATE
AUTO_UPDATE="${AUTO_UPDATE//[$'\r\n ']/}"

if [[ "$AUTO_UPDATE" == "j" || "$AUTO_UPDATE" == "J" ]]; then
    echo -e "${BLUE}Aktualisiere settings.json...${NC}"
    python3 << EOF
import json

symbols_raw   = """$SYMBOLS""".split()
timeframes    = """$TIMEFRAMES""".split()

strategies = []
for sym in symbols_raw:
    symbol = f"{sym}/USDT:USDT" if '/' not in sym else sym
    for tf in timeframes:
        strategies.append({"symbol": symbol, "timeframe": tf, "active": True})

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
