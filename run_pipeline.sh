#!/bin/bash
# run_pipeline.sh - Angepasst fuer mbot (Momentum Breakout)
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "       mbot Momentum Optimierungs-Pipeline"
echo -e "=======================================================${NC}"

# --- Pfade definieren ---
VENV_PATH=".venv/bin/activate"
OPTIMIZER="src/mbot/analysis/optimizer.py"

# --- Umgebung aktivieren ---
if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden ($VENV_PATH). Bitte install.sh ausfuehren.${NC}"
    exit 1
fi
source "$VENV_PATH"
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"

# --- AUFRAEUM-ASSISTENT ---
echo -e "\n${YELLOW}Moechtest du alle alten, generierten Configs vor dem Start loeschen?${NC}"
read -p "Dies wird fuer einen kompletten Neustart empfohlen. (j/n) [Standard: n]: " CLEANUP_CHOICE; CLEANUP_CHOICE=${CLEANUP_CHOICE:-n}
if [[ "$CLEANUP_CHOICE" == "j" || "$CLEANUP_CHOICE" == "J" ]]; then
    echo -e "${YELLOW}Loesche alte Konfigurationen...${NC}"
    rm -f src/mbot/strategy/configs/config_*.json
    echo -e "${GREEN}✔ Aufraeumen abgeschlossen.${NC}"
else
    echo -e "${GREEN}✔ Alte Ergebnisse werden beibehalten.${NC}"
fi

# --- Interaktive Abfrage ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 1h 4h): " TIMEFRAMES

echo -e "\n${BLUE}--- Empfehlung: Optimaler Rueckblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"
printf "| Zeitfenster | Empfohlener Rueckblick (Tage)  |\n"
printf "+-------------+--------------------------------+\n"
printf "| 5m, 15m     | 15 - 90 Tage                   |\n"
printf "| 30m, 1h     | 180 - 365 Tage                 |\n"
printf "| 2h, 4h      | 550 - 730 Tage                 |\n"
printf "| 6h, 1d      | 1095 - 1825 Tage               |\n"
printf "+-------------+--------------------------------+\n"
read -p "Startdatum (JJJJ-MM-TT) oder 'a' fuer Automatik [Standard: a]: " START_DATE_INPUT; START_DATE_INPUT=${START_DATE_INPUT:-a}

read -p "Enddatum (JJJJ-MM-TT) [Standard: Heute]: " END_DATE; END_DATE=${END_DATE:-$(date +%F)}
read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL; START_CAPITAL=${START_CAPITAL:-1000}
read -p "CPU-Kerne [Standard: -1 fuer alle]: " N_CORES; N_CORES=${N_CORES:--1}
read -p "Anzahl Trials [Standard: 200]: " N_TRIALS; N_TRIALS=${N_TRIALS:-200}

echo -e "\n${YELLOW}Waehle einen Optimierungs-Modus:${NC}"
echo "  1) Strenger Modus (Profitabel & Sicher)"
echo "  2) 'Finde das Beste'-Modus (Max Profit)"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE; OPTIM_MODE=${OPTIM_MODE:-1}

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

for symbol in $SYMBOLS; do
    for timeframe in $TIMEFRAMES; do

        # --- DATUMSBERECHNUNG ---
        if [ "$START_DATE_INPUT" == "a" ]; then
            lookback_days=365
            case "$timeframe" in
                5m|15m) lookback_days=60 ;;
                30m|1h) lookback_days=365 ;;
                2h|4h)  lookback_days=730 ;;
                6h|1d)  lookback_days=1095 ;;
            esac
            FINAL_START_DATE=$(date -d "$lookback_days days ago" +%F 2>/dev/null || date -v-${lookback_days}d +%F)
            echo -e "${YELLOW}INFO: Automatisches Startdatum fuer $timeframe (${lookback_days} Tage Rueckblick) gesetzt auf: $FINAL_START_DATE${NC}"
        else
            FINAL_START_DATE=$START_DATE_INPUT
        fi

        echo -e "\n${BLUE}=======================================================${NC}"
        echo -e "${BLUE}  Bearbeite Pipeline fuer: $symbol ($timeframe)${NC}"
        echo -e "${BLUE}  Datenzeitraum: $FINAL_START_DATE bis $END_DATE${NC}"
        echo -e "${BLUE}=======================================================${NC}"

        echo -e "\n${GREEN}>>> Starte Momentum-Optimierung fuer $symbol ($timeframe)...${NC}"
        python3 "$OPTIMIZER" \
            --symbols "$symbol" \
            --timeframes "$timeframe" \
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
            echo -e "${RED}Fehler im Optimizer fuer $symbol ($timeframe). Ueberspringe...${NC}"
        fi
    done
done

echo -e "\n${GREEN}>>> Optimierung abgeschlossen. Zeige Ergebnisse...${NC}\n"
python3 src/mbot/analysis/show_results.py --mode 1

# --- Optional: settings.json aktualisieren ---
echo ""
echo -e "${YELLOW}─────────────────────────────────────────────────${NC}"
read -p "Sollen die optimierten Strategien automatisch in settings.json eingetragen werden? (j/n): " AUTO_UPDATE
AUTO_UPDATE="${AUTO_UPDATE//[$'\r\n ']/}"

if [[ "$AUTO_UPDATE" == "j" || "$AUTO_UPDATE" == "J" || "$AUTO_UPDATE" == "y" || "$AUTO_UPDATE" == "Y" ]]; then
    echo -e "${BLUE}Uebertrage Ergebnisse nach settings.json...${NC}"

    python3 << 'EOF'
import json
import os

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

print(f"✔ {len(strategies)} Strategie(n) wurden in settings.json eingetragen:")
for s in strategies:
    print(f"   - {s['symbol']} ({s['timeframe']})")
EOF

    echo -e "${GREEN}✔ settings.json erfolgreich aktualisiert!${NC}"
else
    echo -e "${YELLOW}Keine Aenderungen an settings.json vorgenommen.${NC}"
fi

deactivate
echo -e "\n${BLUE}✔ Alle Pipeline-Aufgaben erfolgreich abgeschlossen!${NC}"
