#!/usr/bin/env python3
# run_backtest.py
# Fuehrt Backtests fuer alle optimierten Configs durch.
#
# Ausfuehrung:
#   .venv/bin/python3 run_backtest.py
#   .venv/bin/python3 run_backtest.py --capital 1000 --risk 1.0

import os
import sys
import json
import logging
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.exchange import Exchange
from mbot.analysis.backtester import load_data, run_backtest

logging.basicConfig(level=logging.WARNING)

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')

GREEN  = '\033[0;32m'
YELLOW = '\033[1;33m'
RED    = '\033[0;31m'
CYAN   = '\033[0;36m'
NC     = '\033[0m'


def main():
    parser = argparse.ArgumentParser(description='mbot Backtest-Validierung')
    parser.add_argument('--capital', type=float, default=1000.0,
                        help='Startkapital in USDT')
    args = parser.parse_args()

    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)

    accounts = secrets.get('mbot', [])
    if not accounts:
        print(f'{RED}Kein mbot-Account in secret.json gefunden.{NC}')
        sys.exit(1)

    exchange = Exchange(accounts[0])

    if not os.path.exists(CONFIGS_DIR):
        print(f'{RED}Configs-Verzeichnis nicht gefunden: {CONFIGS_DIR}{NC}')
        sys.exit(1)

    config_files = sorted(
        f for f in os.listdir(CONFIGS_DIR)
        if f.startswith('config_') and f.endswith('_mers.json')
    )
    if not config_files:
        print(f'{RED}Keine Config-Dateien gefunden. Bitte zuerst run_pipeline.sh ausfuehren.{NC}')
        sys.exit(1)

    w = 68
    print(f"\n{'=' * w}")
    print(f"  mbot — Backtest-Validierung")
    print(f"  Kapital: {args.capital:.0f} USDT | Configs: {len(config_files)} | Risiko: aus Config (Optuna-optimiert)")
    print(f"{'=' * w}\n")

    all_results = []
    for fn in config_files:
        path = os.path.join(CONFIGS_DIR, fn)
        try:
            with open(path) as f:
                cfg = json.load(f)
        except Exception as e:
            print(f'  {RED}Fehler beim Laden von {fn}: {e}{NC}')
            continue

        market  = cfg.get('market', {})
        symbol  = market.get('symbol')
        tf      = market.get('timeframe')
        meta    = cfg.get('_meta', {})
        sig_cfg = cfg.get('signal', {})

        # Zeitraum aus Config-Metadaten
        start_date = meta.get('start_date', '2024-01-01')
        end_date   = meta.get('end_date',   '2099-01-01')

        print(f'  Lade: {symbol} ({tf}) | {start_date} → {end_date}...')
        df = load_data(exchange, symbol, tf, start_date, end_date)
        if df is None or df.empty:
            print(f'  {RED}Keine Daten. Ueberspringe.{NC}')
            continue

        # risk_per_trade_pct kommt aus sig_cfg (Optuna-optimiert, in Config gespeichert)
        risk_config = {'risk_per_trade_pct': sig_cfg.get('risk_per_trade_pct', 1.0)}
        result = run_backtest(df, sig_cfg, risk_config,
                              start_capital=args.capital, symbol=symbol)
        result['timeframe'] = tf

        wr   = result.get('win_rate', 0.0)
        pnl  = result.get('total_pnl_pct', 0.0)
        dd   = result.get('max_drawdown', 0.0)
        n    = result.get('total_trades', 0)
        end  = result.get('end_capital', args.capital)

        pnl_c = GREEN if pnl >= 0 else RED
        wr_c  = GREEN if wr >= 55 else YELLOW if wr >= 45 else RED
        dd_c  = GREEN if dd <= 10 else YELLOW if dd <= 25 else RED

        print(
            f'  {symbol:<22} {tf:<5} '
            f'Trades:{n:>4} | '
            f'WR:{wr_c}{wr:>5.1f}%{NC} | '
            f'PnL:{pnl_c}{pnl:>+7.2f}%{NC} | '
            f'MaxDD:{dd_c}{dd:>5.1f}%{NC} | '
            f'End:{end:>8.2f} USDT'
        )
        all_results.append(result)

    if not all_results:
        print(f'\n{RED}Keine Ergebnisse.{NC}')
        return

    if len(all_results) > 1:
        print(f'\n{"=" * w}')
        print(f'  ZUSAMMENFASSUNG — {len(all_results)} Configs')
        print(f'{"=" * w}')
        sorted_results = sorted(all_results, key=lambda r: r.get('total_pnl_pct', 0), reverse=True)
        for r in sorted_results:
            sym  = r.get('symbol', '?')
            tf   = r.get('timeframe', '?')
            pnl  = r.get('total_pnl_pct', 0.0)
            wr   = r.get('win_rate', 0.0)
            dd   = r.get('max_drawdown', 0.0)
            n    = r.get('total_trades', 0)
            pnl_c = GREEN if pnl >= 0 else RED
            wr_c  = GREEN if wr >= 55 else YELLOW if wr >= 45 else RED
            print(
                f'  {sym:<22} {tf:<5} {n:>4} Trades | '
                f'{wr_c}{wr:>5.1f}%{NC} WR | '
                f'{pnl_c}{pnl:>+7.2f}%{NC}'
            )
        print(f'{"=" * w}\n')


if __name__ == '__main__':
    main()
