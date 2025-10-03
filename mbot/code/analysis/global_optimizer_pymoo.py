# code/analysis/global_optimizer_pymoo.py

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
from pymoo.core.callback import Callback

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_macd_backtest
from utilities.strategy_logic import calculate_macd_indicators

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
MAX_LOSS_PER_TRADE_PCT = 2.0
MINIMUM_TRADES = 10

class TqdmCallback(Callback):
    def __init__(self, pbar):
        super().__init__()
        self.pbar = pbar

    def notify(self, algorithm):
        self.pbar.update(1)

def format_time(seconds):
    if seconds < 60: return f"{seconds:.1f} Sekunden"
    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)
    if minutes < 60: return f"{minutes} Minuten und {remaining_seconds} Sekunden"
    hours = int(minutes // 60)
    remaining_minutes = int(minutes % 60)
    return f"{hours} Stunden, {remaining_minutes} Minuten und {remaining_seconds} Sekunden"

class MACDOptimizationProblem(Problem):
    def __init__(self, trend_filter_enabled, **kwargs):
        super().__init__(n_var=7, n_obj=2, n_constr=1,
                         # macd_fast, macd_slow, macd_signal, swing_lookback, sl_buffer, base_leverage, target_atr
                         xl=[5,  20, 5,  5, 0.1, 1, 1.0],
                         xu=[50, 100, 30, 60, 2.0, 20, 5.0], **kwargs)
        self.trend_filter_enabled = trend_filter_enabled

    def _evaluate(self, x, out, *args, **kwargs):
        results_F = []
        results_G = []
        for individual in x:
            fast = int(round(individual[0]))
            slow = int(round(individual[1]))
            results_G.append([fast - slow])

            params = {
                'macd_fast': fast,
                'macd_slow': slow,
                'macd_signal': int(round(individual[2])),
                'swing_lookback': int(round(individual[3])),
                'sl_buffer_pct': round(individual[4], 2),
                'base_leverage': int(round(individual[5])),
                'target_atr_pct': round(individual[6], 2),
                'max_leverage': 50.0, 'start_capital': START_CAPITAL, 'balance_fraction_pct': 10.0,
                'trend_filter': { 'enabled': self.trend_filter_enabled, 'period': 200 }
            }
            
            data_with_indicators = calculate_macd_indicators(HISTORICAL_DATA.copy(), params)
            result = run_macd_backtest(data_with_indicators.dropna(), params)
            pnl = result.get('total_pnl_pct', -1000)
            drawdown = result.get('max_drawdown_pct', 1.0) * 100
            
            if result['trades_count'] < MINIMUM_TRADES: pnl = -1000
            
            if result["trade_log"]:
                max_loss_in_run = max([-t['pnl'] for t in result["trade_log"] if t['pnl'] < 0], default=0)
                if (max_loss_in_run / START_CAPITAL * 100) > MAX_LOSS_PER_TRADE_PCT:
                     pnl = -1001

            results_F.append([-pnl, drawdown])
        
        out["F"] = np.array(results_F)
        out["G"] = np.array(results_G)

def main(n_procs, n_gen_default):
    print("\n--- [Stufe 1/2] Globale Suche mit Pymoo (mbot - MACD) ---")
    symbol_input = input("Handelspaar(e) eingeben (z.B. BTC ETH): ")
    timeframe_input = input("Zeitfenster eingeben (z.B. 1h 4h): ")
    start_date = input("Startdatum eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum eingeben (JJJJ-MM-TT): ")
    n_gen_input = input(f"Anzahl der Generationen eingeben (Standard: {n_gen_default}): ")
    n_gen = int(n_gen_input) if n_gen_input else n_gen_default

    print("\n--- Filter-Einstellungen für die Optimierung ---")
    trend_response = input("Soll der EMA-Trendfilter für diese Optimierung aktiviert werden? [j/N]: ")
    is_trend_filter_enabled = trend_response.lower() in ['j', 'ja']
    print("--------------------------------------------------\n")

    global START_CAPITAL, MAX_LOSS_PER_TRADE_PCT, MINIMUM_TRADES
    START_CAPITAL = float(input("Startkapital in USDT eingeben (z.B. 1000): "))
    MAX_LOSS_PER_TRADE_PCT = float(input("Maximaler Verlust pro Trade in % des Startkapitals (z.B. 5.0): "))
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

            print(f"\n===== Optimiere {symbol} auf {timeframe} mit mbot (MACD) =====")
            if is_trend_filter_enabled: print("   - EMA-Trendfilter: Aktiviert")

            pop_size = 100
            
            with Pool(n_procs) as pool:
                problem = MACDOptimizationProblem(
                    trend_filter_enabled=is_trend_filter_enabled,
                    parallelization=StarmapParallelization(pool.starmap)
                )
                algorithm = NSGA2(pop_size=pop_size)
                termination = get_termination("n_gen", n_gen)

                with tqdm(total=n_gen, desc="Generationen") as pbar:
                    res = minimize(problem, algorithm, termination, seed=1, callback=TqdmCallback(pbar), verbose=False)

                valid_indices = [i for i, f in enumerate(res.F) if f[0] < 0]
                if not valid_indices: continue
                
                for i in sorted(valid_indices, key=lambda i: res.F[i][0])[:5]:
                    params = res.X[i]
                    param_dict = {
                        'symbol': symbol, 'timeframe': timeframe, 'start_date': start_date, 'end_date': end_date, 'start_capital': START_CAPITAL,
                        'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                        'params': {
                            'macd_fast': int(round(params[0])),
                            'macd_slow': int(round(params[1])),
                            'macd_signal': int(round(params[2])),
                            'swing_lookback': int(round(params[3])),
                            'sl_buffer_pct': round(params[4], 2),
                            'base_leverage': int(round(params[5])),
                            'target_atr_pct': round(params[6], 2),
                            'trend_filter': { 'enabled': is_trend_filter_enabled, 'period': 200 },
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
    parser = argparse.ArgumentParser(description="Stufe 1: Globale Parameter-Optimierung mit Pymoo für mbot.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--gen', type=int, default=50, help='Standard-Anzahl der Generationen.')
    args = parser.parse_args()
    main(n_procs=args.jobs, n_gen_default=args.gen)
