#!/usr/bin/env python3
"""
run_portfolio_optimizer.py  (mbot)

Lädt alle Config-Dateien, führt frische Backtests durch und wählt
das beste Portfolio via Calmar-Greedy. Schreibt active_strategies in settings.json.

Prinzip (analog dnabot):
  Schritt 1 — Frischer Backtest aller existierenden Configs auf aktuellen Daten
  Schritt 2 — Calmar-Greedy: Start mit bestem Calmar, füge weitere Coins hinzu
               solange MaxDD-Constraint erfüllt (max. 1 Coin pro Position)
  Schritt 3 — Ergebnis in settings.json schreiben (--auto-write)

Aufruf:
  python3 run_portfolio_optimizer.py              # interaktiv
  python3 run_portfolio_optimizer.py --auto-write # automatisch (Scheduler)
"""
import os
import sys
import json
import argparse
from datetime import date, timedelta
from tqdm import tqdm

PROJECT_ROOT  = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

CONFIGS_DIR   = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
SETTINGS_PATH = os.path.join(PROJECT_ROOT, 'settings.json')
SECRET_PATH   = os.path.join(PROJECT_ROOT, 'secret.json')

G  = '\033[0;32m'
Y  = '\033[1;33m'
R  = '\033[0;31m'
B  = '\033[1;37m'
NC = '\033[0m'

LOOKBACK_MAP = {
    '5m': 60,  '15m': 60,  '30m': 180,
    '1h': 365, '2h':  730, '4h':  730,
    '6h': 1095,'1d': 1095, '1w': 1095,
}
MIN_TRADES = 5


def _calmar(pnl: float, max_dd: float) -> float:
    return pnl / max_dd if max_dd > 0 else pnl


def _scan_configs() -> list:
    if not os.path.isdir(CONFIGS_DIR):
        return []
    return sorted([
        os.path.join(CONFIGS_DIR, f)
        for f in os.listdir(CONFIGS_DIR)
        if f.endswith('.json')
    ])


def _parse_config(path: str) -> tuple:
    with open(path) as f:
        cfg = json.load(f)
    market        = cfg.get('market', {})
    symbol        = market.get('symbol', '')
    timeframe     = market.get('timeframe', '')
    signal_config = cfg.get('signal', {})
    return symbol, timeframe, signal_config


def _make_exchange():
    from mbot.utils.exchange import Exchange
    with open(SECRET_PATH) as f:
        secrets = json.load(f)
    accounts = secrets.get('mbot', [])
    if not accounts:
        raise RuntimeError("Keine 'mbot'-Accounts in secret.json")
    return Exchange(accounts[0])


def _run_backtest(symbol: str, timeframe: str,
                  signal_config: dict, risk_config: dict,
                  capital: float, exchange,
                  start: str | None = None, end: str | None = None) -> dict | None:
    from mbot.analysis.backtester import load_data, run_backtest
    if end is None:
        end = date.today().strftime('%Y-%m-%d')
    if start is None:
        lb    = LOOKBACK_MAP.get(timeframe, 365)
        start = (date.today() - timedelta(days=lb)).strftime('%Y-%m-%d')
    data  = load_data(exchange, symbol, timeframe, start, end)
    if data is None or data.empty or len(data) < 50:
        return None
    result = run_backtest(data, signal_config, risk_config, capital, symbol)
    if not result or result.get('total_trades', 0) == 0:
        return None
    return {
        'total_pnl_pct':    result.get('total_pnl_pct', 0.0),
        'max_drawdown_pct': result.get('max_drawdown', 0.0),
        'win_rate':         result.get('win_rate', 0.0),
        'trades_count':     result.get('total_trades', 0),
    }


def _optimize_portfolio(candidates: list, max_dd: float, max_positions: int) -> list:
    eligible = [
        c for c in candidates
        if c['pnl_pct'] > 0 and c['max_dd'] <= max_dd and c['trades'] >= MIN_TRADES
    ]
    if not eligible:
        return []

    coin_best: dict = {}
    for c in eligible:
        coin  = c['symbol'].split('/')[0]
        score = _calmar(c['pnl_pct'], c['max_dd'])
        if coin not in coin_best or score > _calmar(coin_best[coin]['pnl_pct'], coin_best[coin]['max_dd']):
            coin_best[coin] = c

    pool = sorted(coin_best.values(),
                  key=lambda c: _calmar(c['pnl_pct'], c['max_dd']), reverse=True)
    selected, seen = [], set()
    for c in pool:
        if len(selected) >= max_positions:
            break
        coin = c['symbol'].split('/')[0]
        if coin not in seen:
            selected.append(c)
            seen.add(coin)
    return selected


def _write_to_settings(selected: list) -> None:
    with open(SETTINGS_PATH) as f:
        settings = json.load(f)
    existing     = settings.get('live_trading_settings', {}).get('active_strategies', [])
    existing_map = {(s.get('symbol'), s.get('timeframe')): s for s in existing}
    new_strategies = []
    for c in selected:
        base  = existing_map.get((c['symbol'], c['timeframe']), {})
        entry = {**base, 'symbol': c['symbol'], 'timeframe': c['timeframe'], 'active': True}
        new_strategies.append(entry)
    lt = settings.setdefault('live_trading_settings', {})
    lt['active_strategies']          = new_strategies
    lt['use_auto_optimizer_results'] = True
    with open(SETTINGS_PATH, 'w') as f:
        json.dump(settings, f, indent=4)


