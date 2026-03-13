# src/mbot/analysis/optimizer.py
"""
mbot MERS Parameter Optimizer (Optuna)

Optimiert die MERS Signal-Parameter fuer jedes Symbol/Timeframe-Paar.
Die Risiko-Parameter (Hebel 20x, Margin-Mode) sind fest vorgegeben.
SL/TP werden ATR-basiert durch atr_sl_mult / atr_tp_mult gesteuert.

Optimierte Parameter:
  risk_per_trade_pct   : Anteil des Kapitals pro Trade (1-100%)
  leverage             : Hebel pro Trade (1-30x)
  entropy_window       : Rollierendes Fenster fuer Shannon Entropy (10-60)
  entropy_lookback     : Rueckschau fuer Entropy-Vergleich (3-25)
  energy_lookback      : Rueckschau fuer Energie-Vergleich (3-25)
  min_entropy_drop_pct : Minimaler Entropy-Abfall fuer Signal (0.01-0.35)
  min_energy_rise_pct  : Minimaler Energie-Anstieg fuer Signal (0.05-1.50)
  atr_period           : ATR-Periode (7-28)
  atr_sl_mult          : SL = entry +/- atr_sl_mult * ATR (0.5-3.0)
  atr_tp_mult          : TP = entry +/- atr_tp_mult * ATR (1.0-6.0, immer > atr_sl_mult)

Gespeicherte Config-Datei: src/mbot/strategy/configs/config_BTCUSDTUSDT_15m_mers.json
"""

import os
import sys
import json
import optuna
import argparse
import logging
from datetime import datetime as _dt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.analysis.backtester import load_data, run_backtest
from mbot.utils.exchange import Exchange

optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Globale State fuer Optuna-Objective
HISTORICAL_DATA         = None
CURRENT_SYMBOL          = None
CURRENT_TIMEFRAME       = None
RISK_CONFIG             = {}
START_CAPITAL           = 1000.0
MAX_DRAWDOWN_CONSTRAINT = 0.30
MIN_WIN_RATE_CONSTRAINT = 50.0
MIN_PNL_CONSTRAINT      = 0.0
MIN_TRADES_CONSTRAINT   = 10
OPTIM_MODE              = 'strict'

RESULTS_FILE = os.path.join(PROJECT_ROOT, 'artifacts', 'results', 'last_optimizer_run.json')


def create_safe_filename(symbol: str, timeframe: str) -> str:
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"


def objective(trial):
    """Optuna-Zielfunktion: maximiert PnL% unter den konfigurierten Constraints."""
    # --- Risiko-Parameter ---
    risk_per_trade_pct = trial.suggest_float('risk_per_trade_pct', 1.0, 100.0, step=1.0)
    leverage           = trial.suggest_int(  'leverage',           1,   30)

    # --- Kern-MERS Parameter ---
    entropy_window   = trial.suggest_int(  'entropy_window',       10,  60)
    entropy_lookback = trial.suggest_int(  'entropy_lookback',      3,  25)
    energy_lookback  = trial.suggest_int(  'energy_lookback',       3,  25)
    min_entropy_drop = trial.suggest_float('min_entropy_drop_pct', 0.01, 0.35, step=0.01)
    min_energy_rise  = trial.suggest_float('min_energy_rise_pct',  0.05, 1.50, step=0.05)
    atr_period       = trial.suggest_int(  'atr_period',            7,  28)
    atr_sl_mult      = trial.suggest_float('atr_sl_mult',          0.5,  3.0, step=0.25)
    # TP muss groesser als SL sein (sonst kein positives R:R)
    atr_tp_mult      = trial.suggest_float('atr_tp_mult',
                                            max(atr_sl_mult + 0.5, 1.0), 6.0, step=0.25)

    # --- MDEF Regime-Check Parameter ---
    use_regime_filter = trial.suggest_int('use_regime_filter', 0, 1)
    regime_window     = trial.suggest_int('regime_window',     10, 40)
    allow_range_trade = trial.suggest_int('allow_range_trade',  0,  1)

    # --- MDEF Multi-Timeframe Parameter ---
    use_multitf_filter = trial.suggest_int('use_multitf_filter', 0, 1)
    meso_tf_mult       = trial.suggest_int('meso_tf_mult',       2,  8)
    macro_tf_mult      = trial.suggest_int('macro_tf_mult',      8, 32)

    signal_config = {
        'risk_per_trade_pct':   risk_per_trade_pct,
        'leverage':             leverage,
        'entropy_window':       entropy_window,
        'entropy_lookback':     entropy_lookback,
        'energy_lookback':      energy_lookback,
        'min_entropy_drop_pct': min_entropy_drop,
        'min_energy_rise_pct':  min_energy_rise,
        'atr_period':           atr_period,
        'atr_sl_mult':          atr_sl_mult,
        'atr_tp_mult':          atr_tp_mult,
        'use_regime_filter':    use_regime_filter,
        'regime_window':        regime_window,
        'allow_range_trade':    allow_range_trade,
        'use_multitf_filter':   use_multitf_filter,
        'meso_tf_mult':         meso_tf_mult,
        'macro_tf_mult':        macro_tf_mult,
    }

    result = run_backtest(
        HISTORICAL_DATA.copy(),
        signal_config,
        RISK_CONFIG,
        start_capital=START_CAPITAL,
        symbol=CURRENT_SYMBOL,
    )

    pnl      = result.get('total_pnl_pct', -9999.0)
    drawdown = result.get('max_drawdown',  100.0)
    win_rate = result.get('win_rate',        0.0)
    trades   = result.get('total_trades',      0)

    if OPTIM_MODE == 'strict':
        if (drawdown > MAX_DRAWDOWN_CONSTRAINT * 100
                or win_rate < MIN_WIN_RATE_CONSTRAINT
                or pnl < MIN_PNL_CONSTRAINT
                or trades < MIN_TRADES_CONSTRAINT):
            raise optuna.exceptions.TrialPruned()
    elif OPTIM_MODE == 'best_profit':
        if trades < MIN_TRADES_CONSTRAINT or drawdown > MAX_DRAWDOWN_CONSTRAINT * 100:
            raise optuna.exceptions.TrialPruned()
        return pnl

    return pnl


