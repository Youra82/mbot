# code/utilities/strategy_logic.py

import pandas as pd
import ta

def calculate_macd_indicators(data, params):
    """
    Berechnet MACD, Swing Points, ATR, EMA-Trendfilter.
    """
    # MACD Parameter
    fast_period = params.get('macd_fast', 12)
    slow_period = params.get('macd_slow', 26)
    signal_period = params.get('macd_signal', 9)

    # Andere Parameter
    swing_lookback = params.get('swing_lookback', 10)
    atr_period = params.get('atr_period', 14)
    trend_filter_cfg = params.get('trend_filter', {})
    trend_filter_period = trend_filter_cfg.get('period', 200)

    indicators = pd.DataFrame(index=data.index)

    # MACD-Berechnung
    macd_indicator = ta.trend.MACD(
        close=data['close'],
        window_slow=slow_period,
        window_fast=fast_period,
        window_sign=signal_period
    )
    indicators['macd'] = macd_indicator.macd()
    indicators['macd_signal'] = macd_indicator.macd_signal()
    indicators['macd_diff'] = macd_indicator.macd_diff() # Histogramm

    # Beibehaltung der nützlichen Zusatzindikatoren
    indicators['swing_low'] = data['low'].rolling(window=swing_lookback).min()
    indicators['swing_high'] = data['high'].rolling(window=swing_lookback).max()
    
    atr = ta.volatility.AverageTrueRange(data['high'], data['low'], data['close'], window=atr_period).average_true_range()
    indicators['atr_pct'] = (atr / data['close']) * 100

    indicators['ema_trend'] = ta.trend.ema_indicator(data['close'], window=trend_filter_period)

    return data.join(indicators)
