#!/bin/bash
# run_analysis.sh — mbot Wissenschaftliche Analysen (Port von dnabot/run_analysis.sh)
#
# 16 Analysen unter einem Befehl (14 Skripte unter src/mbot/analysis/ + der
# root-level walk_forward_test.py; param_optimizer.py stellt 2 Modi/Menuepunkte).
# Ergebnisse werden als Chart via Telegram gesendet.
#
# Ausfuehrung:
#   ./run_analysis.sh
#   ./run_analysis.sh --no-telegram    (kein Telegram, nur lokale Charts)

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
ANALYSIS_DIR="$SCRIPT_DIR/src/mbot/analysis"
NO_TELEGRAM=""

for arg in "$@"; do
    [[ "$arg" == "--no-telegram" ]] && NO_TELEGRAM="--no-telegram"
done

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}FEHLER: .venv nicht gefunden. Erst install.sh ausfuehren!${NC}"
    exit 1
fi
source "$SCRIPT_DIR/.venv/bin/activate"

# UTF-8 erzwingen -- sonst crashen die Unicode-Pfeile/Symbole in den Charts/Ausgaben
# auf Windows-Konsolen (cp1252-Default), z.B. beim lokalen Testen unter Git Bash.
export PYTHONIOENCODING="utf-8"

# src/ im PYTHONPATH damit 'from mbot.analysis.utils import *' funktioniert
export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH}"

# ─── Menü ─────────────────────────────────────────────────────────────────────

echo ""
echo "======================================================="
echo -e "  ${BOLD}mbot — Wissenschaftliche Analysen (MDEF-MERS)${NC}"
echo "======================================================="
echo ""
echo -e "  ${CYAN}── Prioritaet 1: Fundament ─────────────────────────${NC}"
echo "   1) Walk-Forward Lookback-Analyse"
echo "   2) Slippage & Fee Impact"
echo "   3) Monte Carlo Simulation"
echo "   4) Bootstrap Signifikanztest"
echo ""
echo -e "  ${CYAN}── Prioritaet 2: Direkte Gewinnoptimierung (echte Re-Backtests) ──${NC}"
echo "   5) RR-Ratio Optimierung          (Walk-Forward)"
echo "   6) Entry-Threshold Sweep         (Walk-Forward)"
echo "   7) Parameter Sensitivity         (Tornado-Diagramm)"
echo ""
echo -e "  ${CYAN}── Prioritaet 3: Systemverbesserung ─────────────────${NC}"
echo "   8) Multi-Pair Confirmation"
echo "   9) Anti-Korrelations-Portfolio"
echo "  10) Kelly Position Sizing"
echo ""
echo -e "  ${CYAN}── Prioritaet 4–6: Feintuning & Portfolio ───────────${NC}"
echo "  11) Regime Performance Analysis"
echo "  12) Volatilitaets-Filter Optimierung (explorativ)"
echo "  13) Tageszeit-Analyse"
echo "  14) Regime-adaptive R:R"
echo "  15) Drawdown Duration Analysis"
echo ""
echo -e "  ${CYAN}── Strategie-Vergleich ─────────────────────────────${NC}"
echo "  16) WF Re-Opt vs. Alle Configs       (Langzeit-Vergleich)"
echo ""
echo "   0) Alle Analysen nacheinander ausfuehren"
echo ""
read -p "Auswahl (0-16): " MODE
MODE="${MODE//[$'\r\n ']/}"
echo ""

# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

