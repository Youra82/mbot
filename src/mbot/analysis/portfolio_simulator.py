# src/mbot/analysis/portfolio_simulator.py
"""
mbot Portfolio Simulator (v2 — Jaegerbot-Stil)

Aenderungen gegenueber v1:
  - Pro-Strategie-Lock statt globalem Lock:
    Mehrere Strategien koennen gleichzeitig in einem Trade sein,
    solange es sich um unterschiedliche Symbol+TF-Kombinationen handelt.
  - Calmar Ratio (PnL / MaxDD) als Optimierungs-Score statt rohem PnL.
  - Greedy-Algorithmus statt Brute-Force-Kombinatorik.
  - _best_tf_per_coin nutzt ebenfalls Calmar als Auswahlkriterium.

Funktionen:
  run_portfolio_simulation(results, start_capital) -> portfolio_dict
  find_best_portfolio(results, start_capital, target_max_dd) -> best_dict
"""


def _empty_portfolio(start_capital: float) -> dict:
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


def _calmar(port: dict) -> float:
    """Calmar Ratio: PnL% / MaxDD%. Gibt PnL zurueck wenn DD = 0."""
    dd  = port.get('max_drawdown', 0.0)
    pnl = port.get('total_pnl_pct', 0.0)
    return pnl / dd if dd > 0 else pnl


def _merge_trades_chronological(results_dict: dict) -> list:
    """
    Sammelt alle Trades aus allen Strategien, haengt den Strategie-Key
    (_strategy_key = Dateiname) an jeden Trade und sortiert chronologisch.
    """
    all_trades = []
    for fn, result in results_dict.items():
        for t in result.get('trades', []):
            all_trades.append({**t, '_strategy_key': fn})
    all_trades.sort(key=lambda t: t.get('entry_time', ''))
    return all_trades


