# code/analysis/local_refiner_optuna.py for mbot

import json, os, sys, argparse, optuna, numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import run_mbot_backtest, load_data
from utilities.strategy_logic import calculate_mbot_indicators

optuna.logging.set_verbosity(optuna.logging.WARNING)
HISTORICAL_DATA, START_CAPITAL, BASE_PARAMS = None, 1000.0, {}

def objective(trial):
    base_p = BASE_PARAMS['params']
    params = {
        'impulse_macd': {
            'length_ma': trial.suggest_int('length_ma', max(15, base_p['impulse_macd']['length_ma'] - 10), base_p['impulse_macd']['length_ma'] + 10),
            'length_signal': trial.suggest_int('length_signal', max(3, base_p['impulse_macd']['length_signal'] - 3), base_p['impulse_macd']['length_signal'] + 3),
        },
        'risk': {
            'tp_atr_multiplier': trial.suggest_float('tp_atr_multiplier', base_p['risk']['tp_atr_multiplier'] * 0.7, base_p['risk']['tp_atr_multiplier'] * 1.3),
            'sl_buffer_pct': trial.suggest_float('sl_buffer_pct', base_p['risk']['sl_buffer_pct'] * 0.5, base_p['risk']['sl_buffer_pct'] * 1.5, log=True),
            'swing_lookback': trial.suggest_int('swing_lookback', max(5, base_p['risk']['swing_lookback'] - 10), base_p['risk']['swing_lookback'] + 10),
            'base_leverage': trial.suggest_int('base_leverage', max(1, base_p['risk']['base_leverage'] - 5), base_p['risk']['base_leverage'] + 5),
            'balance_fraction_pct': 10, 'atr_period': 14
        }, 'start_capital': START_CAPITAL
    }
    data_with_indicators = calculate_mbot_indicators(HISTORICAL_DATA.copy(), params)
    result = run_mbot_backtest(data_with_indicators.dropna(), params)
    pnl, drawdown = result.get('total_pnl_pct', -1000), result.get('max_drawdown_pct', 1.0)
    score = pnl * (1 - drawdown) if result['trades_count'] > 0 else -1000
    return score if np.isfinite(score) else -float('inf')

def main(n_jobs, n_trials):
    print("\n--- [Stufe 2/2] Lokale Verfeinerung für mbot mit Optuna ---")
    try:
        with open(os.path.join(os.path.dirname(__file__), 'optimization_candidates.json'), 'r') as f: candidates = json.load(f)
        print(f"Lade {len(candidates)} Kandidaten zur Verfeinerung...")
    except FileNotFoundError:
        print("Fehler: Kandidaten-Datei nicht gefunden."); return
    if not candidates: return

    best_overall_trial, best_overall_score, best_overall_info = None, -float('inf'), {}
    for i, candidate in enumerate(candidates):
        print(f"\n===== Verfeinere Kandidat {i+1}/{len(candidates)} für {candidate['symbol']} ({candidate['timeframe']}) =====")
        global HISTORICAL_DATA, BASE_PARAMS, START_CAPITAL
        HISTORICAL_DATA, BASE_PARAMS, START_CAPITAL = load_data(candidate['symbol'], candidate['timeframe'], candidate['start_date'], candidate['end_date']), candidate, candidate['start_capital']
        if HISTORICAL_DATA.empty: continue
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)
        if study.best_value > best_overall_score:
            best_overall_score, best_overall_trial, best_overall_info = study.best_value, study.best_trial, candidate

    if best_overall_trial:
        print("\n\n" + "="*80 + "\n     +++ FINALES BESTES ERGEBNIS +++\n" + "="*80)
        final_params = {
            'impulse_macd': { 'length_ma': best_overall_trial.params['length_ma'], 'length_signal': best_overall_trial.params['length_signal'] },
            'risk': {
                'margin_mode': "isolated", 'balance_fraction_pct': 10, 'max_leverage': 20, 'atr_period': 14,
                'tp_atr_multiplier': round(best_overall_trial.params['tp_atr_multiplier'], 2),
                'sl_buffer_pct': round(best_overall_trial.params['sl_buffer_pct'], 2),
                'swing_lookback': best_overall_trial.params['swing_lookback'],
                'base_leverage': best_overall_trial.params['base_leverage']
            }
        }
        final_data = load_data(best_overall_info['symbol'], best_overall_info['timeframe'], best_overall_info['start_date'], best_overall_info['end_date'])
        data_with_indicators = calculate_mbot_indicators(final_data.copy(), {**final_params, 'start_capital': best_overall_info['start_capital']})
        final_result = run_mbot_backtest(data_with_indicators.dropna(), {**final_params, 'start_capital': best_overall_info['start_capital']})

        print(f"  HANDELSCOIN: {best_overall_info['symbol']} | TIMEFRAME: {best_overall_info['timeframe']}")
        print(f"  PERFORMANCE-SCORE: {best_overall_score:.2f} (PnL, gewichtet mit Drawdown)")
        print("\n  FINALE PERFORMANCE-METRIKEN:")
        print(f"    - Gesamtgewinn (PnL): {final_result['total_pnl_pct']:.2f} % \n    - Max. Drawdown:      {final_result['max_drawdown_pct']*100:.2f} % \n    - Anzahl Trades:      {final_result['trades_count']} \n    - Win-Rate:           {final_result['win_rate']:.2f} %")
        
        print("\n  >>> EINSTELLUNGEN FÜR DEINE 'config.json' <<<")
        config_output = {"market": {"symbol": best_overall_info['symbol'], "timeframe": best_overall_info['timeframe']}, **final_params, "behavior": {"use_longs": True, "use_shorts": True}}
        print(json.dumps(config_output, indent=4) + "\n" + "="*80)
    else:
        print("Kein gültiges Ergebnis nach der Verfeinerung gefunden.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stufe 2: Lokale Parameter-Verfeinerung für mbot.")
    parser.add_argument('--jobs', type=int, default=1, help='Anzahl der CPU-Kerne.')
    parser.add_argument('--trials', type=int, default=200, help='Anzahl der Versuche pro Kandidat.')
    main(n_jobs=parser.parse_args().jobs, n_trials=parser.parse_args().trials)
