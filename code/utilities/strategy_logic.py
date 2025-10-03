# code/utilities/strategy_logic.py

import pandas as pd
import ta

def calculate_stochrsi_indicators(data, params):
    """
    Berechnet Stoch RSI, Swing Points, ATR, EMA-Trendfilter und Seitwärts-Filter.
    """
    rsi_period = params.get('stoch_rsi_period', 14)
    # stoch_period = params.get('stoch_period', 14) # --- ENTFERNT ---
    k_period = params.get('stoch_k', 3)
    d_period = params.get('stoch_d', 3)
    swing_lookback = params.get('swing_lookback', 10)
    atr_period = params.get('atr_period', 14)

    trend_filter_cfg = params.get('trend_filter', {})
    sideways_filter_cfg = params.get('sideways_filter', {})
    trend_filter_period = trend_filter_cfg.get('period', 200)
    sideways_lookback = sideways_filter_cfg.get('lookback', 50)

    indicators = pd.DataFrame(index=data.index)

    stoch_rsi = ta.momentum.StochRSIIndicator(
        close=data['close'],
        window=rsi_period,
        # stoch_window=stoch_period, # --- ENTFERNT ---
        smooth1=k_period,
        smooth2=d_period
    )
    indicators['%k'] = stoch_rsi.stochrsi_k() * 100
    indicators['%d'] = stoch_rsi.stochrsi_d() * 100

    indicators['swing_low'] = data['low'].rolling(window=swing_lookback).min()
    indicators['swing_high'] = data['high'].rolling(window=swing_lookback).max()

    atr = ta.volatility.AverageTrueRange(data['high'], data['low'], data['close'], window=atr_period).average_true_range()
    indicators['atr_pct'] = (atr / data['close']) * 100

    indicators['ema_trend'] = ta.trend.ema_indicator(data['close'], window=trend_filter_period)

    cross_up = (indicators['%k'].shift(1) < 50) & (indicators['%k'] >= 50)
    cross_down = (indicators['%k'].shift(1) > 50) & (indicators['%k'] <= 50)
    crosses = cross_up | cross_down
    indicators['sideways_cross_count'] = crosses.rolling(window=sideways_lookback).sum()

    return data.join(indicators)
