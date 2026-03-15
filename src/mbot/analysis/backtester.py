# src/mbot/analysis/backtester.py
"""
mbot Backtester (MDEF-MERS)

Simuliert das MERS-Signal auf historischen Kerzen und berechnet:
- Anzahl Trades (Long/Short)
- Win-Rate
- Gesamt-PnL (USDT und %)
- Max Drawdown
- Bestes/Schlechtestes Trade-Ergebnis
- Trade-Liste

SL/TP: ATR-basiert (aus MERS signal['sl_price'] / signal['tp_price'])
Exit:   Intra-candle SL/TP-Pruefung via High/Low (Hard Stop)

Optimierung: Features werden einmal auf dem gesamten DataFrame vorberechnet
(O(N) statt O(N^2)) anstatt get_mers_signal() fuer jede Kerze aufzurufen.
"""

import os
import sys
import logging
import time
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.strategy.mdef_analysis import (
    calc_log_returns,
    calc_rolling_entropy,
    calc_velocity,
    calc_acceleration,
    calc_energy,
    calc_atr,
    classify_phase_regime,
    calc_multitf_alignment,
)

logger = logging.getLogger(__name__)

# Mindest-Kerzen bevor erstes Signal berechnet werden kann
# entropy_window (max 60) + entropy_lookback (max 20) + atr_period (max 21) + buffer
MIN_CANDLES = 110


def load_data(exchange_instance, symbol: str, timeframe: str,
              start_date: str, end_date: str) -> pd.DataFrame:
    """
    Laedt historische OHLCV-Daten von Bitget.
    Benoetigt eine Exchange-Instanz.
    """
    logger.info(f"Lade Daten: {symbol} ({timeframe}) | {start_date} -> {end_date}")
    if not hasattr(exchange_instance, 'exchange'):
        logger.error("Ungueltige Exchange-Instanz uebergeben.")
        return pd.DataFrame()

    start_ts = int(exchange_instance.exchange.parse8601(start_date + 'T00:00:00Z'))
    end_ts   = int(exchange_instance.exchange.parse8601(end_date   + 'T23:59:59Z'))
    tf_ms    = exchange_instance.exchange.parse_timeframe(timeframe) * 1000

    all_ohlcv = []
    current   = start_ts

    while current < end_ts:
        try:
            chunk = exchange_instance.exchange.fetch_ohlcv(symbol, timeframe, current, 200)
            if not chunk:
                break
            chunk = [c for c in chunk if c[0] <= end_ts]
            if not chunk:
                break
            all_ohlcv.extend(chunk)
            current = chunk[-1][0] + tf_ms
            time.sleep(exchange_instance.exchange.rateLimit / 1000)
        except Exception as e:
            logger.error(f"Fehler beim Laden der Daten: {e}")
            break

    if not all_ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep='last')]
    logger.info(f"  -> {len(df)} Kerzen geladen.")
    return df


