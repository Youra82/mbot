# src/mbot/strategy/momentum_logic.py
"""
Momentum Signal Logic fuer mbot.

Signal: N-Bar Breakout
  LONG:  close > max(high der letzten N Kerzen) UND bullische Kerze UND sauberer Koerper
  SHORT: close < min(low  der letzten N Kerzen) UND baerische  Kerze UND sauberer Koerper

Parameter (nur 2):
  breakout_period — wie viele Kerzen zurueckschauen (Referenz-Hoch/Tief)
  min_body_ratio  — Mindest-Kerzenkoerper (0.5 = 50% der Gesamtrange)
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)


def get_momentum_signal(df: pd.DataFrame, signal_config: dict) -> dict:
    """
    Gibt ein Momentum-Signal zurueck.

    Returns:
        {
            'side':        'long' | 'short' | None,
            'entry_price': float,
            'body_ratio':  float,
            'reason':      str,
        }
    """
    breakout_period = int(signal_config.get('breakout_period', 20))
    min_body_ratio  = float(signal_config.get('min_body_ratio', 0.50))

    result = {'side': None, 'entry_price': 0.0, 'body_ratio': 0.0, 'reason': 'kein Signal'}

    if df is None or len(df) < breakout_period + 2:
        result['reason'] = f'Nicht genug Kerzen ({len(df) if df is not None else 0} < {breakout_period + 2})'
        return result

    current = df.iloc[-1]
    prev    = df.iloc[-breakout_period - 1:-1]  # die N Kerzen VOR der aktuellen

    highest_high = prev['high'].max()
    lowest_low   = prev['low'].min()

    total_range = current['high'] - current['low']
    body        = abs(current['close'] - current['open'])
    body_ratio  = body / total_range if total_range > 1e-10 else 0.0

    result['entry_price'] = float(current['close'])
    result['body_ratio']  = round(body_ratio, 3)

    is_bullish = current['close'] > current['open']
    is_bearish = current['close'] < current['open']

    if is_bullish and current['close'] > highest_high and body_ratio >= min_body_ratio:
        result['side']   = 'long'
        result['reason'] = (
            f"LONG Breakout ({breakout_period}): "
            f"close={current['close']:.4f} > Hoch={highest_high:.4f} | Koerper={body_ratio:.0%}"
        )
        logger.debug(f"LONG Signal: {result['reason']}")

    elif is_bearish and current['close'] < lowest_low and body_ratio >= min_body_ratio:
        result['side']   = 'short'
        result['reason'] = (
            f"SHORT Breakout ({breakout_period}): "
            f"close={current['close']:.4f} < Tief={lowest_low:.4f} | Koerper={body_ratio:.0%}"
        )
        logger.debug(f"SHORT Signal: {result['reason']}")

    else:
        reasons = []
        if not (is_bullish and current['close'] > highest_high) and \
           not (is_bearish and current['close'] < lowest_low):
            reasons.append(f"kein Ausbruch (Hoch={highest_high:.4f}, Tief={lowest_low:.4f})")
        if body_ratio < min_body_ratio:
            reasons.append(f"Koerper zu klein ({body_ratio:.0%} < {min_body_ratio:.0%})")
        result['reason'] = 'kein Signal: ' + ', '.join(reasons) if reasons else 'kein Signal'
        logger.debug(result['reason'])

    return result
