import pandas as pd
import ta
import numpy as np
from datetime import timedelta

def calculate_macd_forecast_indicators(data, params):
    fast = params.get('fast_len', 12)
    slow = params.get('slow_len', 26)
    sigLen = params.get('signal_len', 9)
    trend_determination = params.get('trend_determination', 'MACD - Signal')
    max_memory = params.get('max_memory', 50)
    swing_lookback = params.get('swing_lookback', 20)

    # Indikatoren auf dem gesamten Datensatz berechnen
    indicators = pd.DataFrame(index=data.index)
    macd_indicator = ta.trend.MACD(close=data['close'], window_slow=slow, window_fast=fast, window_sign=sigLen)
    indicators['macd'] = macd_indicator.macd()
    indicators['signal'] = macd_indicator.macd_signal()
    
    if trend_determination == 'MACD':
        indicators['uptrend'] = (indicators['macd'] > 0)
    else:
        indicators['uptrend'] = (indicators['macd'] > indicators['signal'])
    
    indicators['downtrend'] = ~indicators['uptrend']
    
    indicators['swing_low'] = data['low'].rolling(window=swing_lookback).min()
    indicators['swing_high'] = data['high'].rolling(window=swing_lookback).max()
    
    # Gedächtnis aufbauen
    memory = {1: {}, 0: {}} # 1: uptrends, 0: downtrends
    
    trend_init_price = 0
    trend_start_index = 0
    current_trend = None

    for i in range(1, len(data)):
        prev_is_uptrend = indicators['uptrend'].iloc[i-1]
        curr_is_uptrend = indicators['uptrend'].iloc[i]

        is_new_uptrend = curr_is_uptrend and not prev_is_uptrend
        is_new_downtrend = not curr_is_uptrend and prev_is_uptrend

        if is_new_uptrend:
            current_trend = 1
            trend_init_price = data['close'].iloc[i]
            trend_start_index = i
        elif is_new_downtrend:
            current_trend = 0
            trend_init_price = data['close'].iloc[i]
            trend_start_index = i
        
        if current_trend is not None:
            trend_duration = i - trend_start_index
            deviation = data['close'].iloc[i] - trend_init_price
            
            if trend_duration not in memory[current_trend]:
                memory[current_trend][trend_duration] = []
            
            memory[current_trend][trend_duration].append(deviation)
            
            if len(memory[current_trend][trend_duration]) > max_memory:
                memory[current_trend][trend_duration].pop(0)

    # Prognose berechnen
    forecast_len = params.get('forecast_len', 100)
    up_per = params.get('upper_percentile', 80)
    mid_per = params.get('mid_percentile', 50)
    dn_per = params.get('lower_percentile', 20)
    
    for p in ['upper', 'mid', 'lower']:
        indicators[f'{p}_forecast'] = np.nan

    triggers = (indicators['uptrend'] != indicators['uptrend'].shift(1)).fillna(False)
    trigger_indices = triggers[triggers].index

    for trigger_idx in trigger_indices:
        i = data.index.get_loc(trigger_idx)
        is_uptrend = indicators['uptrend'].iloc[i]
        trend_type = 1 if is_uptrend else 0
        init_price = data['close'].iloc[i]
        
        for step in range(forecast_len):
            future_duration = step
            if future_duration in memory[trend_type] and len(memory[trend_type][future_duration]) > 1:
                historical_deviations = memory[trend_type][future_duration]
                
                upper_dev = np.percentile(historical_deviations, up_per)
                mid_dev = np.percentile(historical_deviations, mid_per)
                lower_dev = np.percentile(historical_deviations, dn_per)
                
                if i + step < len(indicators):
                    # Direkter Zugriff via .iat für Performance
                    indicators.iat[i + step, indicators.columns.get_loc('upper_forecast')] = init_price + upper_dev
                    indicators.iat[i + step, indicators.columns.get_loc('mid_forecast')] = init_price + mid_dev
                    indicators.iat[i + step, indicators.columns.get_loc('lower_forecast')] = init_price + lower_dev

    # Fülle die Prognosewerte für die Dauer des Trends
    indicators[['upper_forecast', 'mid_forecast', 'lower_forecast']] = indicators[['upper_forecast', 'mid_forecast', 'lower_forecast']].ffill()

    return data.join(indicators)
