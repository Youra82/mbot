# code/utilities/strategy_logic.py

import pandas as pd
import pandas_ta as ta
import numpy as np

# Hilfsfunktionen zur Berechnung der Indikatoren aus dem Impulse MACD Skript
def _calc_smma(series: pd.Series, length: int) -> pd.Series:
    """ Berechnet den Smoothed Moving Average (SMMA). """
    return series.ewm(alpha=1/length, adjust=False).mean()

def _calc_zlema(series: pd.Series, length: int) -> pd.Series:
    """ Berechnet den Zero-Lag Exponential Moving Average (ZLEMA). """
    ema1 = series.ewm(span=length, adjust=False).mean()
    ema2 = ema1.ewm(span=length, adjust=False).mean()
    d = ema1 - ema2
    return ema1 + d

def calculate_mbot_indicators(data: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Berechnet alle für den mbot notwendigen Indikatoren.
    Kombiniert die Logik aus "Impulse MACD" und "MACD Based Price Forecasting".
    """
    # Parameter aus der Konfiguration extrahieren
    macd_params = params.get('macd', {})
    impulse_params = params.get('impulse_macd', {})
    forecast_params = params.get('forecast', {})
    
    # --- 1. Standard MACD Berechnungen (aus LuxAlgo Skript) ---
    fast = macd_params.get('fast', 12)
    slow = macd_params.get('slow', 26)
    signal = macd_params.get('signal', 9)
    
    macd_df = ta.macd(data['close'], fast=fast, slow=slow, signal=signal)
    data['macd'] = macd_df[f'MACD_{fast}_{slow}_{signal}']
    data['macd_signal'] = macd_df[f'MACDs_{fast}_{slow}_{signal}']
    data['macd_uptrend'] = (data['macd'] > data['macd_signal']).astype(int)
    
    # --- 2. Impulse MACD Berechnungen (aus LazyBear Skript) ---
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

    # --- 3. Zusätzliche Indikatoren für die Strategie (SL, ATR) ---
    risk_params = params.get('risk', {})
    swing_lookback = risk_params.get('swing_lookback', 30)
    data['swing_low'] = data['low'].rolling(window=swing_lookback).min()
    data['swing_high'] = data['high'].rolling(window=swing_lookback).max()
    
    atr_period = macd_params.get('atr_period', 14)
    atr = ta.atr(data['high'], data['low'], data['close'], length=atr_period)
    data['atr_pct'] = (atr / data['close']) * 100

    # --- 4. Preisprognose-Logik (vereinfacht für Backtesting) ---
    # Hier simulieren wir einen ATR-basierten Take-Profit, der in der Optimierung
    # angepasst werden kann, um die Forecast-Idee abzubilden.
    
    tp_atr_multiplier = forecast_params.get('tp_atr_multiplier', 3.0)
    data['tp_atr_distance'] = ta.atr(data['high'], data['low'], data['close'], length=atr_period) * tp_atr_multiplier

    return data