def main() -> int:
    parser = argparse.ArgumentParser(description='mbot Portfolio-Optimizer')
    parser.add_argument('--capital',    type=float, default=None)
    parser.add_argument('--max-dd',     type=float, default=30.0)
    parser.add_argument('--start-date', type=str,   default=None)
    parser.add_argument('--end-date',   type=str,   default=None)
    parser.add_argument('--auto-write', action='store_true')
    args = parser.parse_args()

    with open(SETTINGS_PATH) as f:
        settings = json.load(f)
    opt           = settings.get('optimization_settings', {})
    risk_config   = settings.get('risk', {})
    capital       = args.capital or float(opt.get('start_capital', 100))
    max_dd        = args.max_dd
    start_date    = args.start_date
    end_date      = args.end_date
    max_positions = int(settings.get('live_trading_settings', {}).get('max_open_positions', 10))

    date_info = f"{start_date or 'auto'} → {end_date or 'heute'}"
    print(f"\n{'─' * 72}")
    print(f"{B}  mbot — Automatische Portfolio-Optimierung{NC}")
    print(f"  Schritt 1: Frischer Backtest aller vorhandenen Configs")
    print(f"  Schritt 2: Calmar-Greedy-Selektion (MaxDD ≤ {max_dd:.0f}%)")
    print(f"  Kapital: {capital:.0f} USDT | Max Positionen: {max_positions} | Zeitraum: {date_info}")
    print(f"{'─' * 72}\n")

    config_files = _scan_configs()
    if not config_files:
        print(f"{R}  Keine Configs in {CONFIGS_DIR}{NC}")
        print(f"  → Zuerst run_pipeline.sh ausführen!\n")
        return 1

    try:
        exchange = _make_exchange()
    except Exception as e:
        print(f"{R}  Exchange-Verbindung fehlgeschlagen: {e}{NC}")
        return 1

    print(f"  {len(config_files)} Config(s) gefunden. Starte Backtests...\n")
    candidates = []
    for path in tqdm(config_files, desc='Backteste Configs'):
        try:
            symbol, timeframe, signal_cfg = _parse_config(path)
            if not symbol or not timeframe:
                continue
            result = _run_backtest(symbol, timeframe, signal_cfg, risk_config, capital, exchange, start=start_date, end=end_date)
            if not result:
                continue
            candidates.append({
                'symbol':    symbol,
                'timeframe': timeframe,
                'pnl_pct':   result.get('total_pnl_pct', 0.0),
                'max_dd':    result.get('max_drawdown_pct', 100.0),
                'win_rate':  result.get('win_rate', 0.0),
                'trades':    result.get('trades_count', 0),
            })
        except Exception as e:
            print(f"  {Y}Fehler bei {os.path.basename(path)}: {e}{NC}")

    if not candidates:
        print(f"\n{R}  Kein Backtest erfolgreich.{NC}")
        return 1

    print(f"\n  {'Symbol':<24} {'TF':<6} {'Trades':>7} {'WR':>7} {'PnL%':>9} {'MaxDD':>8} {'Calmar':>8}")
    print(f"  {'─' * 68}")
    for c in sorted(candidates, key=lambda x: _calmar(x['pnl_pct'], x['max_dd']), reverse=True):
        ok  = c['pnl_pct'] > 0 and c['max_dd'] <= max_dd and c['trades'] >= MIN_TRADES
        col = G if ok else Y
        print(
            f"  {col}{c['symbol']:<24} {c['timeframe']:<6} {c['trades']:>7} "
            f"{c['win_rate']:>6.1f}% {c['pnl_pct']:>+8.1f}% {c['max_dd']:>7.1f}% "
            f"{_calmar(c['pnl_pct'], c['max_dd']):>7.2f}{NC}"
        )

    selected = _optimize_portfolio(candidates, max_dd, max_positions)

    print(f"\n{'=' * 72}")
    if not selected:
        print(f"{R}  Kein Portfolio erfüllt die Bedingungen (MaxDD ≤ {max_dd:.0f}%, PnL > 0).{NC}")
        print(f"  → run_pipeline.sh mit anderen Parametern oder Coins ausführen.\n")
        return 0

    print(f"{B}  Optimales Portfolio — {len(selected)} Strategie(n){NC}\n")
    for c in selected:
        print(
            f"  {G}✓{NC} {c['symbol']:<26} / {c['timeframe']:<6} "
            f" PnL: {c['pnl_pct']:>+6.1f}%  MaxDD: {c['max_dd']:>5.1f}%  "
            f"Calmar: {_calmar(c['pnl_pct'], c['max_dd']):.2f}"
        )
    print(f"{'=' * 72}\n")

    current_set = {
        (s.get('symbol'), s.get('timeframe'))
        for s in settings.get('live_trading_settings', {}).get('active_strategies', [])
        if s.get('active')
    }
    new_set = {(c['symbol'], c['timeframe']) for c in selected}

    if args.auto_write:
        _write_to_settings(selected)
        print(f"{G}✓ settings.json aktualisiert — {len(selected)} Strategie(n) eingetragen.{NC}\n")
    else:
        if current_set == new_set:
            print(f"{Y}  Portfolio unverändert — keine Änderung nötig.{NC}\n")
        else:
            try:
                ans = input("  Optimales Portfolio in settings.json eintragen? (j/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = 'n'
            if ans in ('j', 'ja', 'y', 'yes'):
                _write_to_settings(selected)
                print(f"{G}✓ settings.json aktualisiert.{NC}\n")
            else:
                print(f"{Y}  settings.json NICHT geändert.{NC}\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
