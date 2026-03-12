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
"""

import os
import sys
import logging
import time
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.strategy.mers_signal import get_mers_signal

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

    Logik:
    - Jede Kerze wird als potentielles MERS-Signal geprueft
    - Signal -> sofortiger Entry zum Close-Kurs dieser Kerze
    - SL/TP werden ATR-basiert aus dem Signal berechnet
    - TP / SL werden auf naechsten Kerzen geprueft (intra-candle via High/Low)
    - Nur ein Trade zur Zeit (kein gleichzeitiger Trade)

    Returns dict mit allen Ergebnissen.
    """
    leverage = int(risk_config.get('leverage', 20))

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

                if side == 'long':
                    pnl_pct = (exit_p - entry) / entry * leverage * 100
                else:
                    pnl_pct = (entry - exit_p) / entry * leverage * 100

                pnl_usdt = capital * pnl_pct / 100
                capital  = max(capital + pnl_usdt, 0.0)

                idx = df.index[i]
                exit_time = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
                trade.update({
                    'exit_price':    exit_p,
                    'exit_time':     exit_time,
                    'result':        result,
                    'pnl_pct':       round(pnl_pct, 2),
                    'pnl_usdt':      round(pnl_usdt, 2),
                    'capital_after': round(capital, 2),
                })
                trades.append(trade)
                in_trade = False
                trade = {}
            continue  # Wenn in Trade: keine neue Signal-Pruefung

        # --- MERS-Signal-Pruefung ---
        window = df.iloc[max(0, i - MIN_CANDLES):i + 1]
        signal = get_mers_signal(window, signal_config)

        if signal['side'] is None:
            continue

        # Entry
        entry_price = float(current['close'])
        side        = signal['side']

        # ATR-basierte SL/TP anhand tatsaechlichem Entry-Preis neu berechnen
        atr         = signal.get('atr', 0)
        atr_sl_mult = signal.get('atr_sl_mult', 1.5)
        atr_tp_mult = signal.get('atr_tp_mult', 3.0)

        if atr and atr > 0:
            if side == 'long':
                sl_price_abs = entry_price - atr_sl_mult * atr
                tp_price_abs = entry_price + atr_tp_mult * atr
            else:
                sl_price_abs = entry_price + atr_sl_mult * atr
                tp_price_abs = entry_price - atr_tp_mult * atr
        else:
            # Fallback: aus Signal (sollte nicht passieren)
            sl_price_abs = signal['sl_price']
            tp_price_abs = signal['tp_price']

        idx = df.index[i]
        entry_time = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
        in_trade = True
        trade = {
            'symbol':        symbol,
            'side':          side,
            'entry_time':    entry_time,
            'entry_price':   entry_price,
            'sl_price':      sl_price_abs,
            'tp_price':      tp_price_abs,
            'atr':           round(atr, 6) if atr else None,
            'entropy_drop':  signal.get('entropy_drop'),
            'energy_rise':   signal.get('energy_rise'),
            'reason':        signal.get('reason', ''),
        }

    # --- Statistiken ---
    if not trades:
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
