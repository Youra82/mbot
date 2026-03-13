# src/mbot/analysis/show_results.py
"""
mbot Ergebnis-Analyse

Modi:
  1) Einzel-Analyse            — jede Strategie isoliert backtesten
  2) Manuelle Portfolio-Sim    — user waehlt Strategien, gemeinsamer Kapitalpool
  3) Auto Portfolio-Optimierung — Bot findet bestes Team
  4) Interaktive Charts         — Candlestick + Trade-Marker als HTML
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

CONFIGS_DIR  = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
RESULTS_DIR  = os.path.join(PROJECT_ROOT, 'artifacts', 'results')

GREEN  = '\033[0;32m'
BLUE   = '\033[0;34m'
YELLOW = '\033[1;33m'
RED    = '\033[0;31m'
CYAN   = '\033[0;36m'
BOLD   = '\033[1m'
NC     = '\033[0m'

logging.basicConfig(level=logging.WARNING)


# ============================================================
# Hilfsfunktionen
# ============================================================

def load_all_configs() -> list:
    if not os.path.exists(CONFIGS_DIR):
        return []
    files = sorted(f for f in os.listdir(CONFIGS_DIR)
                   if f.startswith('config_') and f.endswith('_mers.json'))
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


def ask_backtest_config(default_start='2024-01-01'):
    """Interaktive Abfrage von Zeitraum und Kapital."""
    print('\n--- Bitte Konfiguration fuer den Backtest festlegen ---')
    raw = input(f'Startdatum (JJJJ-MM-TT) [Standard: {default_start}]: ').strip()
    start_date = raw if raw else default_start

    raw = input('Enddatum (JJJJ-MM-TT) [Standard: Heute]: ').strip()
    end_date = raw if raw else datetime.now(timezone.utc).strftime('%Y-%m-%d')

    raw = input('Startkapital in USDT eingeben [Standard: 1000]: ').strip()
    try:
        start_capital = float(raw) if raw else 1000.0
    except ValueError:
        start_capital = 1000.0

    print('-' * 50)
    return start_date, end_date, start_capital


def get_exchange_and_risk():
    """Laedt Exchange + Risiko-Config."""
    from mbot.utils.exchange import Exchange
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    with open(os.path.join(PROJECT_ROOT, 'settings.json')) as f:
        settings = json.load(f)
    accounts = secrets.get('mbot', [])
    if not accounts:
        print(f'{RED}Keine mbot-Accounts in secret.json.{NC}')
        sys.exit(1)
    exchange    = Exchange(accounts[0])
    risk_config = settings.get('risk', {})
    telegram    = secrets.get('telegram', {})
    return exchange, risk_config, telegram


def run_all_backtests(configs, exchange, risk_config, start_date, end_date, start_capital):
    """Fuehrt Backtest fuer alle Configs durch. Gibt dict {filename: result} zurueck."""
    from mbot.analysis.backtester import load_data, run_backtest
    results = {}
    for cfg in configs:
        fn        = cfg.get('_filename', '?')
        market    = cfg.get('market', {})
        symbol    = market.get('symbol', '?')
        tf        = market.get('timeframe', '?')
        sig_cfg   = cfg.get('signal', {})

        print(f'  Lade: {symbol} ({tf})...')
        df = load_data(exchange, symbol, tf, start_date, end_date)
        if df is None or df.empty:
            print(f'  {RED}Keine Daten. Ueberspringe.{NC}')
            continue

        result = run_backtest(df, sig_cfg, risk_config, start_capital=start_capital,
                               symbol=symbol)
        result['timeframe']  = tf
        result['start_date'] = start_date
        result['end_date']   = end_date
        results[fn] = result
    return results


def print_single_result(result, show_trades=False):
    """Gibt Ergebnis einer einzelnen Strategie aus."""
    symbol   = result.get('symbol', '?')
    tf       = result.get('timeframe', '?')
    trades   = result.get('total_trades', 0)
    win_rate = result.get('win_rate', 0.0)
    pnl_pct  = result.get('total_pnl_pct', 0.0)
    max_dd   = result.get('max_drawdown', 0.0)
    end_cap  = result.get('end_capital', 0.0)
    start_cap= result.get('start_capital', 0.0)

    pnl_c = GREEN if pnl_pct >= 0 else RED
    wr_c  = GREEN if win_rate >= 55 else YELLOW if win_rate >= 45 else RED
    dd_c  = GREEN if max_dd <= 10 else YELLOW if max_dd <= 25 else RED

    print(
        f'  {BOLD}{symbol:<22}{NC} {tf:<6} '
        f'Trades: {trades:>4} | '
        f'WR: {wr_c}{win_rate:>5.1f}%{NC} | '
        f'PnL: {pnl_c}{pnl_pct:>+7.2f}%{NC} ({pnl_c}{end_cap - start_cap:>+8.2f} USDT{NC}) | '
        f'MaxDD: {dd_c}{max_dd:>6.1f}%{NC} | '
        f'End: {end_cap:>8.2f} USDT'
    )

    if show_trades:
        for t in result.get('trades', []):
            side   = t.get('side', '?').upper()
            entry  = t.get('entry_time', '')[:16].replace('T', ' ')
            ep     = t.get('entry_price', 0.0)
            xp     = t.get('exit_price', 0.0)
            res    = t.get('result', '?')
            pnl    = t.get('pnl_pct', 0.0)
            rc     = GREEN if res == 'win' else RED
            print(f'    {entry} | {side:<5} | {ep:>10.4f} → {xp:>10.4f} | '
                  f'{rc}{res:<4}{NC} {pnl:>+6.2f}%')


# ============================================================
# Modus 1: Einzel-Analyse
# ============================================================

def mode_single(target_max_dd):
    print('\n--- mbot Ergebnis-Analyse (Einzel-Modus) ---\n')

    configs = load_all_configs()
    if not configs:
        print(f'{RED}Keine Config-Dateien gefunden in {CONFIGS_DIR}{NC}')
        print(f'{YELLOW}Bitte zuerst run_pipeline.sh ausfuehren.{NC}')
        return

    start_date, end_date, start_capital = ask_backtest_config()
    exchange, risk_config, _ = get_exchange_and_risk()

    print(f'\nZeitraum: {start_date} bis {end_date} | Startkapital: {start_capital} USDT\n')
    results = run_all_backtests(configs, exchange, risk_config, start_date, end_date, start_capital)

    if not results:
        print(f'{RED}Keine Ergebnisse.{NC}')
        return

    print(f'\n{BOLD}{"Symbol":<22} {"TF":<6} {"Trades":>6} {"Win%":>6} {"PnL%":>8} {"USDT":>9} {"MaxDD%":>8} {"Endkap.":>10}{NC}')
    print('-' * 82)

    for fn, r in results.items():
        symbol   = r.get('symbol', '?')
        tf       = r.get('timeframe', '?')
        trades   = r.get('total_trades', 0)
        wr       = r.get('win_rate', 0.0)
        pnl_pct  = r.get('total_pnl_pct', 0.0)
        pnl_usdt = r.get('total_pnl_usdt', 0.0)
        max_dd   = r.get('max_drawdown', 0.0)
        end_cap  = r.get('end_capital', 0.0)

        pnl_c = GREEN if pnl_pct >= 0 else RED
        wr_c  = GREEN if wr >= 55 else YELLOW if wr >= 45 else RED
        dd_c  = GREEN if max_dd <= 10 else YELLOW if max_dd <= 25 else RED

        print(
            f'{symbol:<22} {tf:<6} {trades:>6} '
            f'{wr_c}{wr:>5.1f}%{NC} '
            f'{pnl_c}{pnl_pct:>+7.2f}%{NC} '
            f'{pnl_c}{pnl_usdt:>+8.2f}{NC} '
            f'{dd_c}{max_dd:>7.1f}%{NC} '
            f'{end_cap:>10.2f}'
        )
    print()


# ============================================================
# Modus 2: Manuelle Portfolio-Simulation
# ============================================================

def mode_manual_portfolio(target_max_dd):
    print('\n--- mbot Manuelle Portfolio-Simulation ---\n')

    configs = load_all_configs()
    if not configs:
        print(f'{RED}Keine Configs gefunden. Bitte run_pipeline.sh ausfuehren.{NC}')
        return

    # Verfuegbare Configs auflisten
    print(f'{BOLD}{"="*60}{NC}')
    print('Verfuegbare Strategien:')
    print(f'{BOLD}{"="*60}{NC}')
    for idx, cfg in enumerate(configs, 1):
        market  = cfg.get('market', {})
        symbol  = market.get('symbol', '?')
        tf      = market.get('timeframe', '?')
        pnl     = cfg.get('_meta', {}).get('pnl_pct')
        pnl_str = f'  [{pnl:+.1f}%]' if pnl is not None else ''
        clean   = cfg['_filename'].replace('config_', '').replace('_mers.json', '')
        print(f'{idx:>3}) {clean}{CYAN}{pnl_str}{NC}')
    print(f'{BOLD}{"="*60}{NC}')

    raw = input('\nWaehle Strategien (z.B. "1,3" oder "alle"): ').strip().lower()
    if raw == 'alle' or raw == 'all':
        selected = configs
    else:
        indices = []
        for part in raw.replace(',', ' ').split():
            try:
                indices.append(int(part) - 1)
            except ValueError:
                pass
        selected = [configs[i] for i in indices if 0 <= i < len(configs)]

    if not selected:
        print(f'{RED}Keine gueltigen Strategien ausgewaehlt.{NC}')
        return

    start_date, end_date, start_capital = ask_backtest_config()
    exchange, risk_config, _ = get_exchange_and_risk()

    print(f'\nLade Daten und fuehre Backtests durch...')
    results = run_all_backtests(selected, exchange, risk_config, start_date, end_date, start_capital)

    if not results:
        print(f'{RED}Keine Ergebnisse.{NC}')
        return

    from mbot.analysis.portfolio_simulator import run_portfolio_simulation
    portfolio = run_portfolio_simulation(results, start_capital)
    _print_portfolio_result(portfolio, 'Manuelles Portfolio')


# ============================================================
# Modus 3: Automatische Portfolio-Optimierung
# ============================================================

def mode_auto_portfolio(target_max_dd):
    print(f'\n--- mbot Automatische Portfolio-Optimierung ---')
    print(f'Ziel: Maximaler Profit bei maximal {target_max_dd:.2f}% Drawdown.\n')

    configs = load_all_configs()
    if not configs:
        print(f'{RED}Keine optimierten Strategien (Configs) gefunden.{NC}')
        return

    start_date, end_date, start_capital = ask_backtest_config()
    exchange, risk_config, _ = get_exchange_and_risk()

    print(f'\nLade Daten fuer alle {len(configs)} Strategien...')
    results = run_all_backtests(configs, exchange, risk_config, start_date, end_date, start_capital)

    if not results:
        print(f'{RED}Keine Ergebnisse.{NC}')
        return

    # Strategien mit negativem PnL vor der Optimierung ausfiltern
    positive_results = {k: v for k, v in results.items() if v.get('total_pnl_pct', 0.0) > 0}
    if not positive_results:
        print(f'{RED}Keine Strategie mit positivem PnL gefunden.{NC}')
        return
    excluded = len(results) - len(positive_results)
    if excluded:
        print(f'  {excluded} Strategie(n) mit negativem PnL ausgeschlossen.')

    from mbot.analysis.portfolio_simulator import find_best_portfolio
    best = find_best_portfolio(positive_results, start_capital, target_max_dd)

    if not best:
        print(f'{RED}Kein Portfolio gefunden das den Drawdown-Constraint ({target_max_dd}%) erfuellt.{NC}')
        return

    _print_portfolio_result(best['portfolio'], 'Optimales Portfolio')

    print(f'\n{BOLD}Ausgewaehlte Strategien:{NC}')
    for fn in best['selected']:
        r = results[fn]
        market = r.get('symbol', '?')
        tf     = r.get('timeframe', '?')
        pnl    = r.get('total_pnl_pct', 0.0)
        print(f'  - {market} ({tf})  PnL: {pnl:+.2f}%')

    # Fuer settings.json Update: optimal_portfolio.json speichern
    os.makedirs(RESULTS_DIR, exist_ok=True)
    portfolio_data = {
        'selected_strategies': [
            {'symbol': results[fn].get('symbol'), 'timeframe': results[fn].get('timeframe')}
            for fn in best['selected']
        ],
        'pnl_pct':     best['portfolio']['total_pnl_pct'],
        'max_drawdown': best['portfolio']['max_drawdown'],
    }
    with open(os.path.join(RESULTS_DIR, 'optimal_portfolio.json'), 'w') as f:
        json.dump(portfolio_data, f, indent=2)


def _print_portfolio_result(portfolio, label):
    if not portfolio:
        print(f'{RED}Keine Portfolio-Ergebnisse.{NC}')
        return
    pnl_pct  = portfolio.get('total_pnl_pct', 0.0)
    pnl_usdt = portfolio.get('total_pnl_usdt', 0.0)
    max_dd   = portfolio.get('max_drawdown', 0.0)
    trades   = portfolio.get('total_trades', 0)
    wr       = portfolio.get('win_rate', 0.0)
    end_cap  = portfolio.get('end_capital', 0.0)

    pnl_c = GREEN if pnl_pct >= 0 else RED
    dd_c  = GREEN if max_dd <= 10 else YELLOW if max_dd <= 25 else RED

    print(f'\n{BOLD}{"="*55}{NC}')
    print(f'{BOLD}  {label}{NC}')
    print(f'{BOLD}{"="*55}{NC}')
    print(f'  Trades:      {trades}')
    print(f'  Win-Rate:    {wr:.1f}%')
    print(f'  PnL:         {pnl_c}{pnl_pct:+.2f}%  ({pnl_usdt:+.2f} USDT){NC}')
    print(f'  Max Drawdown:{dd_c}{max_dd:.1f}%{NC}')
    print(f'  Endkapital:  {end_cap:.2f} USDT')
    print(f'{BOLD}{"="*55}{NC}\n')


# ============================================================
# Modus 4: Interaktive Charts
# ============================================================

def mode_interactive_charts():
    from mbot.analysis.interactive_chart import run_interactive_chart
    run_interactive_chart()


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='mbot Ergebnisse anzeigen')
    parser.add_argument('--mode',               type=str, default='1',
                        choices=['1', '2', '3', '4'])
    parser.add_argument('--target_max_drawdown', type=float, default=30.0)
    args = parser.parse_args()

    try:
        if args.mode == '1':
            mode_single(args.target_max_drawdown)
        elif args.mode == '2':
            mode_manual_portfolio(args.target_max_drawdown)
        elif args.mode == '3':
            mode_auto_portfolio(args.target_max_drawdown)
        elif args.mode == '4':
            mode_interactive_charts()
    except KeyboardInterrupt:
        print(f'\n{YELLOW}Abgebrochen.{NC}')
        sys.exit(0)


if __name__ == '__main__':
    main()
