#!/bin/bash
# run_pipeline.sh — mbot MDEF-MERS Pipeline
#
# Schritt 1: optimizer.py    → MERS Signal-Optimierung (Optuna)
# Schritt 2: run_backtest.py → Validierung der besten Configs
# Schritt 3: Ergebnisse anzeigen

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
VENV_PATH="$SCRIPT_DIR/.venv/bin/activate"

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}FEHLER: .venv nicht gefunden. Erst install.sh ausfuehren!${NC}"
    exit 1
fi
source "$VENV_PATH"
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"

echo ""
echo "======================================================="
echo "       mbot — MDEF-MERS Optimierungs-Pipeline"
echo "======================================================="
echo ""

# ── 1. Alte Configs loeschen? ────────────────────────────────────────────────
CONFIGS_DIR="$SCRIPT_DIR/src/mbot/strategy/configs"
if ls "$CONFIGS_DIR"/config_*.json 2>/dev/null | grep -q .; then
    read -p "Alte Configs vor dem Start loeschen (Neustart)? (j/n) [Standard: n]: " RESET_CONFIGS
    RESET_CONFIGS="${RESET_CONFIGS//[$'\r\n ']/}"
    if [[ "$RESET_CONFIGS" == "j" || "$RESET_CONFIGS" == "J" || "$RESET_CONFIGS" == "y" || "$RESET_CONFIGS" == "Y" ]]; then
        rm -f "$CONFIGS_DIR"/config_*.json
        echo -e "${GREEN}✔ Alte Configs geloescht — Neustart.${NC}"
    else
        echo -e "${GREEN}✔ Bestehende Configs werden beibehalten.${NC}"
    fi
else
    echo -e "${CYAN}ℹ  Keine bestehenden Configs gefunden — werden neu erstellt.${NC}"
fi

# ── 2. Coins / Timeframes ────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Coins und Timeframes:${NC}"
echo "  Leer lassen → automatisch aus active_strategies in settings.json uebernehmen"
echo ""
read -p "Coin(s) eingeben (z.B. BTC ETH SOL) [leer=auto]: " COINS_INPUT
read -p "Timeframe(s) eingeben (z.B. 6h 1d) [leer=auto]: "  TF_INPUT

COINS_INPUT="${COINS_INPUT//[$'\r\n']/}"
TF_INPUT="${TF_INPUT//[$'\r\n']/}"

if [ -n "$COINS_INPUT" ]; then export MBOT_OVERRIDE_COINS="$COINS_INPUT"; fi
if [ -n "$TF_INPUT" ];    then export MBOT_OVERRIDE_TFS="$TF_INPUT";     fi

PAIRS=$($PYTHON - <<'PYEOF'
import os, json

coins_raw = os.environ.get('MBOT_OVERRIDE_COINS', '').strip()
tfs_raw   = os.environ.get('MBOT_OVERRIDE_TFS',   '').strip()

try:
    with open('settings.json') as f:
        s = json.load(f)
    active     = s.get('live_trading_settings', {}).get('active_strategies', [])
    auto_coins = list(dict.fromkeys(x['symbol']    for x in active if x.get('symbol')))
    auto_tfs   = list(dict.fromkeys(x['timeframe'] for x in active if x.get('timeframe')))
except Exception:
    auto_coins = ['BTC/USDT:USDT']
    auto_tfs   = ['6h']

def to_symbol(coin):
    coin = coin.strip().upper()
    return coin if '/' in coin else f"{coin}/USDT:USDT"

coins = [to_symbol(c) for c in coins_raw.split()] if coins_raw else auto_coins
tfs   = [t.strip() for t in tfs_raw.split()]      if tfs_raw   else auto_tfs

if not coins: coins = ['BTC/USDT:USDT']
if not tfs:   tfs   = ['6h']

for sym in coins:
    for tf in tfs:
        print(f"{sym} {tf}")
PYEOF
)

echo -e "${CYAN}Optimierungs-Paare:${NC}"
echo "$PAIRS" | while read -r sym tf; do
    echo "  → $sym ($tf)"
done
echo ""

# ── 3. History-Tage ──────────────────────────────────────────────────────────
echo -e "${YELLOW}--- Empfehlung: Optimaler Rueckblick-Zeitraum ---${NC}"
printf "  %-12s  %s\n" "Zeitfenster" "Empfohlener Rueckblick (Tage)"
printf "  %-12s  %s\n" "──────────" "──────────────────────────"
printf "  %-12s  %s\n" "5m, 15m"    "60 - 180 Tage"
printf "  %-12s  %s\n" "30m, 1h"    "180 - 365 Tage"
printf "  %-12s  %s\n" "2h, 4h"     "365 - 730 Tage"
printf "  %-12s  %s\n" "6h, 1d"     "730 - 1095 Tage"
echo ""
read -p "History-Tage (oder 'a' fuer Automatik nach Timeframe) [Standard: a]: " HISTORY_INPUT
HISTORY_INPUT="${HISTORY_INPUT//[$'\r\n ']/}"
HISTORY_INPUT="${HISTORY_INPUT:-a}"

