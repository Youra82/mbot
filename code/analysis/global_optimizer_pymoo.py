# code/analysis/global_optimizer_pymoo.py for mbot

import json
import time
import numpy as np
import os
import sys
import argparse
from multiprocessing import Pool
from tqdm import tqdm

from pymoo.core.problem import StarmapParallelization, Problem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.optimize import minimize
from pymoo.termination import get_termination

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_mbot_backtest
from utilities.strategy_logic import calculate_mbot_indicators

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
MINIMUM_TRADES = 20

# Problem-Definition für die mbot-Strategie
class MbotOptimizationProblem(Problem):
    def __init__(self, **kwargs):
        super().__init__(n_var=9, n_obj=2, n_constr=0,
                         xl=[5, 15, 5, 20, 5, 1.0, 0.1, 10, 2],
                         xu=[20, 40, 15, 50, 15, 8.0, 1.5, 40, 15], **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        results = []
        for ind in x:
            params = {
                'macd': { 'fast': int(ind[0]), 'slow': int(ind[1]), 'signal': int(ind[2]) },
                'impulse_macd': { 'length_ma': int(ind[3]), 'length_signal': int(ind[4]) },
                'forecast': { 'tp_atr_multiplier': round(ind[5], 2) },
                'risk': { 'sl_buffer_pct': round(ind[6], 2), 'swing_lookback': int(ind[7]), 'base_leverage': int(ind[8]), 'balance_fraction_pct': 10 },
                'start_capital': START_CAPITAL
            }

            if params['macd']['fast'] >= params['macd']['slow']:
                results.append([9999, 100.0]) 
                continue

            data_with_indicators = calculate_mbot_indicators(HISTORICAL_DATA.copy(), params)
            result = run_mbot_backtest(data_with_indicators.dropna(), params)
            
            pnl = result.get('total_pnl_pct', -1000)
            drawdown = result.get('max_drawdown_pct', 1.0) * 100
            
            if result['trades_count'] < MINIMUM_TRADES:
                pnl = -1000
            
            results.append([-pnl, drawdown])
            
        out["F"] = np.array(results)

def main(n_procs, n_gen_default):
    print("\n--- [Stufe 1/2] Globale Suche für mbot mit Pymoo ---")
    symbol_input = input("Handelspaar(e) eingeben (z.B. BTC ETH): ")
    timeframe_input = input("Zeitfenster eingeben (z.B. 1h 4h): ")
    start_date = input("Startdatum eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum eingeben (JJJJ-MM-TT): ")
    n_gen_input = input(f"Anzahl der Generationen (Standard: {n_gen_default}): ")
    n_gen = int(n_gen_input) if n_gen_input else n_gen_default
    
    global START_CAPITAL, MINIMUM_TRADES
    START_CAPITAL = float(input("Startkapital in USDT (z.B. 1000): "))
    MINIMUM_TRADES = int(input("Mindestanzahl an Trades (z.B. 20): "))
    
    symbols_to_run = symbol_input.split()
    timeframes_to_run = timeframe_input.split()
    all_champions = []

    for symbol_short in symbols_to_run:
        for timeframe in timeframes_to_run:
            symbol = f"{symbol_short.upper()}/USDT:USDT"
            global HISTORICAL_DATA
            HISTORICAL_DATA = load_data(symbol, timeframe, start_date, end_date)
            
            # VERBESSERUNG: Deutliche Warnung und garantierter Abbruch für diese Kombination
            if HISTORICAL_DATA.empty:
                print(f"\n\033[91mFEHLER: Für {symbol} ({timeframe}) im Zeitraum {start_date} bis {end_date} wurden keine Daten gefunden.\033[0m")
                print("\033[93mDies geschieht meist, wenn das Handelspaar nicht existiert oder der Zeitraum ungültig ist. Überspringe...\033[0m")
                continue # Springe zur nächsten Symbol/Timeframe-Kombination

            print(f"\n===== Optimiere {symbol} auf {timeframe} für mbot =====")
            
            with Pool(n_procs) as pool:
                problem = MbotOptimizationProblem(parallelization=StarmapParallelization(pool.starmap))
                algorithm = NSGA2(pop_size=100)
                termination = get_termination("n_gen", n_gen)

                with tqdm(total=n_gen, desc="Generationen") as pbar:
                    res = minimize(problem, algorithm, termination, seed=1, callback=lambda alg: pbar.update(1), verbose=False)

                valid_indices = [i for i, f in enumerate(res.F) if f[0] < 9000] 
                if not valid_indices: continue
                
                for i in sorted(valid_indices, key=lambda i: res.F[i][0])[:5]:
                    ind = res.X[i]
                    param_dict = {
                        'symbol': symbol, 'timeframe': timeframe, 'start_date': start_date, 'end_date': end_date,
                        'start_capital': START_CAPITAL, 'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                        'params': {
                            'macd': {'fast': int(ind[0]), 'slow': int(ind[1]), 'signal': int(ind[2])},
                            'impulse_macd': {'length_ma': int(ind[3]), 'length_signal': int(ind[4])},
                            'forecast': {'tp_atr_multiplier': round(ind[5], 2)},
                            'risk': {'sl_buffer_pct': round(ind[6], 2), 'swing_lookback': int(ind[7]), 'base_leverage': int(ind[8])}
                        }
                    }
                    all_champions.append(param_dict)

    if not all_champions:
        print("\nKeine vielversprechenden Kandidaten gefunden."); return

    output_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    with open(output_file, 'w') as f:
        json.dump(all_champions, f, indent=4)
    print(f"\n--- Globale Suche beendet. Top-Kandidaten in '{output_file}' gespeichert. ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 1: Globale Parameter-Optimierung für mbot.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--gen', type=int, default=50, help='Standard-Anzahl der Generationen.')
    args = parser.parse_args()
    main(n_procs=args.jobs, n_gen_default=args.gen)
