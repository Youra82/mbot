# code/analysis/local_refiner_optuna.py for mbot

import json
import os
import sys
import argparse
import optuna
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import run_mbot_backtest
from utilities.strategy_logic import calculate_mbot_indicators
from analysis.global_optimizer_pymoo import load_data

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
BASE_PARAMS = {}

# Objective-Funktion für Optuna, angepasst an mbot-Parameter
def objective(trial):
    base = BASE_PARAMS['params']
    
    params = {
        'macd': {
            'fast': trial.suggest_int('macd_fast', max(5, base['macd']['fast'] - 3), base['macd']['fast'] + 3),
            'slow': trial.suggest_int('macd_slow', max(15, base['macd']['slow'] - 5), base['macd']['slow'] + 5),
            'signal': trial.suggest_int('macd_signal', max(5, base['macd']['signal'] - 3), base['macd']['signal'] + 3),
        },
        'impulse_macd': {
            'length_ma': trial.suggest_int('impulse_length_ma', max(20, base['impulse_macd']['length_ma'] - 10), base['impulse_macd']['length_ma'] + 10),
            'length_signal': trial.suggest_int('impulse_length_signal', max(5, base['impulse_macd']['length_signal'] - 3), base['impulse_macd']['length_signal'] + 3),
        },
        'forecast': {
            'tp_atr_multiplier': trial.suggest_float('tp_atr_multiplier', base['forecast']['tp_atr_multiplier'] * 0.7, base['forecast']['tp_atr_multiplier'] * 1.3),
        },
        'risk': {
            'sl_buffer_pct': trial.suggest_float('sl_buffer_pct', base['risk']['sl_buffer_pct'] * 0.5, base['risk']['sl_buffer_pct'] * 1.5, log=True),
            'swing_lookback': trial.suggest_int('swing_lookback', max(10, base['risk']['swing_lookback'] - 10), base['risk']['swing_lookback'] + 10),
            'base_leverage': trial.suggest_int('base_leverage', max(1, base['risk']['base_leverage'] - 5), base['risk']['base_leverage'] + 5),
            'balance_fraction_pct': 10
        },
        'start_capital': START_CAPITAL
    }

    data_with_indicators = calculate_mbot_indicators(HISTORICAL_DATA.copy(), params)
    result = run_mbot_backtest(data_with_indicators.dropna(), params)

    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    
    # Score-Funktion: PnL, gewichtet mit dem Drawdown
    score = pnl * (1 - drawdown)
    return score if np.isfinite(score) else -float('inf')

def main(n_jobs, n_trials):
    print("\n--- [Stufe 2/2] Lokale Verfeinerung für mbot mit Optuna ---")
    
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
        print("     +++ FINALES BESTES ERGEBNIS NACH GLOBALER & LOKALER OPTIMIERUNG +++")
        print("="*80)
        
        # Finale Parameter aus dem besten Trial extrahieren
        final_params = {
            'macd': {
                'fast': best_overall_trial.params['macd_fast'], 'slow': best_overall_trial.params['macd_slow'],
                'signal': best_overall_trial.params['macd_signal'], 'atr_period': 14
            },
            'impulse_macd': {
                'length_ma': best_overall_trial.params['impulse_length_ma'], 'length_signal': best_overall_trial.params['impulse_length_signal']
            },
            'forecast': {
                'tp_atr_multiplier': round(best_overall_trial.params['tp_atr_multiplier'], 2)
            },
            'risk': {
                'margin_mode': "isolated", 'balance_fraction_pct': 10, 'max_leverage': 20,
                'sl_buffer_pct': round(best_overall_trial.params['sl_buffer_pct'], 2),
                'swing_lookback': best_overall_trial.params['swing_lookback'],
                'base_leverage': best_overall_trial.params['base_leverage']
            }
        }
        
        # Finalen Backtest durchführen
        backtest_params = {**final_params, 'start_capital': START_CAPITAL}
        data_with_indicators = calculate_mbot_indicators(HISTORICAL_DATA.copy(), backtest_params)
        final_result = run_mbot_backtest(data_with_indicators.dropna(), backtest_params)

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
            **final_params,
            "behavior": {"use_longs": True, "use_shorts": True}
        }
        print(json.dumps(config_output, indent=4))
        print("\n" + "="*80)
    else:
        print("Kein gültiges Ergebnis nach der Verfeinerung gefunden.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 2: Lokale Parameter-Verfeinerung für mbot.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne für die Optimierung.')
    parser.add_argument('--trials', type=int, default=200, help='Anzahl der Versuche pro Kandidat.')
    args = parser.parse_args()
    main(n_jobs=args.jobs, n_trials=args.trials)
