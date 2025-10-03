# code/analysis/global_optimizer_pymoo.py for mbot

import json
import os
import sys
import argparse
from multiprocessing import Pool
from tqdm import tqdm
import numpy as np

from pymoo.core.problem import StarmapParallelization, Problem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.optimize import minimize
from pymoo.termination import get_termination

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_mbot_backtest
from utilities.strategy_logic import calculate_mbot_indicators

HISTORICAL_DATA, START_CAPITAL, MINIMUM_TRADES = None, 1000.0, 20

class MbotOptimizationProblem(Problem):
    def __init__(self, **kwargs):
        super().__init__(n_var=9, n_obj=3, n_constr=0,
                         xl=[3, 10, 3, 15, 3, 1.0, 0.1, 5, 2],
                         xu=[25, 50, 20, 60, 20, 10.0, 2.0, 50, 20], **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        results, trade_counts = [], []
        for ind in x:
            params = {
                'macd': {'fast': int(ind[0]), 'slow': int(ind[1]), 'signal': int(ind[2])},
                'impulse_macd': {'length_ma': int(ind[3]), 'length_signal': int(ind[4])},
                'forecast': {'tp_atr_multiplier': round(ind[5], 2)},
                'risk': {'sl_buffer_pct': round(ind[6], 2), 'swing_lookback': int(ind[7]), 'base_leverage': int(ind[8]), 'balance_fraction_pct': 10},
                'start_capital': START_CAPITAL
            }
            if params['macd']['fast'] >= params['macd']['slow']:
                results.append([9999, 100.0]); trade_counts.append(0); continue
            data_with_indicators = calculate_mbot_indicators(HISTORICAL_DATA.copy(), params)
            result = run_mbot_backtest(data_with_indicators.dropna(), params)
            pnl, drawdown, trades = result.get('total_pnl_pct', -1000), result.get('max_drawdown_pct', 1.0) * 100, result.get('trades_count', 0)
            trade_counts.append(trades)
            if trades < MINIMUM_TRADES: pnl = -1000
            results.append([-pnl, drawdown])
        out["F"] = np.column_stack([np.array(results), -np.array(trade_counts)])

def main(n_procs, n_gen_default):
    print("\n--- [Stufe 1/2] Globale Suche für mbot mit Pymoo ---")
    symbol_input = input("Handelspaar(e) eingeben (z.B. BTC ETH): ")
    timeframe_input = input("Zeitfenster eingeben (z.B. 1h 4h): ")
    start_date, end_date = input("Startdatum eingeben (JJJJ-MM-TT): "), input("Enddatum eingeben (JJJJ-MM-TT): ")
    n_gen = int(input(f"Anzahl der Generationen (Standard: {n_gen_default}): ") or n_gen_default)
    
    global START_CAPITAL, MINIMUM_TRADES
    START_CAPITAL = float(input("Startkapital in USDT (z.B. 1000): "))
    MINIMUM_TRADES = int(input("Mindestanzahl an Trades (z.B. 20): "))
    
    all_champions = []
    for symbol_short in symbol_input.split():
        for timeframe in timeframe_input.split():
            symbol = f"{symbol_short.upper()}/USDT:USDT"
            global HISTORICAL_DATA
            HISTORICAL_DATA = load_data(symbol, timeframe, start_date, end_date)
            if HISTORICAL_DATA.empty:
                print(f"\n\033[91mFEHLER: Für {symbol} ({timeframe}) wurden keine Daten gefunden. Überspringe...\033[0m")
                continue
            print(f"\n===== Optimiere {symbol} auf {timeframe} für mbot =====")
            with Pool(n_procs) as pool:
                problem = MbotOptimizationProblem(parallelization=StarmapParallelization(pool.starmap))
                algorithm, termination = NSGA2(pop_size=100), get_termination("n_gen", n_gen)
                with tqdm(total=n_gen, desc="Generationen") as pbar:
                    res = minimize(problem, algorithm, termination, seed=1, callback=lambda alg: pbar.update(1), verbose=False)
                sorted_indices = sorted(range(len(res.F)), key=lambda i: (res.F[i][0], res.F[i][1]))
                for i in sorted_indices[:5]:
                    ind, pnl, drawdown, trades = res.X[i], -res.F[i][0], res.F[i][1], -res.F[i][2]
                    all_champions.append({
                        'symbol': symbol, 'timeframe': timeframe, 'start_date': start_date, 'end_date': end_date,
                        'start_capital': START_CAPITAL, 'pnl': pnl, 'drawdown': drawdown, 'trades_count': int(trades),
                        'params': {
                            'macd': {'fast': int(ind[0]), 'slow': int(ind[1]), 'signal': int(ind[2])},
                            'impulse_macd': {'length_ma': int(ind[3]), 'length_signal': int(ind[4])},
                            'forecast': {'tp_atr_multiplier': round(ind[5], 2)},
                            'risk': {'sl_buffer_pct': round(ind[6], 2), 'swing_lookback': int(ind[7]), 'base_leverage': int(ind[8])}
                        }
                    })
    if not all_champions:
        print("\nKeine vielversprechenden Kandidaten gefunden."); return
    output_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    with open(output_file, 'w') as f: json.dump(all_champions, f, indent=4)
    print(f"\n--- Globale Suche beendet. Top-Kandidaten in '{output_file}' gespeichert. ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 1: Globale Parameter-Optimierung für mbot.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--gen', type=int, default=50, help='Standard-Anzahl der Generationen.')
    args = parser.parse_args()
    main(n_procs=args.jobs, n_gen_default=args.gen)
