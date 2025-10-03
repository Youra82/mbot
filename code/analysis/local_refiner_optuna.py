import json
import os
import sys
import argparse
import optuna
import numpy as np
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import run_stochrsi_backtest, run_mbot_backtest
from utilities.strategy_logic import calculate_stochrsi_indicators, calculate_macd_forecast_indicators
from analysis.global_optimizer_pymoo import load_data, format_time

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
START_CAPITAL = 1000.0
BASE_PARAMS = {}

# --- Objective-Funktion für stbot ---
def objective_stbot(trial):
    base = BASE_PARAMS['params']
    params = {
        'stoch_rsi_period': trial.suggest_int('stoch_rsi_period', max(5, base['stoch_rsi_period'] - 5), base['stoch_rsi_period'] + 5),
        'stoch_k': trial.suggest_int('stoch_k', max(2, base['stoch_k'] - 3), base['stoch_k'] + 3),
        'stoch_d': trial.suggest_int('stoch_d', max(2, base['stoch_d'] - 3), base['stoch_d'] + 3),
        'swing_lookback': trial.suggest_int('swing_lookback', max(5, base['swing_lookback'] - 10), base['swing_lookback'] + 10),
        'sl_buffer_pct': trial.suggest_float('sl_buffer_pct', base['sl_buffer_pct'] * 0.5, base['sl_buffer_pct'] * 1.5),
        'base_leverage': trial.suggest_int('base_leverage', max(1, base['base_leverage'] - 5), base['base_leverage'] + 5),
        **base, 'start_capital': START_CAPITAL
    }
    data_with_indicators = calculate_stochrsi_indicators(HISTORICAL_DATA.copy(), params)
    result = run_stochrsi_backtest(data_with_indicators.dropna(), params)
    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    return pnl * (1 - drawdown)