def _simulate_portfolio(trades: list, start_capital: float) -> dict:
    """
    Simuliert Portfolio mit gemeinsamem Kapital-Pool.

    Regel (Jaegerbot-Stil): Pro Strategie (_strategy_key) darf kein
    neuer Trade starten waehrend die Strategie noch in einem Trade ist.
    Verschiedene Strategien koennen gleichzeitig laufen.
    """
    if not trades:
        return _empty_portfolio(start_capital)

    capital    = start_capital
    executed   = []
    open_until = {}  # strategy_key -> exit_time (ISO-String)

    for t in trades:
        key        = t.get('_strategy_key', '')
        entry_time = t.get('entry_time', '')
        exit_time  = t.get('exit_time', '')

        # Diese Strategie ist noch in einem Trade → ueberspringen
        if key in open_until and entry_time <= open_until[key]:
            continue

        # pnl_pct vom Backtester ist bereits relativ zum vollen Kapital
        pnl_pct  = t.get('pnl_pct', 0.0)
        pnl_usdt = capital * pnl_pct / 100.0
        capital  = max(capital + pnl_usdt, 0.0)

        open_until[key] = exit_time

        executed.append({
            **t,
            'portfolio_pnl_usdt':      round(pnl_usdt, 2),
            'portfolio_capital_after': round(capital, 2),
        })

    if not executed:
        return _empty_portfolio(start_capital)

    wins = sum(1 for t in executed if t.get('result') == 'win')

    cap_curve = [start_capital] + [t['portfolio_capital_after'] for t in executed]
    peak  = cap_curve[0]
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
        'losses':         len(executed) - wins,
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
    Behaelt pro Coin nur den Timeframe mit dem besten Calmar-Wert
    (PnL / MaxDD). Verhindert widerspruechliche Signale fuer denselben Coin.
    """
    best = {}  # coin_base -> (filename, calmar_score)
    for fn, r in results_dict.items():
        coin   = r.get('symbol', '').replace('/USDT:USDT', '').replace('/', '').replace(':', '')
        dd     = r.get('max_drawdown', 0.0)
        pnl    = r.get('total_pnl_pct', 0.0)
        calmar = pnl / dd if dd > 0 else pnl
        if coin not in best or calmar > best[coin][1]:
            best[coin] = (fn, calmar)
    return {fn: results_dict[fn] for fn, _ in best.values()}


def find_best_portfolio(results_dict: dict, start_capital: float,
                        target_max_dd: float, verbose: bool = False) -> dict:
    """
    Findet das beste Portfolio per Greedy-Algorithmus (Jaegerbot-Stil).

    Score-Metrik: Calmar Ratio (PnL% / MaxDD%) — balanciert Rendite und Risiko.
    Algorithmus:
      1. Bewerte alle Einzelstrategien, waehle besten Calmar als Star-Strategie.
      2. Greedy: Fuege die Strategie hinzu die den Calmar am meisten verbessert.
      3. Stoppe wenn keine Ergaenzung mehr hilft.
    Constraint: max. 1 Timeframe pro Coin (bester Calmar wird behalten).
    """
    filtered = _best_tf_per_coin(results_dict)
    if verbose and len(filtered) < len(results_dict):
        skipped = len(results_dict) - len(filtered)
        print(f'  Constraint: 1 TF/Coin → {skipped} schwaecher(e) Timeframe(s) ausgeschlossen.')

    keys = list(filtered.keys())

    # --- Schritt 1: Alle Einzelstrategien bewerten ---
    if verbose:
        print(f'  Bewerte {len(keys)} Einzelstrategie(n)...')

    single_scores = []
    for k in keys:
        subset = {k: filtered[k]}
        trades = _merge_trades_chronological(subset)
        port   = _simulate_portfolio(trades, start_capital)

        if port['total_trades'] == 0:
            continue
        if port['max_drawdown'] > target_max_dd:
            continue
        if port['total_pnl_pct'] <= 0:
            continue

        score = _calmar(port)
        single_scores.append((k, score, port))

    if not single_scores:
        if verbose:
            print(f'  Keine Einzelstrategie erfuellt den DD-Constraint ({target_max_dd}%).')
        return None

    single_scores.sort(key=lambda x: x[1], reverse=True)
    best_key, best_score, best_port = single_scores[0]

    if verbose:
        print(f'  Star-Strategie: {filtered[best_key].get("symbol","?")} '
              f'{filtered[best_key].get("timeframe","?")} '
              f'(Calmar: {best_score:.2f} | '
              f'PnL: {best_port["total_pnl_pct"]:+.1f}% | '
              f'DD: {best_port["max_drawdown"]:.1f}%)')

    best_selected  = [best_key]
    # Alle anderen Einzelkandidaten (auch solche die individuell den DD verletzen
    # koennen im Verbund besser sein — daher aus dem vollen filtered-Pool)
    candidate_pool = [k for k in keys if k != best_key]

    # --- Schritt 2: Greedy — ergaenze solange Calmar steigt ---
    if verbose:
        print(f'  Suche beste Ergaenzungen (Greedy + Calmar)...')

    while candidate_pool:
        best_addition       = None
        best_addition_score = best_score
        best_addition_port  = best_port

        for candidate in candidate_pool:
            combo  = best_selected + [candidate]
            subset = {k: filtered[k] for k in combo}
            trades = _merge_trades_chronological(subset)
            port   = _simulate_portfolio(trades, start_capital)

            if port['total_trades'] == 0:
                continue
            if port['max_drawdown'] > target_max_dd:
                continue
            if port['total_pnl_pct'] <= 0:
                continue

            score = _calmar(port)
            if score > best_addition_score:
                best_addition_score = score
                best_addition       = candidate
                best_addition_port  = port

        if best_addition:
            r = filtered[best_addition]
            if verbose:
                print(f'  + {r.get("symbol","?")} {r.get("timeframe","?")} '
                      f'(Calmar: {best_addition_score:.2f} | '
                      f'PnL: {best_addition_port["total_pnl_pct"]:+.1f}% | '
                      f'DD: {best_addition_port["max_drawdown"]:.1f}%)')
            best_selected.append(best_addition)
            candidate_pool.remove(best_addition)
            best_score = best_addition_score
            best_port  = best_addition_port
        else:
            if verbose:
                print(f'\n  Keine weitere Verbesserung. Optimierung beendet.')
            break

    return {
        'portfolio': best_port,
        'selected':  best_selected,
    }