def main():
    global HISTORICAL_DATA, CURRENT_SYMBOL, CURRENT_TIMEFRAME, RISK_CONFIG
    global START_CAPITAL, MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT
    global MIN_PNL_CONSTRAINT, MIN_TRADES_CONSTRAINT, OPTIM_MODE

    parser = argparse.ArgumentParser(description='mbot MERS Parameter Optimizer')
    parser.add_argument('--symbols',       type=str, required=True,
                        help='Space-getrennte Coins, z.B. "BTC ETH"')
    parser.add_argument('--timeframes',    type=str, required=True,
                        help='Space-getrennte Timeframes, z.B. "15m 1h"')
    parser.add_argument('--start_date',    type=str, required=True)
    parser.add_argument('--end_date',      type=str, required=True)
    parser.add_argument('--start_capital', type=float, default=1000.0)
    parser.add_argument('--trials',        type=int,   default=200)
    parser.add_argument('--jobs',          type=int,   default=1,
                        help='Parallele Optuna-Jobs')
    parser.add_argument('--max_drawdown',  type=float, default=30.0,
                        help='Maximaler Drawdown in % (z.B. 30)')
    parser.add_argument('--min_win_rate',  type=float, default=50.0,
                        help='Minimale Win-Rate in % (z.B. 50)')
    parser.add_argument('--min_pnl',       type=float, default=0.0,
                        help='Minimaler PnL in % (z.B. 0)')
    parser.add_argument('--min_trades',    type=int,   default=10,
                        help='Minimale Anzahl Trades fuer gueltigen Trial (z.B. 5)')
    parser.add_argument('--mode',          type=str,   default='strict',
                        choices=['strict', 'best_profit'])
    args = parser.parse_args()

    MAX_DRAWDOWN_CONSTRAINT = args.max_drawdown / 100.0
    MIN_WIN_RATE_CONSTRAINT = args.min_win_rate
    MIN_PNL_CONSTRAINT      = args.min_pnl
    MIN_TRADES_CONSTRAINT   = args.min_trades
    START_CAPITAL           = args.start_capital
    OPTIM_MODE              = args.mode

    with open(os.path.join(PROJECT_ROOT, 'settings.json'), 'r') as f:
        settings = json.load(f)
    with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
        secrets = json.load(f)

    RISK_CONFIG = settings.get('risk', {})

    accounts = secrets.get('mbot', [])
    if not accounts:
        logger.critical("Keine 'mbot'-Accounts in secret.json.")
        sys.exit(1)

    exchange = Exchange(accounts[0])
    if not exchange.markets:
        logger.critical("Exchange konnte nicht verbunden werden.")
        sys.exit(1)

    symbols    = args.symbols.split()
    timeframes = args.timeframes.split()
    tasks      = [
        {'symbol': f"{s}/USDT:USDT" if '/' not in s else s, 'timeframe': tf}
        for s in symbols for tf in timeframes
    ]

    run_results = {
        'run_start': _dt.now().isoformat(timespec='seconds'),
        'run_end':   None,
        'saved':     [],
        'failed':    [],
    }

    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
    os.makedirs(configs_dir, exist_ok=True)
    db_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'db')
    os.makedirs(db_dir, exist_ok=True)

    for task in tasks:
        CURRENT_SYMBOL    = task['symbol']
        CURRENT_TIMEFRAME = task['timeframe']
        safe_name         = create_safe_filename(CURRENT_SYMBOL, CURRENT_TIMEFRAME)

        print(f"\n===== MERS Optimierung: {CURRENT_SYMBOL} ({CURRENT_TIMEFRAME}) =====")
        print(f"  Modus: {OPTIM_MODE} | Trials: {args.trials} | Kapital: {START_CAPITAL} USDT")
        print(f"  Constraints: MaxDD={args.max_drawdown}% | MinWR={args.min_win_rate}% | MinPnL={args.min_pnl}%")

        HISTORICAL_DATA = load_data(exchange, CURRENT_SYMBOL, CURRENT_TIMEFRAME,
                                     args.start_date, args.end_date)
        if HISTORICAL_DATA is None or HISTORICAL_DATA.empty:
            logger.warning(f"  Keine Daten fuer {CURRENT_SYMBOL} ({CURRENT_TIMEFRAME}). Ueberspringe.")
            run_results['failed'].append({
                'symbol': CURRENT_SYMBOL, 'timeframe': CURRENT_TIMEFRAME,
                'reason': 'no_data',
            })
            continue

        print(f"  {len(HISTORICAL_DATA)} Kerzen geladen.")

        db_file     = os.path.join(db_dir, 'optuna_studies_mbot.db')
        storage_url = f"sqlite:///{db_file}?timeout=60"
        study_name  = f"mers_{safe_name}_{OPTIM_MODE}_mt{MIN_TRADES_CONSTRAINT}_lv"

        study = optuna.create_study(
            storage=storage_url,
            study_name=study_name,
            direction='maximize',
            load_if_exists=True,
        )

        try:
            study.optimize(
                objective,
                n_trials=args.trials,
                n_jobs=args.jobs,
                show_progress_bar=True,
            )
        except Exception as e:
            logger.error(f"Optimizer-Fehler fuer {CURRENT_SYMBOL}: {e}")
            run_results['failed'].append({
                'symbol': CURRENT_SYMBOL, 'timeframe': CURRENT_TIMEFRAME,
                'reason': str(e)[:80],
            })
            continue

        # --- Trial-Statistiken ---
        all_trials     = study.trials
        completed      = [t for t in all_trials if t.state == optuna.trial.TrialState.COMPLETE]
        pruned         = [t for t in all_trials if t.state == optuna.trial.TrialState.PRUNED]
        print(f"  Trials: {len(all_trials)} gesamt | "
              f"{len(completed)} abgeschlossen | "
              f"{len(pruned)} pruned")

        valid_trials = completed
        if not valid_trials:
            hint = ""
            if OPTIM_MODE == 'strict':
                hint = (f"  Tipp: MaxDD ({args.max_drawdown}%) oder MinWR ({args.min_win_rate}%) "
                        f"zu streng, oder zu wenig Trades (min={args.min_trades}). "
                        f"Modus 2 (best_profit) versuchen oder Constraints lockern.")
            else:
                hint = (f"  Tipp: MERS generiert keine Signale fuer {CURRENT_SYMBOL} ({CURRENT_TIMEFRAME}). "
                        f"Kuerzeren Timeframe (z.B. 4h statt 1d) oder mehr Trials versuchen.")
            print(f"  Keine gueltigen Trials gefunden (alle pruned).")
            print(hint)
            run_results['failed'].append({
                'symbol': CURRENT_SYMBOL, 'timeframe': CURRENT_TIMEFRAME,
                'reason': 'no_valid_trials',
            })
            continue

        best_trial  = max(valid_trials, key=lambda t: t.value)
        best_params = best_trial.params
        best_pnl    = best_trial.value

        best_signal_config = {
            'risk_per_trade_pct':   best_params['risk_per_trade_pct'],
            'leverage':             best_params['leverage'],
            'entropy_window':       best_params['entropy_window'],
            'entropy_lookback':     best_params['entropy_lookback'],
            'energy_lookback':      best_params['energy_lookback'],
            'min_entropy_drop_pct': best_params['min_entropy_drop_pct'],
            'min_energy_rise_pct':  best_params['min_energy_rise_pct'],
            'atr_period':           best_params['atr_period'],
            'atr_sl_mult':          best_params['atr_sl_mult'],
            'atr_tp_mult':          best_params['atr_tp_mult'],
            'use_regime_filter':    best_params['use_regime_filter'],
            'regime_window':        best_params['regime_window'],
            'allow_range_trade':    best_params['allow_range_trade'],
            'use_multitf_filter':   best_params['use_multitf_filter'],
            'meso_tf_mult':         best_params['meso_tf_mult'],
            'macro_tf_mult':        best_params['macro_tf_mult'],
        }

        final_result = run_backtest(
            HISTORICAL_DATA.copy(), best_signal_config, RISK_CONFIG,
            start_capital=START_CAPITAL, symbol=CURRENT_SYMBOL,
        )

        # Nur speichern wenn besser als bestehende Config
        config_file  = os.path.join(configs_dir, f'config_{safe_name}_mers.json')
        existing_pnl = None
        if os.path.exists(config_file):
            try:
                with open(config_file) as cf:
                    existing_cfg = json.load(cf)
                existing_pnl = existing_cfg.get('_meta', {}).get('pnl_pct')
            except Exception:
                pass

        if existing_pnl is not None and best_pnl <= existing_pnl:
            print(f"  Bestehende Config besser ({existing_pnl:.2f}% vs {best_pnl:.2f}%) - wird nicht ueberschrieben.")
            run_results['failed'].append({
                'symbol': CURRENT_SYMBOL, 'timeframe': CURRENT_TIMEFRAME,
                'reason': f'existing_better_{existing_pnl:.2f}pct',
            })
            continue

        config_output = {
            'market': {
                'symbol':    CURRENT_SYMBOL,
                'timeframe': CURRENT_TIMEFRAME,
            },
            'signal': best_signal_config,
            '_meta': {
                'strategy':      'MDEF-MERS',
                'pnl_pct':       round(best_pnl, 2),
                'win_rate':      final_result.get('win_rate', 0.0),
                'total_trades':  final_result.get('total_trades', 0),
                'max_drawdown':  final_result.get('max_drawdown', 0.0),
                'start_capital': START_CAPITAL,
                'end_capital':   final_result.get('end_capital', START_CAPITAL),
                'optimized_at':  _dt.now().isoformat(timespec='seconds'),
                'mode':          OPTIM_MODE,
                'start_date':    args.start_date,
                'end_date':      args.end_date,
            },
        }

        with open(config_file, 'w') as f:
            json.dump(config_output, f, indent=4)

        rtp = best_params['risk_per_trade_pct']
        lev = best_params['leverage']
        print(f"\n  [OK] Beste MERS Config gespeichert: config_{safe_name}_mers.json")
        print(f"       PnL: {best_pnl:.2f}% | WR: {final_result.get('win_rate')}% "
              f"| Trades: {final_result.get('total_trades')} "
              f"| MaxDD: {final_result.get('max_drawdown')}%")
        print(f"       leverage={lev}x | risk_per_trade={rtp:.0f}% "
              f"entropy_window={best_params['entropy_window']} "
              f"entropy_lookback={best_params['entropy_lookback']} "
              f"energy_lookback={best_params['energy_lookback']}")
        print(f"       min_entropy_drop={best_params['min_entropy_drop_pct']:.2f} "
              f"min_energy_rise={best_params['min_energy_rise_pct']:.2f} "
              f"atr_period={best_params['atr_period']}")
        print(f"       atr_sl_mult={best_params['atr_sl_mult']:.2f} "
              f"atr_tp_mult={best_params['atr_tp_mult']:.2f} "
              f"(R:R = 1:{best_params['atr_tp_mult']/best_params['atr_sl_mult']:.1f})")
        print(f"       regime_filter={bool(best_params['use_regime_filter'])} "
              f"regime_window={best_params['regime_window']} "
              f"allow_range={bool(best_params['allow_range_trade'])}")
        print(f"       multitf_filter={bool(best_params['use_multitf_filter'])} "
              f"meso_mult={best_params['meso_tf_mult']} "
              f"macro_mult={best_params['macro_tf_mult']}")

        run_results['saved'].append({
            'symbol':      CURRENT_SYMBOL,
            'timeframe':   CURRENT_TIMEFRAME,
            'pnl_pct':     round(best_pnl, 2),
            'config_file': f'config_{safe_name}_mers.json',
        })

    run_results['run_end'] = _dt.now().isoformat(timespec='seconds')
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(run_results, f, indent=2, ensure_ascii=False)

    print(f"\n===== MERS Optimierung abgeschlossen =====")
    print(f"  Gespeichert: {len(run_results['saved'])}  |  Fehlgeschlagen: {len(run_results['failed'])}")


if __name__ == '__main__':
    main()