# --- NEU: Objective-Funktion für mbot ---
def objective_mbot(trial):
    base = BASE_PARAMS['params']
    params = {
        'fast_len': trial.suggest_int('fast_len', max(2, base['fast_len'] - 3), base['fast_len'] + 3),
        'slow_len': trial.suggest_int('slow_len', max(10, base['slow_len'] - 5), base['slow_len'] + 5),
        'signal_len': trial.suggest_int('signal_len', max(2, base['signal_len'] - 3), base['signal_len'] + 3),
        'swing_lookback': trial.suggest_int('swing_lookback', max(5, base['swing_lookback'] - 10), base['swing_lookback'] + 10),
        'sl_buffer_pct': trial.suggest_float('sl_buffer_pct', base['sl_buffer_pct'] * 0.7, base['sl_buffer_pct'] * 1.3),
        'upper_percentile': trial.suggest_int('upper_percentile', base['upper_percentile'] - 5, base['upper_percentile'] + 5),
        'lower_percentile': trial.suggest_int('lower_percentile', base['lower_percentile'] - 5, base['lower_percentile'] + 5),
        **base, 'start_capital': START_CAPITAL, 'leverage': 5.0
    }
    data_with_indicators = calculate_macd_forecast_indicators(HISTORICAL_DATA.copy(), params)
    result = run_mbot_backtest(data_with_indicators.dropna(), params)
    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    return pnl * (1 - drawdown)

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
        bot_type = candidate['bot_type']
        print(f"\n===== Verfeinere Kandidat {i+1}/{len(candidates)} ({bot_type.upper()}) für {candidate['symbol']} =====")
        
        global HISTORICAL_DATA, BASE_PARAMS, START_CAPITAL
        required_days = 200 if bot_type == 'mbot' else 50
        HISTORICAL_DATA = load_data(candidate['symbol'], candidate['timeframe'], candidate['start_date'], candidate['end_date'], required_days_before=required_days)
        BASE_PARAMS = candidate
        START_CAPITAL = candidate['start_capital']
        if HISTORICAL_DATA.empty: continue
            
        study = optuna.create_study(direction="maximize")
        objective_func = objective_mbot if bot_type == 'mbot' else objective_stbot
        study.optimize(objective_func, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)
        
        if study.best_value > best_overall_score:
            best_overall_score = study.best_value
            best_overall_trial = study.best_trial
            best_overall_info = candidate

    if best_overall_trial:
        print("\n\n" + "="*80)
        print("  +++ FINALES BESTES ERGEBNIS NACH GLOBALER & LOKALER OPTIMIERUNG +++")
        print("="*80)
        
        final_bot_type = best_overall_info['bot_type']
        final_params_tuned = best_overall_trial.params
        
        # Führe finalen Backtest mit den besten Parametern durch
        if final_bot_type == 'stbot':
            final_params = {**best_overall_info['params'], **final_params_tuned, 'start_capital': START_CAPITAL}
            data_final = calculate_stochrsi_indicators(HISTORICAL_DATA.copy(), final_params)
            final_result = run_stochrsi_backtest(data_final.dropna(), final_params)
            
            # config.json für stbot erstellen
            config_output = {
                "market": {"symbol": best_overall_info['symbol'], "timeframe": best_overall_info['timeframe']},
                "strategy": {k: v for k, v in final_params.items() if k in ['stoch_rsi_period', 'stoch_k', 'stoch_d', 'swing_lookback', 'oversold_level', 'overbought_level', 'atr_period', 'trend_filter', 'sideways_filter']},
                "risk": {k: v for k, v in final_params.items() if k in ['margin_mode', 'balance_fraction_pct', 'sl_buffer_pct', 'base_leverage', 'max_leverage', 'target_atr_pct']},
                "behavior": {"use_longs": True, "use_shorts": True}
            }

        else: # mbot
            final_params = {**best_overall_info['params'], **final_params_tuned, 'start_capital': START_CAPITAL, 'leverage': 5.0}
            data_final = calculate_macd_forecast_indicators(HISTORICAL_DATA.copy(), final_params)
            final_result = run_mbot_backtest(data_final.dropna(), final_params)

            # config.json für mbot erstellen
            config_output = {
                "market": {"symbol": best_overall_info['symbol'], "timeframe": best_overall_info['timeframe']},
                "strategy": {k: v for k, v in final_params.items() if k in ['fast_len', 'slow_len', 'signal_len', 'trend_determination', 'max_memory', 'forecast_len', 'upper_percentile', 'mid_percentile', 'lower_percentile']},
                "risk": {k: v for k, v in final_params.items() if k in ['margin_mode', 'balance_fraction_pct', 'sl_buffer_pct', 'leverage']},
                "behavior": {"use_longs": True, "use_shorts": True}
            }

        print(f"  BOT-TYP: {final_bot_type.upper()} | COIN: {best_overall_info['symbol']} | TIMEFRAME: {best_overall_info['timeframe']}")
        print(f"  PERFORMANCE-SCORE: {best_overall_score:.2f} (PnL, gewichtet mit Drawdown)")
        print("\n  FINALE PERFORMANCE-METRIKEN:")
        print(f"    - Gesamtgewinn (PnL): {final_result['total_pnl_pct']:.2f} %")
        print(f"    - Max. Drawdown:      {final_result['max_drawdown_pct']*100:.2f} %")
        print(f"    - Anzahl Trades:      {final_result['trades_count']}")
        print(f"    - Win-Rate:           {final_result['win_rate']:.2f} %")
        
        print(f"\n  >>> EINSTELLUNGEN FÜR DEINE '{final_bot_type}/config.json' <<<")
        print(json.dumps(config_output, indent=4))
        print("\n" + "="*80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 2: Lokale Parameter-Verfeinerung.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--trials', type=int, default=100, help='Anzahl der Versuche pro Kandidat.')
    args = parser.parse_args()
    main(n_jobs=args.jobs, n_trials=args.trials)
