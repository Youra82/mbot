import json
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
from pymoo.core.callback import Callback

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_backtest
from utilities.strategy_logic import calculate_macd_forecast_indicators

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
MINIMUM_TRADES = 10

class TqdmCallback(Callback):
    def __init__(self, pbar):
        super().__init__()
        self.pbar = pbar
    def notify(self, algorithm):
        self.pbar.update(1)

class OptimizationProblem(Problem):
    def __init__(self, **kwargs):
        super().__init__(n_var=7, n_obj=2, n_constr=0,
                         # fast, slow, signal, swing, sl_buf, up_%, low_%
                         xl=[5,  20, 5,  10, 0.1, 75, 5],
                         xu=[25, 60, 20, 50, 2.0, 95, 25], **kwargs)
    def _evaluate(self, x, out, *args, **kwargs):
        results = []
        for ind in x:
            params = {
                'fast_len': int(ind[0]), 'slow_len': int(ind[1]), 'signal_len': int(ind[2]),
                'swing_lookback': int(ind[3]), 'sl_buffer_pct': round(ind[4], 2),
                'upper_percentile': int(ind[5]), 'lower_percentile': int(ind[6]),
                'max_memory': 50, 'forecast_len': 100, 'start_capital': START_CAPITAL, 'leverage': 5.0,
                'start_date_str': self.start_date_str
            }
            data_with_indicators = calculate_macd_forecast_indicators(HISTORICAL_DATA.copy(), params)
            result = run_backtest(data_with_indicators.dropna(), params)
            pnl = result.get('total_pnl_pct', -1000)
            drawdown = result.get('max_drawdown_pct', 1.0) * 100
            if result['trades_count'] < MINIMUM_TRADES: pnl = -1001
            results.append([-pnl, drawdown])
        out["F"] = np.array(results)

def main(n_procs, n_gen_default):
    print("\n--- [Stufe 1/2] Globale Suche mit Pymoo für mbot ---")
    
    symbol_input = input("Handelspaar(e) eingeben (z.B. BTC ETH): ")
    timeframe_input = input("Zeitfenster eingeben (z.B. 1h 4h): ")
    start_date = input("Startdatum eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum eingeben (JJJJ-MM-TT): ")
    n_gen_input = input(f"Anzahl der Generationen eingeben (Standard: {n_gen_default}): ")
    n_gen = int(n_gen_input) if n_gen_input else n_gen_default

    global START_CAPITAL, MINIMUM_TRADES
    START_CAPITAL = float(input("Startkapital in USDT eingeben (z.B. 1000): "))
    MINIMUM_TRADES = int(input("Mindestanzahl an Trades (z.B. 20): "))
    
    symbols_to_run = symbol_input.split()
    timeframes_to_run = timeframe_input.split()
    all_champions = []

    for symbol_short in symbols_to_run:
        for timeframe in timeframes_to_run:
            symbol = f"{symbol_short.upper()}/USDT:USDT"
            global HISTORICAL_DATA
            HISTORICAL_DATA = load_data(symbol, timeframe, start_date, end_date)
            if HISTORICAL_DATA.empty: continue

            print(f"\n===== Optimiere {symbol} auf {timeframe} für mbot =====")
            
            with Pool(n_procs) as pool:
                problem = OptimizationProblem(parallelization=StarmapParallelization(pool.starmap))
                problem.start_date_str = start_date # Hinzufügen des Startdatums zum Problem-Objekt

                algorithm = NSGA2(pop_size=100)
                termination = get_termination("n_gen", n_gen)

                with tqdm(total=n_gen, desc="Generationen") as pbar:
                    res = minimize(problem, algorithm, termination, seed=1, callback=TqdmCallback(pbar), verbose=False)

                valid_indices = [i for i, f in enumerate(res.F) if f[0] < -1]
                if not valid_indices: continue
                
                for i in sorted(valid_indices, key=lambda i: res.F[i][0])[:5]:
                    params_raw = res.X[i]
                    param_dict = {
                        'symbol': symbol, 'timeframe': timeframe, 
                        'start_date': start_date, 'end_date': end_date, 'start_capital': START_CAPITAL,
                        'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                        'params': {
                            'fast_len': int(params_raw[0]), 'slow_len': int(params_raw[1]), 'signal_len': int(params_raw[2]),
                            'swing_lookback': int(params_raw[3]), 'sl_buffer_pct': round(params_raw[4], 2),
                            'upper_percentile': int(params_raw[5]), 'lower_percentile': int(params_raw[6])
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