ask_capital() {
    read -p "Startkapital in USDT [Standard: 100]: " CAP
    CAP="${CAP//[$'\r\n ']/}"
    if ! [[ "$CAP" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then CAP=100; fi
    echo "$CAP"
}

run_mode() {
    local m="$1"
    case "$m" in

    # ── 1: Walk-Forward ───────────────────────────────────────────────────────
    1)
        echo -e "${GREEN}▶ Walk-Forward Lookback-Analyse${NC}"
        echo "  Ermittelt den optimalen Lookback-Zeitraum fuer die woechentliche"
        echo "  Portfolio-Optimierung (run_portfolio_optimizer.py)."
        echo ""
        CAP=$(ask_capital)
        read -p "Min. Trades pro Pair im Lookback-Fenster [Standard: 2]: " MIN_T
        MIN_T="${MIN_T//[$'\r\n ']/}"
        if ! [[ "$MIN_T" =~ ^[0-9]+$ ]]; then MIN_T=2; fi
        read -p "Max. Drawdown-Limit fuer Portfolio-Auswahl in % [Standard: 30]: " MAX_DD
        MAX_DD="${MAX_DD//[$'\r\n ']/}"
        if ! [[ "$MAX_DD" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then MAX_DD=30; fi
        $PYTHON "$SCRIPT_DIR/walk_forward_test.py" \
            --capital "$CAP" --min-trades "$MIN_T" --max-dd "$MAX_DD" $NO_TELEGRAM
        ;;

    # ── 2: Fee Impact ─────────────────────────────────────────────────────────
    2)
        echo -e "${GREEN}▶ Slippage & Fee Impact${NC}"
        echo "  Zeigt ab welcher ZUSAETZLICHEN Gebuehr der Bot unrentabel wird."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS_DIR/fee_impact.py" --capital "$CAP" $NO_TELEGRAM
        ;;

    # ── 3: Monte Carlo ────────────────────────────────────────────────────────
    3)
        echo -e "${GREEN}▶ Monte Carlo Simulation${NC}"
        echo "  Zufaellige Trade-Reihenfolgen → Konfidenzintervall & Ruin-Risiko."
        echo ""
        read -p "Anzahl Simulationen [Standard: 10000]: " SIMS
        SIMS="${SIMS//[$'\r\n ']/}"
        if ! [[ "$SIMS" =~ ^[0-9]+$ ]]; then SIMS=10000; fi
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS_DIR/monte_carlo.py" --simulations "$SIMS" --capital "$CAP" $NO_TELEGRAM
        ;;

    # ── 4: Bootstrap Signifikanztest ──────────────────────────────────────────
    4)
        echo -e "${GREEN}▶ Bootstrap Signifikanztest${NC}"
        echo "  Prueft ob aktive Pair-Win-Raten statistisch signifikant ueber Zufall (50%) liegen."
        echo ""
        read -p "Min. Samples pro Pair [Standard: 10]: " MIN_S
        MIN_S="${MIN_S//[$'\r\n ']/}"
        if ! [[ "$MIN_S" =~ ^[0-9]+$ ]]; then MIN_S=10; fi
        read -p "Signifikanzniveau Alpha [Standard: 0.05]: " ALPHA
        ALPHA="${ALPHA//[$'\r\n ']/}"
        if ! [[ "$ALPHA" =~ ^0\.[0-9]+$ ]]; then ALPHA=0.05; fi
        $PYTHON "$ANALYSIS_DIR/bootstrap_test.py" --min-samples "$MIN_S" --alpha "$ALPHA" $NO_TELEGRAM
        ;;

    # ── 5: RR-Ratio Optimierung ───────────────────────────────────────────────
    5)
        echo -e "${GREEN}▶ RR-Ratio Optimierung (Walk-Forward, echte Re-Backtests)${NC}"
        echo "  Sweept atr_tp_mult/atr_sl_mult Out-of-Sample. Kann dauern (echte Backtests)."
        echo ""
        CAP=$(ask_capital)
        read -p "Nur die ersten N aktiven Pairs testen [leer=alle]: " NPAIRS
        NPAIRS="${NPAIRS//[$'\r\n ']/}"
        ARGS="--param rr --capital $CAP $NO_TELEGRAM"
        [[ "$NPAIRS" =~ ^[0-9]+$ ]] && ARGS="$ARGS --pairs $NPAIRS"
        $PYTHON "$ANALYSIS_DIR/param_optimizer.py" $ARGS
        ;;

    # ── 6: Entry-Threshold Sweep ──────────────────────────────────────────────
    6)
        echo -e "${GREEN}▶ Entry-Threshold Sweep (Walk-Forward, echte Re-Backtests)${NC}"
        echo "  Sweept min_entropy_drop_pct (0.01-0.35) Out-of-Sample. Kann dauern."
        echo ""
        CAP=$(ask_capital)
        read -p "Nur die ersten N aktiven Pairs testen [leer=alle]: " NPAIRS
        NPAIRS="${NPAIRS//[$'\r\n ']/}"
        ARGS="--param entry_threshold --capital $CAP $NO_TELEGRAM"
        [[ "$NPAIRS" =~ ^[0-9]+$ ]] && ARGS="$ARGS --pairs $NPAIRS"
        $PYTHON "$ANALYSIS_DIR/param_optimizer.py" $ARGS
        ;;

    # ── 7: Parameter Sensitivity ──────────────────────────────────────────────
    7)
        echo -e "${GREEN}▶ Parameter Sensitivity Analysis (Tornado-Diagramm, echte Re-Backtests)${NC}"
        echo "  Breiter Balken = sensitiv = Overfitting-Risiko. Kann dauern."
        echo ""
        CAP=$(ask_capital)
        read -p "Nur die ersten N aktiven Pairs testen [leer=alle]: " NPAIRS
        NPAIRS="${NPAIRS//[$'\r\n ']/}"
        ARGS="--capital $CAP $NO_TELEGRAM"
        [[ "$NPAIRS" =~ ^[0-9]+$ ]] && ARGS="$ARGS --pairs $NPAIRS"
        $PYTHON "$ANALYSIS_DIR/sensitivity.py" $ARGS
        ;;

    # ── 8: Multi-Pair Confirmation ────────────────────────────────────────────
    8)
        echo -e "${GREEN}▶ Multi-Pair Confirmation${NC}"
        echo "  Trades die gleichzeitig auf mehreren Pairs signalisieren → besser?"
        echo ""
        read -p "Gleichzeitigkeit-Fenster in Stunden [Standard: 2]: " WH
        WH="${WH//[$'\r\n ']/}"
        if ! [[ "$WH" =~ ^[0-9]+$ ]]; then WH=2; fi
        $PYTHON "$ANALYSIS_DIR/multitf_analysis.py" --window-hours "$WH" $NO_TELEGRAM
        ;;

    # ── 9: Anti-Korrelation ───────────────────────────────────────────────────
    9)
        echo -e "${GREEN}▶ Anti-Korrelations-Portfolio${NC}"
        echo "  Welche Pairs verlieren/gewinnen selten gleichzeitig?"
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS_DIR/correlation.py" --capital "$CAP" $NO_TELEGRAM
        ;;

    # ── 10: Kelly Position Sizing ─────────────────────────────────────────────
    10)
        echo -e "${GREEN}▶ Kelly Position Sizing${NC}"
        echo "  Mathematisch optimaler Einsatz pro Pair (Half-Kelly)."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS_DIR/kelly_sizing.py" --capital "$CAP" --half-kelly $NO_TELEGRAM
        ;;

    # ── 11: Regime Performance ────────────────────────────────────────────────
    11)
        echo -e "${GREEN}▶ Regime Performance Analysis${NC}"
        echo "  Win-Rate pro Regime (trend/range/chaos) je aktivem Pair."
        echo ""
        read -p "Min. Samples pro Regime [Standard: 10]: " MIN_S
        MIN_S="${MIN_S//[$'\r\n ']/}"
        if ! [[ "$MIN_S" =~ ^[0-9]+$ ]]; then MIN_S=10; fi
        $PYTHON "$ANALYSIS_DIR/regime_analysis.py" --min-samples "$MIN_S" $NO_TELEGRAM
        ;;

    # ── 12: Volatilitaets-Filter ──────────────────────────────────────────────
    12)
        echo -e "${GREEN}▶ Volatilitaets-Filter Optimierung (EXPLORATIV)${NC}"
        echo "  Testet ein hypothetisches, NICHT implementiertes ATR-Ratio-Gate."
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS_DIR/volatility_filter.py" --capital "$CAP" $NO_TELEGRAM
        ;;

    # ── 13: Tageszeit-Analyse ─────────────────────────────────────────────────
    13)
        echo -e "${GREEN}▶ Tageszeit-Analyse${NC}"
        echo "  Performen Signale zu bestimmten Uhrzeiten besser?"
        echo ""
        $PYTHON "$ANALYSIS_DIR/time_analysis.py" $NO_TELEGRAM
        ;;

    # ── 14: Regime-adaptive R:R ───────────────────────────────────────────────
    14)
        echo -e "${GREEN}▶ Regime-adaptive R:R (Walk-Forward, echte Re-Backtests)${NC}"
        echo "  Unterschiedliches R:R pro Timeframe-Gruppe (schnell/mittel/langsam)?"
        echo ""
        CAP=$(ask_capital)
        read -p "Nur die ersten N aktiven Pairs testen [leer=alle]: " NPAIRS
        NPAIRS="${NPAIRS//[$'\r\n ']/}"
        ARGS="--capital $CAP $NO_TELEGRAM"
        [[ "$NPAIRS" =~ ^[0-9]+$ ]] && ARGS="$ARGS --pairs $NPAIRS"
        $PYTHON "$ANALYSIS_DIR/regime_adaptive.py" $ARGS
        ;;

    # ── 15: Drawdown Duration ─────────────────────────────────────────────────
    15)
        echo -e "${GREEN}▶ Drawdown Duration Analysis${NC}"
        echo "  Wie lange dauern Drawdown-Phasen? Wie lange muss man aussitzen?"
        echo ""
        CAP=$(ask_capital)
        $PYTHON "$ANALYSIS_DIR/drawdown_duration.py" --capital "$CAP" $NO_TELEGRAM
        ;;

    # ── 16: Strategie-Vergleich ───────────────────────────────────────────────
    16)
        echo -e "${GREEN}▶ WF Re-Opt vs. Alle Configs — Langzeit-Vergleich${NC}"
        echo "  Vergleicht woechentliche Re-Optimierung gegen alle Configs dauerhaft."
        echo ""
        CAP=$(ask_capital)
        read -p "Max. Drawdown-Limit fuer Portfolio-Auswahl in % [Standard: 30]: " MAX_DD
        MAX_DD="${MAX_DD//[$'\r\n ']/}"
        if ! [[ "$MAX_DD" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then MAX_DD=30; fi
        read -p "Fenster-Groesse in Wochen [Standard: 2]: " LB
        LB="${LB//[$'\r\n ']/}"
        ARGS="--capital $CAP --max-dd $MAX_DD $NO_TELEGRAM"
        [[ "$LB" =~ ^[0-9]+$ ]] && ARGS="$ARGS --lookback $LB"
        $PYTHON "$ANALYSIS_DIR/strategy_comparison.py" $ARGS
        ;;

    *)
        echo -e "${RED}Ungueltige Auswahl: $m${NC}"
        ;;
    esac
}

