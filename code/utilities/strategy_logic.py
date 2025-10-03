import pandas as pd
import ta
import numpy as np

def calculate_stochrsi_indicators(data, params):
    # ... (komplette stbot-Funktion bleibt hier unverändert) ...
    rsi_period = params.get('stoch_rsi_period', 14)
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


def calculate_macd_forecast_indicators(data, params):
    fast = params.get('fast_len', 12)
    slow = params.get('slow_len', 26)
    sigLen = params.get('signal_len', 9)
    trend_determination = params.get('trend_determination', 'MACD - Signal')
    max_memory = params.get('max_memory', 50)
    swing_lookback = params.get('swing_lookback', 20)

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
    
    memory = {1: {}, 0: {}}
    
    trend_init_price = 0
    trend_start_index = 0
    current_trend = None

    # Daten ab dem Start des Backtest-Zeitraums, aber Gedächtnis mit allen Daten aufbauen
    backtest_start_index = max(0, len(data) - (len(data.loc[params.get('start_date_str', data.index[0].strftime('%Y-%m-%d')):])))

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
        if i < backtest_start_index: continue

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
                    indicators.iat[i + step, indicators.columns.get_loc('upper_forecast')] = init_price + upper_dev
                    indicators.iat[i + step, indicators.columns.get_loc('mid_forecast')] = init_price + mid_dev
                    indicators.iat[i + step, indicators.columns.get_loc('lower_forecast')] = init_price + lower_dev

    indicators[['upper_forecast', 'mid_forecast', 'lower_forecast']] = indicators[['upper_forecast', 'mid_forecast', 'lower_forecast']].ffill()

    # Gib nur den relevanten Zeitraum für den Backtest zurück
    return data.join(indicators).iloc[backtest_start_index:]
