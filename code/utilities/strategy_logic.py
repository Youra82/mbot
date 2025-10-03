
# code/utilities/strategy_logic.py

import pandas as pd
import pandas_ta as ta
import numpy as np

def _calc_smma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1/length, adjust=False).mean()

def _calc_zlema(series: pd.Series, length: int) -> pd.Series:
    ema1 = series.ewm(span=length, adjust=False).mean()
    ema2 = ema1.ewm(span=length, adjust=False).mean()
    d = ema1 - ema2
    return ema1 + d

def calculate_mbot_indicators(data: pd.DataFrame, params: dict) -> pd.DataFrame:
    macd_params = params.get('macd', {})
    impulse_params = params.get('impulse_macd', {})
    forecast_params = params.get('forecast', {})
    
    fast, slow, signal = macd_params.get('fast', 12), macd_params.get('slow', 26), macd_params.get('signal', 9)
    macd_df = ta.macd(data['close'], fast=fast, slow=slow, signal=signal)
    data['macd'] = macd_df[f'MACD_{fast}_{slow}_{signal}']
    data['macd_signal'] = macd_df[f'MACDs_{fast}_{slow}_{signal}']
    data['macd_uptrend'] = (data['macd'] > data['macd_signal']).astype(int)
    
    length_ma, length_signal = impulse_params.get('length_ma', 34), impulse_params.get('length_signal', 9)
    src = (data['high'] + data['low'] + data['close']) / 3
    hi, lo, mi = _calc_smma(data['high'], length_ma), _calc_smma(data['low'], length_ma), _calc_zlema(src, length_ma)
    md = np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0))
    data['impulse_macd'] = md
    data['impulse_signal'] = pd.Series(md).rolling(window=length_signal).mean()
    data['impulse_histo'] = data['impulse_macd'] - data['impulse_signal']

    risk_params = params.get('risk', {})
    swing_lookback = risk_params.get('swing_lookback', 30)
    data['swing_low'] = data['low'].rolling(window=swing_lookback).min()
    data['swing_high'] = data['high'].rolling(window=swing_lookback).max()
    
    atr_period = macd_params.get('atr_period', 14)
    atr = ta.atr(data['high'], data['low'], data['close'], length=atr_period)
    data['atr_pct'] = (atr / data['close']) * 100
    
    tp_atr_multiplier = forecast_params.get('tp_atr_multiplier', 3.0)
    data['tp_atr_distance'] = ta.atr(data['high'], data['low'], data['close'], length=atr_period) * tp_atr_multiplier
    return data