# ─── Auswahl ausfuehren ────────────────────────────────────────────────────────

if [ "$MODE" == "0" ]; then
    echo -e "${YELLOW}▶ Alle 16 Analysen werden nacheinander ausgefuehrt.${NC}"
    echo "  Hinweis: Analysen die Backtest-Daten benoetigen werden uebersprungen"
    echo "  falls artifacts/results/ leer ist. Modi 5/6/7/14 fuehren echte"
    echo "  Re-Backtests aus und koennen deutlich laenger dauern."
    echo ""
    export MBOT_BATCH=1
    for i in $(seq 1 16); do
        echo ""
        echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
        echo -e "${CYAN}  Analyse $i / 16${NC}"
        echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
        case "$i" in
            1)  $PYTHON "$SCRIPT_DIR/walk_forward_test.py" \
                    --capital 100 --min-trades 2 --max-dd 30 $NO_TELEGRAM 2>/dev/null || true ;;
            2)  $PYTHON "$ANALYSIS_DIR/fee_impact.py" \
                    --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            3)  $PYTHON "$ANALYSIS_DIR/monte_carlo.py" \
                    --simulations 10000 --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            4)  $PYTHON "$ANALYSIS_DIR/bootstrap_test.py" \
                    --min-samples 10 --alpha 0.05 $NO_TELEGRAM 2>/dev/null || true ;;
            5)  $PYTHON "$ANALYSIS_DIR/param_optimizer.py" \
                    --param rr --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            6)  $PYTHON "$ANALYSIS_DIR/param_optimizer.py" \
                    --param entry_threshold --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            7)  $PYTHON "$ANALYSIS_DIR/sensitivity.py" \
                    --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            8)  $PYTHON "$ANALYSIS_DIR/multitf_analysis.py" \
                    --window-hours 2 $NO_TELEGRAM 2>/dev/null || true ;;
            9)  $PYTHON "$ANALYSIS_DIR/correlation.py" \
                    --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            10) $PYTHON "$ANALYSIS_DIR/kelly_sizing.py" \
                    --capital 100 --half-kelly $NO_TELEGRAM 2>/dev/null || true ;;
            11) $PYTHON "$ANALYSIS_DIR/regime_analysis.py" \
                    --min-samples 10 $NO_TELEGRAM 2>/dev/null || true ;;
            12) $PYTHON "$ANALYSIS_DIR/volatility_filter.py" \
                    --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            13) $PYTHON "$ANALYSIS_DIR/time_analysis.py" \
                    $NO_TELEGRAM 2>/dev/null || true ;;
            14) $PYTHON "$ANALYSIS_DIR/regime_adaptive.py" \
                    --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            15) $PYTHON "$ANALYSIS_DIR/drawdown_duration.py" \
                    --capital 100 $NO_TELEGRAM 2>/dev/null || true ;;
            16) $PYTHON "$ANALYSIS_DIR/strategy_comparison.py" \
                    --capital 100 --max-dd 30 $NO_TELEGRAM 2>/dev/null || true ;;
        esac
    done
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Alle Analysen abgeschlossen.${NC}"
    echo -e "${GREEN}  Charts gespeichert in: docs/                  ${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
else
    run_mode "$MODE"
fi

deactivate
