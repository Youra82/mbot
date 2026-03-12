#!/bin/bash

# Farben
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$PROJECT_ROOT"

echo -e "${BLUE}======================================================================${NC}"
echo -e "              mbot MDEF-MERS — Status Dashboard"
echo -e "${BLUE}======================================================================${NC}"

# --- Aktiver Trade ---
echo -e "\n${YELLOW}[ AKTIVER TRADE (global_state.json) ]${NC}"
STATE_FILE="$PROJECT_ROOT/artifacts/tracker/global_state.json"
if [ -f "$STATE_FILE" ]; then
    ACTIVE=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('active_symbol','null'))" 2>/dev/null)
    if [ "$ACTIVE" != "None" ] && [ "$ACTIVE" != "null" ] && [ -n "$ACTIVE" ]; then
        echo -e "${GREEN}  Symbol:     $ACTIVE${NC}"
        python3 -c "
import json
d = json.load(open('$STATE_FILE'))
print(f\"  Timeframe:  {d.get('active_timeframe','?')}\")
print(f\"  Seite:      {d.get('side','?').upper()}\")
print(f\"  Entry:      {d.get('entry_price','?')}\")
print(f\"  SL:         {d.get('sl_price','?')}\")
print(f\"  TP:         {d.get('tp_price','?')}\")
print(f\"  Kontrakte:  {d.get('contracts','?')}\")
print(f\"  Seit:       {d.get('active_since','?')}\")
" 2>/dev/null
    else
        echo -e "  ${CYAN}Kein aktiver Trade.${NC}"
    fi
else
    echo -e "  ${RED}global_state.json nicht gefunden.${NC}"
fi

# --- Optimierte Configs ---
echo -e "\n${YELLOW}[ OPTIMIERTE CONFIGS (config_*_mers.json) ]${NC}"
CONFIGS=$(find "$PROJECT_ROOT/src/mbot/strategy/configs" -name "config_*_mers.json" 2>/dev/null)
if [ -n "$CONFIGS" ]; then
    for cfg in $CONFIGS; do
        NAME=$(basename "$cfg")
        MODIFIED=$(date -r "$cfg" "+%Y-%m-%d %H:%M" 2>/dev/null || stat -c "%y" "$cfg" 2>/dev/null | cut -d'.' -f1)
        echo -e "  ${GREEN}$NAME${NC}  (${CYAN}$MODIFIED${NC})"
    done
else
    echo -e "  ${CYAN}Keine Configs gefunden — run_pipeline.sh noch nicht ausgefuehrt.${NC}"
fi

# --- Letzter Optimizer-Run ---
echo -e "\n${YELLOW}[ LETZTER OPTIMIZER-RUN ]${NC}"
OPT_FILE="$PROJECT_ROOT/artifacts/results/last_optimizer_run.json"
if [ -f "$OPT_FILE" ]; then
    python3 -c "
import json
d = json.load(open('$OPT_FILE'))
ts = d.get('timestamp','?')
strats = d.get('strategies',[])
print(f'  Timestamp:   {ts}')
print(f'  Strategien:  {len(strats)}')
for s in strats:
    sym = s.get('symbol','?')
    tf  = s.get('timeframe','?')
    wr  = s.get('win_rate_pct','?')
    pnl = s.get('pnl_pct','?')
    print(f'    {sym} {tf} | WR={wr}% PnL={pnl}%')
" 2>/dev/null
else
    echo -e "  ${CYAN}Kein Optimizer-Run gefunden.${NC}"
fi

# --- Settings ---
echo -e "\n${YELLOW}[ AKTIVE SYMBOLE (settings.json) ]${NC}"
python3 -c "
import json
d = json.load(open('$PROJECT_ROOT/settings.json'))
strats = d.get('live_trading_settings',{}).get('active_strategies',[])
for s in strats:
    active = s.get('active', False)
    status = '\033[0;32mAKTIV\033[0m' if active else '\033[0;31minaktiv\033[0m'
    print(f\"  {s.get('symbol','?')} {s.get('timeframe','?')}  — {status}\")
risk = d.get('risk',{})
print(f\"  Hebel: {risk.get('leverage','?')}x | Margin: {risk.get('margin_mode','?')}\")
" 2>/dev/null

# --- Letzte Log-Zeilen ---
echo -e "\n${YELLOW}[ LETZTE LOGS (master_runner.log, 15 Zeilen) ]${NC}"
LOG_FILE="$PROJECT_ROOT/logs/master_runner.log"
if [ -f "$LOG_FILE" ]; then
    tail -15 "$LOG_FILE"
else
    echo -e "  ${CYAN}Keine Log-Datei gefunden.${NC}"
fi

# --- Cron-Log ---
echo -e "\n${YELLOW}[ LETZTE CRON-AUSGABE (cron.log, 10 Zeilen) ]${NC}"
CRON_LOG="$PROJECT_ROOT/logs/cron.log"
if [ -f "$CRON_LOG" ]; then
    tail -10 "$CRON_LOG"
else
    echo -e "  ${CYAN}Kein cron.log gefunden.${NC}"
fi

echo -e "\n${BLUE}======================================================================${NC}"
