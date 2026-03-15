# src/mbot/analysis/portfolio_simulator.py
"""
mbot Portfolio Simulator

Simuliert ein gemeinsames Kapital-Portfolio aus mehreren Backtest-Ergebnissen.
Regel: Nur ein Trade zur Zeit (wie im Live-Betrieb durch global_state.json).

Funktionen:
  run_portfolio_simulation(results, start_capital) -> portfolio_dict
  find_best_portfolio(results, start_capital, target_max_dd) -> best_dict
"""

from itertools import combinations


def _merge_trades_chronological(results_dict: dict) -> list:
    """Sammelt alle Trades aus allen Strategien und sortiert sie chronologisch."""
    all_trades = []
    for fn, result in results_dict.items():
        for t in result.get('trades', []):
            all_trades.append(t)
    all_trades.sort(key=lambda t: t.get('entry_time', ''))
    return all_trades


def _simulate_portfolio(trades: list, start_capital: float) -> dict:
    """
    Simuliert Portfolio mit gemeinsamen Kapital-Pool.
    Nur ein Trade zur Zeit (chronologisch, kein Overlap erlaubt).
    """
    if not trades:
        return {
            'total_trades':   0,
            'wins':           0,
            'losses':         0,
            'win_rate':       0.0,
            'total_pnl_pct':  0.0,
            'total_pnl_usdt': 0.0,
            'max_drawdown':   0.0,
            'start_capital':  start_capital,
            'end_capital':    start_capital,
            'trades':         [],
        }

    capital       = start_capital
    executed      = []
    last_exit     = ''

    for t in trades:
        entry_time = t.get('entry_time', '')
        exit_time  = t.get('exit_time', '')

        # Kein Overlap: neuer Trade darf erst nach letztem Exit starten
        if last_exit and entry_time <= last_exit:
            continue

        # Kapital-gewichtete PnL – risk_per_trade_pct korrekt beruecksichtigen
        pnl_pct          = t.get('pnl_pct', 0.0)
        risk_per_trade   = t.get('risk_per_trade_pct', 100.0)
        effective_capital = capital * risk_per_trade / 100.0
        pnl_usdt  = effective_capital * pnl_pct / 100.0
        capital   = max(capital + pnl_usdt, 0.0)
        last_exit = exit_time

        executed.append({
            **t,
            'portfolio_pnl_usdt': round(pnl_usdt, 2),
            'portfolio_capital_after': round(capital, 2),
        })

    if not executed:
        return {
            'total_trades':   0,
            'wins':           0,
            'losses':         0,
            'win_rate':       0.0,
            'total_pnl_pct':  0.0,
            'total_pnl_usdt': 0.0,
            'max_drawdown':   0.0,
            'start_capital':  start_capital,
            'end_capital':    start_capital,
            'trades':         [],
        }

    wins    = sum(1 for t in executed if t.get('result') == 'win')
    losses  = len(executed) - wins

    # Drawdown berechnen
    cap_curve = [start_capital] + [t['portfolio_capital_after'] for t in executed]
    peak   = cap_curve[0]
    max_dd = 0.0
    for c in cap_curve:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    total_pnl_usdt = capital - start_capital
    total_pnl_pct  = total_pnl_usdt / start_capital * 100.0 if start_capital > 0 else 0.0

    return {
        'total_trades':   len(executed),
        'wins':           wins,
        'losses':         losses,
        'win_rate':       round(wins / len(executed) * 100, 1),
        'total_pnl_pct':  round(total_pnl_pct, 2),
        'total_pnl_usdt': round(total_pnl_usdt, 2),
        'max_drawdown':   round(max_dd, 2),
        'start_capital':  start_capital,
        'end_capital':    round(capital, 2),
        'trades':         executed,
    }


def run_portfolio_simulation(results_dict: dict, start_capital: float) -> dict:
    """
    Simuliert alle uebergebenen Strategien im gemeinsamen Kapital-Pool.

    Args:
        results_dict: {filename: backtest_result_dict}
        start_capital: Startkapital in USDT

    Returns:
        Portfolio-Ergebnis-Dict
    """
    trades = _merge_trades_chronological(results_dict)
    return _simulate_portfolio(trades, start_capital)


def _best_tf_per_coin(results_dict: dict) -> dict:
    """
    Behaelt pro Coin nur den Timeframe mit dem hoechsten individuellen PnL.
    Verhindert dass z.B. BTC/15m + BTC/1h + BTC/4h gleichzeitig ins Portfolio kommen.
    """
    best = {}  # coin_base -> (filename, pnl)
    for fn, r in results_dict.items():
        coin = r.get('symbol', '').replace('/USDT:USDT', '').replace('/', '').replace(':', '')
        pnl  = r.get('total_pnl_pct', 0.0)
        if coin not in best or pnl > best[coin][1]:
            best[coin] = (fn, pnl)
    return {fn: results_dict[fn] for fn, _ in best.values()}


def find_best_portfolio(results_dict: dict, start_capital: float,
                        target_max_dd: float, verbose: bool = False) -> dict:
    """
    Findet die beste Kombination aus den uebergebenen Strategien,
    die den Drawdown-Constraint erfuellt und den PnL maximiert.
    Regel: max. 1 Timeframe pro Coin (bester individueller PnL wird behalten).
    Bricht fruehzeitig ab, wenn mehr Strategien keine Verbesserung bringen.
    """
    # Pro Coin nur besten TF behalten
    filtered = _best_tf_per_coin(results_dict)
    if verbose and len(filtered) < len(results_dict):
        skipped = len(results_dict) - len(filtered)
        print(f'  Constraint: 1 TF/Coin → {skipped} schwaecher(e) Timeframe(s) ausgeschlossen.')

    keys = list(filtered.keys())
    n    = len(keys)

    max_combo_size     = min(n, 8)
    best_pnl           = -float('inf')
    best_portfolio     = None
    best_selected      = None
    last_improved_size = 0

    for size in range(1, max_combo_size + 1):
        combos        = list(combinations(keys, size))
        size_best_pnl = -float('inf')
        size_best_dd  = 0.0

        for combo in combos:
            subset = {k: filtered[k] for k in combo}
            trades = _merge_trades_chronological(subset)
            port   = _simulate_portfolio(trades, start_capital)

            if port['total_trades'] == 0:
                continue
            if port['max_drawdown'] > target_max_dd:
                continue
            if port['total_pnl_pct'] > size_best_pnl:
                size_best_pnl = port['total_pnl_pct']
                size_best_dd  = port['max_drawdown']
            if port['total_pnl_pct'] > best_pnl:
                best_pnl           = port['total_pnl_pct']
                best_portfolio     = port
                best_selected      = list(combo)
                last_improved_size = size

        if verbose:
            if size_best_pnl > -float('inf'):
                print(f'  Teste Teams mit {size} Strategie(n) ({len(combos)} Kombinationen)'
                      f' ... Bestes PnL: {size_best_pnl:+.1f}%  MaxDD: {size_best_dd:.1f}%')
            else:
                print(f'  Teste Teams mit {size} Strategie(n) ({len(combos)} Kombinationen)'
                      f' ... kein gueltiges Portfolio gefunden')

        # Fruehzeitiger Abbruch bei 2 Groessen ohne Verbesserung
        if last_improved_size > 0 and size >= last_improved_size + 2:
            if verbose:
                print(f'\n  Keine weitere Verbesserung durch mehr Strategien. Optimierung beendet.')
            break

    if best_portfolio is None:
        return None

    return {
        'portfolio': best_portfolio,
        'selected':  best_selected,
    }
