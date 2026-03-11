# src/mbot/analysis/show_results.py
"""
mbot Ergebnis-Anzeige

Modi:
  1) Uebersicht       — liest _meta aus Config-Dateien (schnell, kein API-Zugriff)
  2) Frischer Backtest — laedt aktuelle Daten von Bitget und backtestet neu
  3) Detail-Ansicht   — wie Modus 2 + komplette Trade-Liste

Aufruf: python3 src/mbot/analysis/show_results.py --mode 1|2|3
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')

GREEN  = '\033[0;32m'
BLUE   = '\033[0;34m'
YELLOW = '\033[1;33m'
RED    = '\033[0;31m'
CYAN   = '\033[0;36m'
BOLD   = '\033[1m'
NC     = '\033[0m'

logging.basicConfig(level=logging.WARNING)


def load_configs() -> list:
    if not os.path.exists(CONFIGS_DIR):
        return []
    files = sorted(f for f in os.listdir(CONFIGS_DIR)
                   if f.startswith('config_') and f.endswith('_momentum.json'))
    configs = []
    for fn in files:
        path = os.path.join(CONFIGS_DIR, fn)
        try:
            with open(path) as f:
                cfg = json.load(f)
            cfg['_filename'] = fn
            configs.append(cfg)
        except Exception:
            pass
    return configs


def print_header():
    print(f"\n{BLUE}{BOLD}{'='*70}{NC}")
    print(f"{BLUE}{BOLD}  mbot Signal-Optimizer Ergebnisse{NC}")
    print(f"{BLUE}{BOLD}{'='*70}{NC}\n")


def print_summary_from_meta(configs: list):
    """Modus 1: schnelle Anzeige aus gespeicherten _meta-Werten."""
    print_header()
    if not configs:
        print(f"{RED}Keine Config-Dateien gefunden in:{NC}")
        print(f"  {CONFIGS_DIR}")
        print(f"{YELLOW}Bitte zuerst run_pipeline.sh ausfuehren.{NC}")
        return

    header = (f"{'Symbol':<22} {'TF':<6} {'Trades':>6} {'Win%':>6} "
              f"{'PnL%':>8} {'MaxDD%':>8} {'Startkapital':>13} {'Endkapital':>11}")
    print(f"{BOLD}{header}{NC}")
    print("-" * 85)

    for cfg in configs:
        meta      = cfg.get('_meta', {})
        market    = cfg.get('market', {})
        symbol    = market.get('symbol', '?')
        tf        = market.get('timeframe', '?')
        trades    = meta.get('total_trades', 0)
        win_rate  = meta.get('win_rate', 0.0)
        pnl_pct   = meta.get('pnl_pct', 0.0)
        max_dd    = meta.get('max_drawdown', 0.0)
        start_cap = meta.get('start_capital', 0.0)
        end_cap   = meta.get('end_capital', 0.0)
        opt_at    = meta.get('optimized_at', '')[:10]

        pnl_c = GREEN if pnl_pct >= 0 else RED
        wr_c  = GREEN if win_rate >= 55 else YELLOW if win_rate >= 45 else RED
        dd_c  = GREEN if max_dd <= 10 else YELLOW if max_dd <= 25 else RED

        print(
            f"{symbol:<22} {tf:<6} "
            f"{trades:>6} "
            f"{wr_c}{win_rate:>5.1f}%{NC} "
            f"{pnl_c}{pnl_pct:>+7.2f}%{NC} "
            f"{dd_c}{max_dd:>7.1f}%{NC} "
            f"{start_cap:>12.2f} "
            f"{end_cap:>11.2f}"
        )
        print(f"{'':>22}   {CYAN}Optimiert am: {opt_at} | Modus: {meta.get('mode','?')} "
              f"| Zeitraum: {meta.get('start_date','?')} → {meta.get('end_date','?')}{NC}")

    print()
    print(f"{YELLOW}Tipp: './show_results.sh' Modus 2 fuer frischen Backtest mit aktuellen Daten.{NC}\n")


def print_fresh_backtest(configs: list, show_trades: bool = False):
    """Modus 2/3: laedt frische Daten und backtestet die gespeicherten Params."""
    from mbot.utils.exchange import Exchange
    from mbot.analysis.backtester import load_data, run_backtest
    from datetime import timedelta

    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
            secrets = json.load(f)
        with open(os.path.join(PROJECT_ROOT, 'settings.json')) as f:
            settings = json.load(f)
    except FileNotFoundError as e:
        print(f"{RED}Fehler: {e}{NC}")
        return

    accounts = secrets.get('mbot', [])
    if not accounts:
        print(f"{RED}Keine 'mbot'-Accounts in secret.json.{NC}")
        return

    exchange    = Exchange(accounts[0])
    risk_config = settings.get('risk', {})

    if not configs:
        print(f"{RED}Keine Config-Dateien gefunden. Bitte zuerst run_pipeline.sh ausfuehren.{NC}")
        return

    today      = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    all_results = []

    print_header()
    print(f"{YELLOW}Lade aktuelle Daten von Bitget und backteste...{NC}\n")

    for cfg in configs:
        market    = cfg.get('market', {})
        symbol    = market.get('symbol', '?')
        tf        = market.get('timeframe', '?')
        sig_cfg   = cfg.get('signal', {})
        meta      = cfg.get('_meta', {})
        start_cap = meta.get('start_capital', 1000.0)
        start_date = meta.get('start_date', '2024-01-01')

        print(f"  Lade: {symbol} ({tf}) ab {start_date}...")
        df = load_data(exchange, symbol, tf, start_date, today)
        if df is None or df.empty:
            print(f"  {RED}Keine Daten. Ueberspringe.{NC}")
            continue

        result = run_backtest(df, sig_cfg, risk_config, start_capital=start_cap, symbol=symbol)
        result['timeframe']  = tf
        result['start_date'] = start_date
        result['end_date']   = today
        all_results.append((cfg, result))

    if not all_results:
        print(f"{RED}Keine Ergebnisse.{NC}")
        return

    header = (f"{'Symbol':<22} {'TF':<6} {'Trades':>6} {'Win%':>6} "
              f"{'PnL%':>8} {'MaxDD%':>8} {'Endkapital':>11}")
    print(f"\n{BOLD}{header}{NC}")
    print("-" * 75)

    for cfg, r in all_results:
        symbol   = r.get('symbol', '?')
        tf       = r.get('timeframe', '?')
        trades   = r.get('total_trades', 0)
        win_rate = r.get('win_rate', 0.0)
        pnl_pct  = r.get('total_pnl_pct', 0.0)
        max_dd   = r.get('max_drawdown', 0.0)
        end_cap  = r.get('end_capital', 0.0)

        pnl_c = GREEN if pnl_pct >= 0 else RED
        wr_c  = GREEN if win_rate >= 55 else YELLOW if win_rate >= 45 else RED
        dd_c  = GREEN if max_dd <= 10 else YELLOW if max_dd <= 25 else RED

        print(
            f"{symbol:<22} {tf:<6} "
            f"{trades:>6} "
            f"{wr_c}{win_rate:>5.1f}%{NC} "
            f"{pnl_c}{pnl_pct:>+7.2f}%{NC} "
            f"{dd_c}{max_dd:>7.1f}%{NC} "
            f"{end_cap:>11.2f}"
        )

        if show_trades and r.get('trades'):
            print(f"\n  {CYAN}--- Trades: {symbol} ({tf}) ---{NC}")
            print(f"  {'Nr':>4}  {'Zeit':<20} {'Seite':<6} {'Entry':>10} {'Exit':>10} {'Erg.':<6} {'PnL%':>7}")
            print("  " + "-" * 70)
            for i, t in enumerate(r['trades'], 1):
                entry_time = t.get('entry_time', '')[:19].replace('T', ' ')
                side_str   = t.get('side', '?').upper()
                entry_p    = t.get('entry_price', 0.0)
                exit_p     = t.get('exit_price', 0.0)
                result_str = t.get('result', '?')
                pnl_t      = t.get('pnl_pct', 0.0)
                res_c      = GREEN if result_str == 'win' else RED
                pnl_tc     = GREEN if pnl_t >= 0 else RED
                print(
                    f"  {i:>4}  {entry_time:<20} {side_str:<6} "
                    f"{entry_p:>10.4f} {exit_p:>10.4f} "
                    f"{res_c}{result_str:<6}{NC} {pnl_tc}{pnl_t:>+6.2f}%{NC}"
                )
            print()

    print()


def main():
    parser = argparse.ArgumentParser(description='mbot Ergebnisse anzeigen')
    parser.add_argument('--mode', type=str, default='1',
                        choices=['1', '2', '3'],
                        help='1=Meta-Uebersicht 2=Frischer Backtest 3=Detail+Trades')
    args = parser.parse_args()

    configs = load_configs()

    if args.mode == '1':
        print_summary_from_meta(configs)
    elif args.mode == '2':
        print_fresh_backtest(configs, show_trades=False)
    elif args.mode == '3':
        print_fresh_backtest(configs, show_trades=True)


if __name__ == '__main__':
    main()
