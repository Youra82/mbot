# code/analysis/local_refiner_optuna.py

import json
import os
import sys
import argparse
import optuna
import numpy as np
import time
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
# --- GEÄNDERT: Korrekte Funktionen importieren ---
from analysis.backtest import run_stochrsi_backtest
from utilities.strategy_logic import calculate_stochrsi_indicators
from analysis.global_optimizer_pymoo import load_data, format_time

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
BASE_PARAMS = {}

# --- NEU: objective-Funktion komplett für stbot umgeschrieben ---
def objective(trial):
    base_params = BASE_PARAMS['params']
    
    params = {
        'stoch_rsi_period': trial.suggest_int('stoch_rsi_period', 
            max(5, base_params['stoch_rsi_period'] - 5), base_params['stoch_rsi_period'] + 5),
        'stoch_k': trial.suggest_int('stoch_k', 
            max(2, base_params['stoch_k'] - 2), base_params['stoch_k'] + 2),
        'stoch_d': trial.suggest_int('stoch_d', 
            max(2, base_params['stoch_d'] - 2), base_params['stoch_d'] + 2),
        'swing_lookback': trial.suggest_int('swing_lookback', 
            max(5, base_params['swing_lookback'] - 10), base_params['swing_lookback'] + 10),
        'sl_buffer_pct': trial.suggest_float('sl_buffer_pct', 
            base_params['sl_buffer_pct'] * 0.5, base_params['sl_buffer_pct'] * 1.5, log=True),
        'base_leverage': trial.suggest_int('base_leverage', 
            max(1, base_params['base_leverage'] - 5), base_params['base_leverage'] + 5),
        'target_atr_pct': trial.suggest_float('target_atr_pct', 
            base_params['target_atr_pct'] * 0.8, base_params['target_atr_pct'] * 1.2, log=True),
        'sideways_max_crosses': trial.suggest_int('sideways_max_crosses',
            max(2, base_params['sideways_filter']['max_crosses'] - 4), base_params['sideways_filter']['max_crosses'] + 4),
        
        'start_capital': START_CAPITAL,
        'max_leverage': 50.0,
        'balance_fraction_pct': 2.0,
        
        # Feste Parameter übernehmen
        'oversold_level': base_params.get('oversold_level', 20),
        'overbought_level': base_params.get('overbought_level', 80),
        'trend_filter': base_params['trend_filter'],
        'sideways_filter': {**base_params['sideways_filter'], 'max_crosses': trial.params['sideways_max_crosses']}
    }

    data_with_indicators = calculate_stochrsi_indicators(HISTORICAL_DATA.copy(), params)
    result = run_stochrsi_backtest(data_with_indicators.dropna(), params)

    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    
    score = pnl * (1 - drawdown)
    return score if np.isfinite(score) else -float('inf')

def main(n_jobs, n_trials):
    print("\n--- [Stufe 2/2] Lokale Verfeinerung mit Optuna ---")
    
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

    if best_overall_trial:
        print("\n\n" + "="*80)
        print("    +++ FINALES BESTES ERGEBNIS NACH GLOBALER & LOKALER OPTIMIERUNG +++")
        print("="*80)
        
        final_params_base = best_overall_info['params']
        final_params_tuned = best_overall_trial.params
        
        # --- NEU: Korrekte config.json für stbot erstellen ---
        final_params = {
            'stoch_rsi_period': final_params_tuned['stoch_rsi_period'],
            'stoch_k': final_params_tuned['stoch_k'],
            'stoch_d': final_params_tuned['stoch_d'],
            'swing_lookback': final_params_tuned['swing_lookback'],
            'sl_buffer_pct': round(final_params_tuned['sl_buffer_pct'], 2),
            'base_leverage': final_params_tuned['base_leverage'],
            'target_atr_pct': round(final_params_tuned['target_atr_pct'], 2),
            'sideways_filter': {**final_params_base['sideways_filter'], 'max_crosses': final_params_tuned['sideways_max_crosses']},
            'trend_filter': final_params_base['trend_filter'],
            'oversold_level': final_params_base.get('oversold_level', 20),
            'overbought_level': final_params_base.get('overbought_level', 80),
            'atr_period': final_params_base.get('atr_period', 14)
        }
        
        risk_params = {
            "margin_mode": "isolated", "balance_fraction_pct": 2, "max_leverage": 50,
            'sl_buffer_pct': final_params['sl_buffer_pct'],
            'base_leverage': final_params['base_leverage'],
            'target_atr_pct': final_params['target_atr_pct']
        }
        
        backtest_params = {**final_params, **risk_params, 'start_capital': START_CAPITAL}
        data_with_indicators = calculate_stochrsi_indicators(HISTORICAL_DATA.copy(), backtest_params)
        final_result = run_stochrsi_backtest(data_with_indicators.dropna(), backtest_params)

        print(f"  HANDELSCOIN: {best_overall_info['symbol']} | TIMEFRAME: {best_overall_info['timeframe']}")
        print(f"  PERFORMANCE-SCORE: {best_overall_score:.2f} (PnL, gewichtet mit Drawdown)")
        
        print("\n  FINALE PERFORMANCE-METRIKEN:")
        print(f"    - Gesamtgewinn (PnL): {final_result['total_pnl_pct']:.2f} %")
        print(f"    - Max. Drawdown:      {final_result['max_drawdown_pct']*100:.2f} %")
        print(f"    - Anzahl Trades:      {final_result['trades_count']}")
        print(f"    - Win-Rate:           {final_result['win_rate']:.2f} %")
        
        print("\n  >>> EINSTELLUNGEN FÜR DEINE 'config.json' <<<")
        config_output = {
            "market": {"symbol": best_overall_info['symbol'], "timeframe": best_overall_info['timeframe']},
            "strategy": {key: val for key, val in final_params.items() if key not in risk_params},
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
