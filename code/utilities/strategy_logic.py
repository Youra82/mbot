# code/utilities/strategy_logic.py

import pandas as pd
import pandas_ta as ta
import numpy as np

# Hilfsfunktionen bleiben gleich
def _calc_smma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1/length, adjust=False).mean()

def _calc_zlema(series: pd.Series, length: int) -> pd.Series:
    ema1 = series.ewm(span=length, adjust=False).mean()
    ema2 = ema1.ewm(span=length, adjust=False).mean()
    d = ema1 - ema2
    return ema1 + d

def calculate_mbot_indicators(data: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Berechnet alle für den mbot notwendigen Indikatoren.
    VEREINFACHT: Verwendet nur noch den Impulse MACD.
    """
    impulse_params = params.get('impulse_macd', {})
    risk_params = params.get('risk', {})
    
    # --- 1. Impulse MACD Berechnungen ---
    length_ma = impulse_params.get('length_ma', 34)
    length_signal = impulse_params.get('length_signal', 9)
    
    src = (data['high'] + data['low'] + data['close']) / 3
    hi = _calc_smma(data['high'], length_ma)
    lo = _calc_smma(data['low'], length_ma)
    mi = _calc_zlema(src, length_ma)
    
    md = np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0))
    data['impulse_macd'] = md
    data['impulse_signal'] = pd.Series(md).rolling(window=length_signal).mean()
    data['impulse_histo'] = data['impulse_macd'] - data['impulse_signal']

    # --- 2. Zusätzliche Indikatoren für SL & TP ---
    swing_lookback = risk_params.get('swing_lookback', 30)
    data['swing_low'] = data['low'].rolling(window=swing_lookback).min()
    data['swing_high'] = data['high'].rolling(window=swing_lookback).max()
    
    atr_period = risk_params.get('atr_period', 14)
    tp_atr_multiplier = risk_params.get('tp_atr_multiplier', 3.0)
    data['tp_atr_distance'] = ta.atr(data['high'], data['low'], data['close'], length=atr_period) * tp_atr_multiplier

    return data
