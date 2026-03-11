# src/mbot/analysis/show_results.py
"""
mbot Ergebnis-Anzeige

Laedt das letzte Backtest-Ergebnis aus artifacts/results/backtest_results.json
und zeigt es tabellarisch an.

Aufruf: python3 src/mbot/analysis/show_results.py [--detail]
"""

import os
import sys
import json
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
RESULTS_FILE = os.path.join(PROJECT_ROOT, 'artifacts', 'results', 'backtest_results.json')

GREEN  = '\033[0;32m'
BLUE   = '\033[0;34m'
YELLOW = '\033[1;33m'
RED    = '\033[0;31m'
CYAN   = '\033[0;36m'
BOLD   = '\033[1m'
NC     = '\033[0m'


def color_val(val: float, positive_good: bool = True) -> str:
    if positive_good:
        c = GREEN if val >= 0 else RED
    else:
        c = RED if val >= 10 else YELLOW if val >= 5 else GREEN
    return f"{c}{val:+.2f}{NC}"


def print_summary(results: list):
    if not results:
        print(f"{RED}Keine Ergebnisse gefunden.{NC}")
        return

    print(f"\n{BLUE}{BOLD}{'='*70}{NC}")
    print(f"{BLUE}{BOLD}  mbot Backtest-Ergebnisse{NC}")
    meta = results[0].get('_meta', {})
    if meta:
        print(f"  Zeitraum: {meta.get('start_date', '?')} → {meta.get('end_date', '?')}")
        print(f"  Startkapital: {meta.get('start_capital', '?')} USDT  |  Erstellt: {meta.get('created_at', '?')}")
    print(f"{BLUE}{BOLD}{'='*70}{NC}\n")

    header = f"{'Symbol':<22} {'TF':<6} {'Trades':>6} {'Win%':>6} {'PnL%':>8} {'PnL USDT':>10} {'MaxDD%':>8} {'Endkapital':>12}"
    print(f"{BOLD}{header}{NC}")
    print("-" * 80)

    for r in results:
        if '_meta' in r:
            continue
        symbol    = r.get('symbol', '?')
        tf        = r.get('timeframe', '?')
        trades    = r.get('total_trades', 0)
        win_rate  = r.get('win_rate', 0.0)
        pnl_pct   = r.get('total_pnl_pct', 0.0)
        pnl_usdt  = r.get('total_pnl_usdt', 0.0)
        max_dd    = r.get('max_drawdown', 0.0)
        end_cap   = r.get('end_capital', 0.0)

        pnl_c  = GREEN if pnl_pct >= 0 else RED
        wr_c   = GREEN if win_rate >= 55 else YELLOW if win_rate >= 45 else RED
        dd_c   = GREEN if max_dd <= 10 else YELLOW if max_dd <= 25 else RED

        print(
            f"{symbol:<22} {tf:<6} "
            f"{trades:>6} "
            f"{wr_c}{win_rate:>5.1f}%{NC} "
            f"{pnl_c}{pnl_pct:>+7.2f}%{NC} "
            f"{pnl_c}{pnl_usdt:>+9.2f}{NC} "
            f"{dd_c}{max_dd:>7.1f}%{NC} "
            f"{end_cap:>11.2f}"
        )

    print()


def print_detail(results: list):
    """Zeigt Trade-Liste fuer jede Strategie."""
    for r in results:
        if '_meta' in r:
            continue
        symbol = r.get('symbol', '?')
        tf     = r.get('timeframe', '?')
        trades = r.get('trades', [])
        if not trades:
            continue

        print(f"\n{CYAN}{BOLD}--- Trades: {symbol} ({tf}) ---{NC}")
        print(f"{'Nr':>4}  {'Zeit':<22} {'Seite':<6} {'Entry':>10} {'Exit':>10} {'Erg.':<6} {'PnL%':>7}")
        print("-" * 75)
        for i, t in enumerate(trades, 1):
            entry_time = t.get('entry_time', '')[:19].replace('T', ' ')
            side_str   = t.get('side', '?').upper()
            entry_p    = t.get('entry_price', 0.0)
            exit_p     = t.get('exit_price', 0.0)
            result     = t.get('result', '?')
            pnl_pct    = t.get('pnl_pct', 0.0)
            res_c      = GREEN if result == 'win' else RED
            pnl_c      = GREEN if pnl_pct >= 0 else RED
            print(
                f"{i:>4}  {entry_time:<22} {side_str:<6} "
                f"{entry_p:>10.4f} {exit_p:>10.4f} "
                f"{res_c}{result:<6}{NC} "
                f"{pnl_c}{pnl_pct:>+6.2f}%{NC}"
            )
        print()


def main():
    parser = argparse.ArgumentParser(description='mbot Backtest-Ergebnisse anzeigen')
    parser.add_argument('--detail', action='store_true', help='Zeige einzelne Trades')
    args = parser.parse_args()

    if not os.path.exists(RESULTS_FILE):
        print(f"{RED}Keine Ergebnisse gefunden: {RESULTS_FILE}{NC}")
        print(f"{YELLOW}Bitte zuerst run_pipeline.sh ausfuehren.{NC}")
        sys.exit(1)

    with open(RESULTS_FILE, 'r') as f:
        results = json.load(f)

    print_summary(results)
    if args.detail:
        print_detail(results)


if __name__ == '__main__':
    main()