# ── 4. Kapital + Risiko + Hebel ───────────────────────────────────────────────
echo ""
read -p "Startkapital in USDT [Standard: 1000]: " CAP_INPUT
CAP_INPUT="${CAP_INPUT//[$'\r\n ']/}"
if [[ "$CAP_INPUT" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then CAPITAL=$CAP_INPUT; else CAPITAL=1000; fi

read -p "Risiko pro Trade in % [Standard: 1.0]: " RISK_INPUT
RISK_INPUT="${RISK_INPUT//[$'\r\n ']/}"
if [[ "$RISK_INPUT" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then RISK=$RISK_INPUT; else RISK=1.0; fi

read -p "Hebel [Standard: 20]: " LEV_INPUT
LEV_INPUT="${LEV_INPUT//[$'\r\n ']/}"
if [[ "$LEV_INPUT" =~ ^[0-9]+$ ]]; then LEVERAGE=$LEV_INPUT; else LEVERAGE=20; fi

# ── 5. Optimierungs-Modus ────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Optimierungs-Modus:${NC}"
echo "  1) Strenger Modus (Profitabel & Sicher)"
echo "  2) 'Finde das Beste'-Modus (Max Profit)"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE_SEL
OPTIM_MODE_SEL="${OPTIM_MODE_SEL:-1}"

if [ "$OPTIM_MODE_SEL" == "1" ]; then
    OPTIM_MODE_ARG="strict"
    read -p "Max Drawdown % [Standard: 30]: " MAX_DD;  MAX_DD=${MAX_DD:-30}
    read -p "Min Win-Rate % [Standard: 50]: " MIN_WR;  MIN_WR=${MIN_WR:-50}
    read -p "Min PnL %      [Standard: 0]:  " MIN_PNL; MIN_PNL=${MIN_PNL:-0}
else
    OPTIM_MODE_ARG="best_profit"
    read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}
    MIN_WR=0; MIN_PNL=-99999
fi

read -p "Anzahl Trials [Standard: 200]: " N_TRIALS; N_TRIALS=${N_TRIALS:-200}
read -p "CPU-Kerne     [Standard: 1]:   " N_CORES;  N_CORES=${N_CORES:-1}

# ── Pipeline starten ─────────────────────────────────────────────────────────
echo ""
echo "======================================================="
echo "  Pipeline startet..."
echo "======================================================="
echo ""

# ── [Schritt 1/3] MERS Signal-Optimierung ────────────────────────────────────
echo -e "${YELLOW}[Schritt 1/3] MERS Signal-Optimierung (Optuna)...${NC}"

echo "$PAIRS" | while IFS=' ' read -r sym tf; do
    # Datum berechnen
    if [[ "$HISTORY_INPUT" =~ ^[0-9]+$ ]]; then
        LOOKBACK=$HISTORY_INPUT
    else
        case "$tf" in
            5m|15m) LOOKBACK=90   ;;
            30m|1h) LOOKBACK=365  ;;
            2h|4h)  LOOKBACK=730  ;;
            6h|1d)  LOOKBACK=1095 ;;
            *)      LOOKBACK=730  ;;
        esac
    fi
    START_DATE=$(date -d "$LOOKBACK days ago" +%F 2>/dev/null || date -v-${LOOKBACK}d +%F)
    END_DATE=$(date +%F)

    echo ""
    echo -e "${CYAN}  Optimiere: $sym ($tf) | ${START_DATE} → ${END_DATE}${NC}"

    $PYTHON "src/mbot/analysis/optimizer.py" \
        --symbols       "$sym" \
        --timeframes    "$tf" \
        --start_date    "$START_DATE" \
        --end_date      "$END_DATE" \
        --start_capital "$CAPITAL" \
        --risk_per_trade_pct "$RISK" \
        --leverage      "$LEVERAGE" \
        --trials        "$N_TRIALS" \
        --jobs          "$N_CORES" \
        --max_drawdown  "$MAX_DD" \
        --min_win_rate  "$MIN_WR" \
        --min_pnl       "$MIN_PNL" \
        --mode          "$OPTIM_MODE_ARG"

    if [ $? -ne 0 ]; then
        echo -e "${RED}  Fehler bei $sym ($tf). Weiter mit naechstem Paar.${NC}"
    fi
done

echo ""

# ── [Schritt 2/3] Backtest-Validierung ───────────────────────────────────────
echo -e "${YELLOW}[Schritt 2/3] Backtest-Validierung...${NC}"
$PYTHON "$SCRIPT_DIR/run_backtest.py" --capital "$CAPITAL" --risk "$RISK"

echo ""

# ── [Schritt 3/3] Ergebnisse ─────────────────────────────────────────────────
echo -e "${YELLOW}[Schritt 3/3] Ergebnisse...${NC}"
$PYTHON "src/mbot/analysis/show_results.py" --mode 1

echo ""
echo "======================================================="
echo -e "  ${GREEN}Pipeline abgeschlossen!${NC}"
echo ""
echo "  Naechste Schritte:"
echo "    1. Ergebnisse pruefen:     ./show_results.sh"
echo "    2. Settings aktualisieren: settings.json → active_strategies"
echo "    3. Bot starten:            ./master_runner.sh"
echo "======================================================="

deactivate