def run_backtest(df: pd.DataFrame, signal_config: dict, risk_config: dict,
                  start_capital: float = 1000.0, symbol: str = '') -> dict:
    """
    Fuehrt den MERS-Backtest durch.

    Optimierung: Alle Features (Entropy, ATR, Velocity, Acc, Energy) werden
    einmal auf dem gesamten DataFrame vorberechnet (O(N)). Danach werden im
    Loop nur Werte per Index nachgeschlagen, statt get_mers_signal() fuer
    jede Kerze neu aufzurufen (was O(N^2) waere).

    Returns dict mit allen Ergebnissen.
    """
    risk_per_trade_pct = float(signal_config.get('risk_per_trade_pct',
                               risk_config.get('risk_per_trade_pct', 1.0)))

    # --- Signal-Parameter auslesen ---
    entropy_window    = int(signal_config.get('entropy_window',       20))
    entropy_lookback  = int(signal_config.get('entropy_lookback',     10))
    energy_lookback   = int(signal_config.get('energy_lookback',       5))
    min_entropy_drop  = float(signal_config.get('min_entropy_drop_pct', 0.05))
    min_energy_rise   = float(signal_config.get('min_energy_rise_pct',  0.20))
    atr_period        = int(signal_config.get('atr_period',            14))
    atr_sl_mult       = float(signal_config.get('atr_sl_mult',          1.5))
    atr_tp_mult       = float(signal_config.get('atr_tp_mult',          3.0))
    use_regime_filter = bool(int(signal_config.get('use_regime_filter',   1)))
    regime_window     = int(signal_config.get('regime_window',          20))
    allow_range_trade = bool(int(signal_config.get('allow_range_trade',   0)))
    use_multitf_filter = bool(int(signal_config.get('use_multitf_filter', 0)))
    meso_tf_mult      = int(signal_config.get('meso_tf_mult',            4))
    macro_tf_mult     = int(signal_config.get('macro_tf_mult',           16))

    if len(df) < MIN_CANDLES + 1:
        return _empty_result(symbol, start_capital)

    # -------------------------------------------------------
    # FEATURE-VORBERECHNUNG (einmalig auf vollem DataFrame)
    # -------------------------------------------------------
    price    = df['close']
    returns  = calc_log_returns(price)
    entropy  = calc_rolling_entropy(returns, window=entropy_window)
    velocity = calc_velocity(price)
    acc      = calc_acceleration(velocity)
    energy   = calc_energy(velocity)
    atr_ser  = calc_atr(df, period=atr_period)

    capital  = start_capital
    trades   = []
    in_trade = False
    trade    = {}

    for i in range(MIN_CANDLES, len(df)):
        current = df.iloc[i]

        # --- Trade-Aufloesung (SL/TP hit check) ---
        if in_trade:
            entry = trade['entry_price']
            side  = trade['side']
            sl_p  = trade['sl_price']
            tp_p  = trade['tp_price']
            hi    = current['high']
            lo    = current['low']

            hit_sl = (side == 'long'  and lo  <= sl_p) or (side == 'short' and hi >= sl_p)
            hit_tp = (side == 'long'  and hi  >= tp_p) or (side == 'short' and lo <= tp_p)

            if hit_sl or hit_tp:
                # Bei gleicher Kerze: SL-Prioritaet (konservativ)
                if hit_sl and hit_tp:
                    result = 'loss'
                    exit_p = sl_p
                elif hit_tp:
                    result = 'win'
                    exit_p = tp_p
                else:
                    result = 'loss'
                    exit_p = sl_p

                # Risiko-basierte Positionsgroesse (wie dnabot)
                sl_distance = abs(entry - trade['sl_price'])
                risk_amount = capital * risk_per_trade_pct / 100.0
                if sl_distance > 0:
                    pos_contracts = risk_amount / sl_distance
                else:
                    pos_contracts = 0.0

                if side == 'long':
                    pnl_usdt = pos_contracts * (exit_p - entry)
                else:
                    pnl_usdt = pos_contracts * (entry - exit_p)

                pnl_pct  = pnl_usdt / capital * 100 if capital > 0 else 0.0
                capital  = max(capital + pnl_usdt, 0.0)

                idx = df.index[i]
                exit_time = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
                trade.update({
                    'exit_price':         exit_p,
                    'exit_time':          exit_time,
                    'result':             result,
                    'pnl_pct':            round(pnl_pct, 2),
                    'pnl_usdt':           round(pnl_usdt, 2),
                    'capital_after':      round(capital, 2),
                    'risk_per_trade_pct': risk_per_trade_pct,
                })
                trades.append(trade)
                in_trade = False
                trade = {}
            continue  # Wenn in Trade: keine neue Signal-Pruefung

        # -------------------------------------------------------
        # MERS-SIGNAL-PRUEFUNG (vorberechnete Features nutzen)
        # -------------------------------------------------------
        cur_entropy  = entropy.iloc[i]
        cur_energy   = energy.iloc[i]
        cur_acc      = acc.iloc[i]
        cur_atr      = atr_ser.iloc[i]
        entry_price  = float(price.iloc[i])

        # Lookback-Schutz
        if i < entropy_lookback or i < energy_lookback:
            continue
        prev_entropy = entropy.iloc[i - entropy_lookback]
        prev_energy  = energy.iloc[i - energy_lookback]

        # NaN / ungueltige Werte ueberspringen
        if any(np.isnan(v) for v in [cur_entropy, prev_entropy, cur_energy,
                                      prev_energy, cur_acc, cur_atr]):
            continue
        if cur_atr <= 0 or entry_price <= 0:
            continue

        # --- Layer 2: Regime-Check (optional) ---
        if use_regime_filter:
            vel_win = velocity.iloc[max(0, i - regime_window):i + 1]
            acc_win = acc.iloc[max(0, i - regime_window):i + 1]
            regime  = classify_phase_regime(vel_win, acc_win, window=regime_window)
            if regime == 'chaos':
                continue
            if regime == 'range' and not allow_range_trade:
                continue

        # --- Layer 1: Entropy-Bedingung ---
        if prev_entropy <= 0:
            continue
        entropy_drop = (prev_entropy - cur_entropy) / prev_entropy
        if entropy_drop < min_entropy_drop:
            continue

        # --- Layer 1: Energie-Bedingung ---
        if prev_energy <= 0:
            continue
        energy_rise = (cur_energy - prev_energy) / prev_energy
        if energy_rise < min_energy_rise:
            continue

        # --- Layer 1: Richtung via Beschleunigung ---
        if cur_acc > 0:
            side = 'long'
        elif cur_acc < 0:
            side = 'short'
        else:
            continue

        # --- Layer 3: Multi-Timeframe (optional) ---
        if use_multitf_filter:
            window_df = df.iloc[max(0, i - MIN_CANDLES):i + 1]
            mtf = calc_multitf_alignment(window_df,
                                          meso_mult=meso_tf_mult,
                                          macro_mult=macro_tf_mult)
            if not mtf['aligned']:
                continue
            if side == 'long'  and mtf['direction'] < 0:
                continue
            if side == 'short' and mtf['direction'] > 0:
                continue

        # --- SL/TP berechnen (ATR-basiert) ---
        if side == 'long':
            sl_price_abs = entry_price - atr_sl_mult * cur_atr
            tp_price_abs = entry_price + atr_tp_mult * cur_atr
        else:
            sl_price_abs = entry_price + atr_sl_mult * cur_atr
            tp_price_abs = entry_price - atr_tp_mult * cur_atr

        idx = df.index[i]
        entry_time = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
        in_trade = True
        trade = {
            'symbol':      symbol,
            'side':        side,
            'entry_time':  entry_time,
            'entry_price': entry_price,
            'sl_price':    sl_price_abs,
            'tp_price':    tp_price_abs,
            'atr':         round(cur_atr, 6),
        }

    # --- Statistiken ---
    if not trades:
        return _empty_result(symbol, start_capital)

    wins   = sum(1 for t in trades if t['result'] == 'win')
    losses = len(trades) - wins
    pnls   = [t['pnl_pct'] for t in trades]

    # Drawdown
    cap_curve = [start_capital] + [t['capital_after'] for t in trades]
    peak  = cap_curve[0]
    max_dd = 0.0
    for c in cap_curve:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    total_pnl_usdt = capital - start_capital
    total_pnl_pct  = total_pnl_usdt / start_capital * 100 if start_capital > 0 else 0.0

    return {
        'symbol':         symbol,
        'trades':         trades,
        'total_trades':   len(trades),
        'wins':           wins,
        'losses':         losses,
        'win_rate':       round(wins / len(trades) * 100, 1),
        'total_pnl_pct':  round(total_pnl_pct, 2),
        'total_pnl_usdt': round(total_pnl_usdt, 2),
        'max_drawdown':   round(max_dd, 2),
        'best_trade':     round(max(pnls), 2),
        'worst_trade':    round(min(pnls), 2),
        'start_capital':  start_capital,
        'end_capital':    round(capital, 2),
    }


def _empty_result(symbol: str, start_capital: float) -> dict:
    return {
        'symbol':         symbol,
        'trades':         [],
        'total_trades':   0,
        'wins':           0,
        'losses':         0,
        'win_rate':       0.0,
        'total_pnl_pct':  0.0,
        'total_pnl_usdt': 0.0,
        'max_drawdown':   0.0,
        'best_trade':     0.0,
        'worst_trade':    0.0,
        'start_capital':  start_capital,
        'end_capital':    start_capital,
    }
