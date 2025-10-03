# code/analysis/local_refiner_optuna.py

import json
import os
import sys
import argparse
import optuna
import numpy as np
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
# ### KORRIGIERT: Der Import-Name ist nun korrekt. ###
from analysis.backtest import run_macd_backtest
from utilities.strategy_logic import calculate_macd_indicators
from analysis.global_optimizer_pymoo import load_data

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
BASE_PARAMS = {}

def objective(trial):
    base_params = BASE_PARAMS['params']
    
    macd_fast = trial.suggest_int('macd_fast', max(5, base_params['macd_fast'] - 5), base_params['macd_fast'] + 5)
    macd_slow = trial.suggest_int('macd_slow', max(20, base_params['macd_slow'] - 10), base_params['macd_slow'] + 10)
    
    if macd_fast >= macd_slow:
        return -float('inf')

    params = {
        'macd_fast': macd_fast,
        'macd_slow': macd_slow,
        'macd_signal': trial.suggest_int('macd_signal', max(2, base_params['macd_signal'] - 3), base_params['macd_signal'] + 3),
        'swing_lookback': trial.suggest_int('swing_lookback', max(5, base_params['swing_lookback'] - 10), base_params['swing_lookback'] + 10),
        'sl_buffer_pct': trial.suggest_float('sl_buffer_pct', base_params['sl_buffer_pct'] * 0.7, base_params['sl_buffer_pct'] * 1.3, log=True),
        'base_leverage': trial.suggest_int('base_leverage', max(1, base_params['base_leverage'] - 5), base_params['base_leverage'] + 5),
        'target_atr_pct': trial.suggest_float('target_atr_pct', base_params['target_atr_pct'] * 0.8, base_params['target_atr_pct'] * 1.2, log=True),
        'start_capital': START_CAPITAL,
        'max_leverage': 50.0,
        'balance_fraction_pct': 10.0,
        'trend_filter': base_params['trend_filter']
    }

    data_with_indicators = calculate_macd_indicators(HISTORICAL_DATA.copy(), params)
    # ### KORRIGIERT: Der Funktionsaufruf ist nun korrekt. ###
    result = run_macd_backtest(data_with_indicators.dropna(), params)

    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    trades = result.get('trades_count', 0)

    if trades < 10:
        return -float('inf')
    
    score = pnl / (drawdown + 0.01) # Sharpe-ähnliche Metrik
    return score if np.isfinite(score) else -float('inf')

def main(n_jobs, n_trials):
    print("\n--- [Stufe 2/2] Lokale Verfeinerung mit Optuna (mbot - MACD) ---")
    
    input_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    if not os.path.exists(input_file):
        print(f"Fehler: '{input_file}' nicht gefunden. Bitte Stufe 1 zuerst ausführen.")
        return

    with open(input_file, 'r') as f: candidates = json.load(f)
    print(f"Lade {len(candidates)} Kandidaten zur Verfeinerung...")
    
    if not candidates: return

    best_overall_trial = None
    best_overall_score = -float('inf')
    best_overall_info = {}
    best_final_result = {}

    for i, candidate in enumerate(candidates):
        print(f"\n===== Verfeinere Kandidat {i+1}/{len(candidates)} für {candidate['symbol']} ({candidate['timeframe']}) =====")
        
        global HISTORICAL_DATA, BASE_PARAMS, START_CAPITAL
        HISTORICAL_DATA = load_data(candidate['symbol'], candidate['timeframe'], candidate['start_date'], candidate['end_date'])
        BASE_PARAMS = candidate
        START_CAPITAL = candidate['start_capital']
        
        if HISTORICAL_DATA.empty: continue
            
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)
        
        if study.best_value > best_overall_score:
            best_overall_score = study.best_value
            best_overall_trial = study.best_trial
            best_overall_info = candidate
            
            # Führe finalen Backtest mit den besten Parametern durch
            final_params_for_backtest = {**study.best_params, 'start_capital': START_CAPITAL, 'trend_filter': candidate['params']['trend_filter']}
            data_with_indicators = calculate_macd_indicators(HISTORICAL_DATA.copy(), final_params_for_backtest)
            best_final_result = run_macd_backtest(data_with_indicators.dropna(), final_params_for_backtest)


    if best_overall_trial:
        print("\n\n" + "="*80)
        print("   +++ FINALES BESTES ERGEBNIS NACH GLOBALER & LOKALER OPTIMIERUNG +++")
        print("="*80)
        
        final_params_tuned = best_overall_trial.params
        
        print(f"  HANDELSCOIN: {best_overall_info['symbol']} | TIMEFRAME: {best_overall_info['timeframe']}")
        print(f"  PERFORMANCE-SCORE: {best_overall_score:.2f}")
        
        print("\n  FINALE PERFORMANCE-METRIKEN:")
        print(f"    - Gesamtgewinn (PnL): {best_final_result['total_pnl_pct']:.2f} %")
        print(f"    - Max. Drawdown:      {best_final_result['max_drawdown_pct']*100:.2f} %")
        print(f"    - Anzahl Trades:      {best_final_result['trades_count']}")
        print(f"    - Win-Rate:           {best_final_result['win_rate']:.2f} %")
        
        print("\n  >>> EINSTELLUNGEN FÜR DEINE 'config.json' <<<")
        strategy_params = {
            'macd_fast': final_params_tuned['macd_fast'],
            'macd_slow': final_params_tuned['macd_slow'],
            'macd_signal': final_params_tuned['macd_signal'],
            'swing_lookback': final_params_tuned['swing_lookback'],
            'atr_period': 14, # Standardwert
            'trend_filter': best_overall_info['params']['trend_filter'],
        }
        risk_params = {
            "margin_mode": "isolated",
            "balance_fraction_pct": 10,
            "max_leverage": 20,
            'sl_buffer_pct': round(final_params_tuned['sl_buffer_pct'], 2),
            'base_leverage': final_params_tuned['base_leverage'],
            'target_atr_pct': round(final_params_tuned['target_atr_pct'], 2)
        }
        config_output = {
            "market": {"symbol": best_overall_info['symbol'], "timeframe": best_overall_info['timeframe']},
            "strategy": strategy_params,
            "risk": risk_params,
            "behavior": {"use_longs": True, "use_shorts": True}
        }
        print(json.dumps(config_output, indent=4))
        print("\n" + "="*80)
    else:
        print("Kein gültiges Ergebnis nach der Verfeinerung gefunden.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 2: Lokale Parameter-Verfeinerung mit Optuna.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne für die Optimierung.')
    parser.add_argument('--trials', type=int, default=200, help='Anzahl der Versuche pro Kandidat.')
    args = parser.parse_args()
    main(n_jobs=args.jobs, n_trials=args.trials)
