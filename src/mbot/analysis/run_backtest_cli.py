# src/mbot/analysis/run_backtest_cli.py
"""
Wird von run_pipeline.sh aufgerufen.
Nimmt alle Parameter als CLI-Argumente entgegen und fuehrt den Backtest durch.
Speichert Ergebnisse in artifacts/results/backtest_results.json
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.exchange import Exchange
from mbot.analysis.backtester import load_data, run_backtest

RESULTS_DIR  = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
RESULTS_FILE = os.path.join(RESULTS_DIR, 'backtest_results.json')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols',       required=True, nargs='+')
    parser.add_argument('--timeframes',    required=True, nargs='+')
    parser.add_argument('--start_date',    required=True)
    parser.add_argument('--end_date',      required=True)
    parser.add_argument('--start_capital', type=float, default=1000.0)
    args = parser.parse_args()

    # Settings + Secret laden
    with open(os.path.join(PROJECT_ROOT, 'settings.json'), 'r') as f:
        settings = json.load(f)
    with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
        secrets = json.load(f)

    accounts = secrets.get('mbot', [])
    if not accounts:
        logger.critical("Keine 'mbot'-Accounts in secret.json.")
        sys.exit(1)

    risk_config   = settings.get('risk', {})
    signal_config = settings.get('signal', {})

    exchange = Exchange(accounts[0])
    if not exchange.markets:
        logger.critical("Exchange konnte nicht verbunden werden.")
        sys.exit(1)

    os.makedirs(RESULTS_DIR, exist_ok=True)

    all_results = [{
        '_meta': {
            'start_date':    args.start_date,
            'end_date':      args.end_date,
            'start_capital': args.start_capital,
            'created_at':    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        }
    }]

    for symbol_raw in args.symbols:
        symbol = f"{symbol_raw}/USDT:USDT" if '/' not in symbol_raw else symbol_raw
        for timeframe in args.timeframes:
            logger.info(f"=== Backtest: {symbol} ({timeframe}) ===")
            df = load_data(exchange, symbol, timeframe, args.start_date, args.end_date)
            if df.empty:
                logger.warning(f"Keine Daten fuer {symbol} ({timeframe}). Ueberspringe.")
                continue

            result = run_backtest(df, signal_config, risk_config,
                                   start_capital=args.start_capital, symbol=symbol)
            result['timeframe'] = timeframe
            all_results.append(result)

            logger.info(
                f"  Trades: {result['total_trades']} | "
                f"Win-Rate: {result['win_rate']}% | "
                f"PnL: {result['total_pnl_pct']:+.2f}% ({result['total_pnl_usdt']:+.2f} USDT) | "
                f"Max DD: {result['max_drawdown']}%"
            )

    with open(RESULTS_FILE, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info(f"Ergebnisse gespeichert: {RESULTS_FILE}")
    logger.info("Fertig.")


if __name__ == '__main__':
    main()
