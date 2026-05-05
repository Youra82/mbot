#!/usr/bin/env python3
"""
run_portfolio_optimizer.py  (mbot)

Lädt alle Configs, führt Backtests durch und wählt das beste Portfolio
per Portfolio-Simulation + Calmar-Greedy. Schreibt active_strategies in settings.json.

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

B  = '\033[1;37m'
G  = '\033[0;32m'
Y  = '\033[1;33m'
R  = '\033[0;31m'
NC = '\033[0m'

DEFAULT_LOOKBACK_DAYS = 1095  # ~3 Jahre als Standard


def _scan_configs() -> list:
    if not os.path.isdir(CONFIGS_DIR):
        return []
    return sorted([
        os.path.join(CONFIGS_DIR, f)
        for f in os.listdir(CONFIGS_DIR)
        if f.endswith('.json')
    ])


def _make_exchange():
    from mbot.utils.exchange import Exchange
    with open(SECRET_PATH) as f:
        secrets = json.load(f)
    accounts = secrets.get('mbot', [])
    if not accounts:
        raise RuntimeError("Keine 'mbot'-Accounts in secret.json")
    return Exchange(accounts[0])


def _build_results_dict(config_files: list, risk_config: dict, capital: float,
                         exchange, start_date: str, end_date: str) -> dict:
    from mbot.analysis.backtester import load_data, run_backtest
    results_dict = {}
    for path in tqdm(config_files, desc='Lade Configs & Backtests'):
        fname = os.path.basename(path)
        try:
            with open(path) as f:
                cfg = json.load(f)
            market        = cfg.get('market', {})
            symbol        = market.get('symbol', '')
            timeframe     = market.get('timeframe', '')
            signal_config = cfg.get('signal', {})
            if not symbol or not timeframe:
                continue
            data = load_data(exchange, symbol, timeframe, start_date, end_date)
            if data is None or data.empty or len(data) < 50:
                print(f"  {Y}Uebersprungen (keine Daten): {fname}{NC}")
                continue
            result = run_backtest(data, signal_config, risk_config, capital, symbol)
            if not result or result.get('total_trades', 0) == 0:
                continue
            results_dict[fname] = {**result, 'symbol': symbol, 'timeframe': timeframe}
        except Exception as e:
            print(f"  {Y}Fehler bei {fname}: {e}{NC}")
    return results_dict


def _simulate_current_portfolio(settings: dict, results_dict: dict,
                                 start_capital: float) -> dict | None:
    """Simuliert das aktuell aktive Portfolio mit den vorhandenen Backtest-Daten."""
    from mbot.analysis.portfolio_simulator import run_portfolio_simulation
    current = [
        s for s in settings.get('live_trading_settings', {}).get('active_strategies', [])
        if s.get('active')
    ]
    if not current:
        return None
    subset = {}
    for s in current:
        sym, tf = s.get('symbol', ''), s.get('timeframe', '')
        for fname, rd in results_dict.items():
            if rd.get('symbol') == sym and rd.get('timeframe') == tf:
                subset[fname] = rd
                break
    if not subset:
        return None
    return run_portfolio_simulation(subset, start_capital)


def _write_to_settings(selected_files: list, results_dict: dict) -> None:
    with open(SETTINGS_PATH) as f:
        settings = json.load(f)
    existing     = settings.get('live_trading_settings', {}).get('active_strategies', [])
    existing_map = {(s.get('symbol'), s.get('timeframe')): s for s in existing}
    new_strategies = []
    for fname in selected_files:
        rd        = results_dict.get(fname, {})
        symbol    = rd.get('symbol', '')
        timeframe = rd.get('timeframe', '')
        if not symbol or not timeframe:
            continue
        base  = existing_map.get((symbol, timeframe), {})
        entry = {**base, 'symbol': symbol, 'timeframe': timeframe, 'active': True}
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
    end_date      = args.end_date   or date.today().strftime('%Y-%m-%d')
    start_date    = args.start_date or (
        date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    ).strftime('%Y-%m-%d')
    max_positions = int(settings.get('live_trading_settings', {}).get('max_open_positions', 10))

    print(f"\n{'─'*72}")
    print(f"{B}  mbot — Automatische Portfolio-Optimierung{NC}")
    print(f"  Portfolio-Simulation + Calmar-Greedy (MaxDD ≤ {max_dd:.0f}%)")
    print(f"  Kapital: {capital:.0f} USDT | Positionen: max {max_positions} | "
          f"Zeitraum: {start_date} → {end_date}")
    print(f"{'─'*72}\n")

    config_files = _scan_configs()
    if not config_files:
        print(f"{R}  Keine Configs in {CONFIGS_DIR}{NC}")
        print(f"  → Zuerst run_pipeline.sh ausfuehren!\n")
        return 1

    try:
        exchange = _make_exchange()
    except Exception as e:
        print(f"{R}  Exchange-Verbindung fehlgeschlagen: {e}{NC}")
        return 1

    print(f"  {len(config_files)} Config(s) gefunden.\n")
    results_dict = _build_results_dict(config_files, risk_config, capital, exchange,
                                        start_date, end_date)
    if not results_dict:
        print(f"{R}  Keine Backtests erfolgreich.{NC}")
        return 1

    from mbot.analysis.portfolio_simulator import find_best_portfolio
    portfolio = find_best_portfolio(results_dict, capital, max_dd, verbose=True)

    if not portfolio or not portfolio.get('selected'):
        print(f"{R}  Kein Portfolio erfuellt die Bedingungen (MaxDD ≤ {max_dd:.0f}%).{NC}\n")
        return 0

    selected_files = portfolio['selected'][:max_positions]
    final          = portfolio.get('portfolio') or {}

    print(f"\n{'='*72}")
    print(f"{B}  Optimales Portfolio — {len(selected_files)} Strategie(n){NC}\n")
    for fname in selected_files:
        rd = results_dict.get(fname, {})
        print(f"  {G}✓{NC} {rd.get('symbol', fname):<26} / {rd.get('timeframe', ''):<6}")
    if final:
        pnl = final.get('total_pnl_pct', 0)
        print(f"\n  Endkapital: {final.get('end_capital', 0):.2f} USDT  "
              f"| PnL: {pnl:+.1f}%  "
              f"| MaxDD: {final.get('max_drawdown', 0):.2f}%")
    print(f"{'='*72}\n")

    current_set = {
        (s.get('symbol'), s.get('timeframe'))
        for s in settings.get('live_trading_settings', {}).get('active_strategies', [])
        if s.get('active')
    }
    new_set = {
        (results_dict.get(f, {}).get('symbol'), results_dict.get(f, {}).get('timeframe'))
        for f in selected_files
    }

    cur_result  = _simulate_current_portfolio(settings, results_dict, capital)
    cur_cap     = cur_result.get('end_capital', 0) if cur_result else 0
    new_cap     = final.get('end_capital', 0)
    if cur_result:
        print(f"  Aktuelles Portfolio: {cur_cap:.2f} USDT  "
              f"| PnL: {cur_result.get('total_pnl_pct', 0):+.1f}%  "
              f"| MaxDD: {cur_result.get('max_drawdown', 0):.2f}%")
        print(f"  Neues Portfolio:     {new_cap:.2f} USDT  "
              f"| PnL: {final.get('total_pnl_pct', 0):+.1f}%  "
              f"| MaxDD: {final.get('max_drawdown', 0):.2f}%\n")

    if args.auto_write:
        if cur_result and new_cap <= cur_cap:
            print(f"{Y}  Neues Portfolio ({new_cap:.2f} USDT) nicht besser als aktuelles "
                  f"({cur_cap:.2f} USDT) — keine Aenderung.{NC}\n")
        else:
            _write_to_settings(selected_files, results_dict)
            print(f"{G}✓ settings.json aktualisiert — {len(selected_files)} Strategie(n).{NC}\n")
    else:
        if current_set == new_set:
            print(f"{Y}  Portfolio unveraendert — keine Aenderung noetig.{NC}\n")
        else:
            try:
                ans = input("  Optimales Portfolio in settings.json eintragen? (j/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = 'n'
            if ans in ('j', 'ja', 'y', 'yes'):
                _write_to_settings(selected_files, results_dict)
                print(f"{G}✓ settings.json aktualisiert.{NC}\n")
            else:
                print(f"{Y}  settings.json NICHT geaendert.{NC}\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
