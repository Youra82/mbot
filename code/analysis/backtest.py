# code/analysis/backtest.py

import os
import sys
import json
import pandas as pd
import numpy as np
import warnings
from datetime import timedelta

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_stochrsi_indicators

def load_data(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), '..', 'analysis', 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    if os.path.exists(cache_file):
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)
        required_start = pd.to_datetime(start_date_str, utc=True)
        if data.index.min() <= required_start and data.index.max() >= pd.to_datetime(end_date_str, utc=True):
            return data.loc[start_date_str:end_date_str]
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets.get('envelope', secrets.get('bitget_example'))
        bitget = BitgetFutures(api_setup)
        download_start = (pd.to_datetime(start_date_str) - timedelta(days=50)).strftime('%Y-%m-%d')
        download_end = (pd.to_datetime(end_date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start, download_end)
        if full_data is not None and not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data.loc[start_date_str:end_date_str]
        else: return pd.DataFrame()
    except Exception as e:
        print(f"Fehler beim Daten-Download für {timeframe}: {e}"); return pd.DataFrame()

def run_stochrsi_backtest(data, params):
    base_leverage = params.get('base_leverage', 10.0)
    target_atr_pct = params.get('target_atr_pct', 1.5)
    max_leverage = params.get('max_leverage', 50.0)
    balance_fraction = params.get('balance_fraction_pct', 100) / 100
    fee_pct = 0.05 / 100
    start_capital = params.get('start_capital', 1000)
    sl_buffer_pct = params.get('sl_buffer_pct', 0.1) / 100
    oversold_level = params.get('oversold_level', 20)
    overbought_level = params.get('overbought_level', 80)
    
    trend_filter_cfg = params.get('trend_filter', {})
    sideways_filter_cfg = params.get('sideways_filter', {})

    current_capital = start_capital
    trades_count, wins_count = 0, 0
    trade_log = []
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    position = None

    for i in range(1, len(data)):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]

        if position:
            exit_price, reason = None, None
            if position['side'] == 'long' and current_candle['low'] <= position['sl_price']:
                exit_price, reason = position['sl_price'], "Stop-Loss"
            elif position['side'] == 'short' and current_candle['high'] >= position['sl_price']:
                exit_price, reason = position['sl_price'], "Stop-Loss"
            
            if not exit_price:
                if position['side'] == 'long' and current_candle['%k'] > overbought_level:
                    exit_price, reason = current_candle['open'], "Take-Profit (Gegenextrem)"
                elif position['side'] == 'short' and current_candle['%k'] < oversold_level:
                    exit_price, reason = current_candle['open'], "Take-Profit (Gegenextrem)"

            if exit_price is not None:
                pnl = (exit_price - position['entry_price']) * position['amount'] if position['side'] == 'long' else (position['entry_price'] - exit_price) * position['amount']
                notional_entry = position['entry_price'] * position['amount']
                notional_exit = exit_price * position['amount']
                pnl -= (notional_entry + notional_exit) * fee_pct
                
                current_capital += pnl
                trades_count += 1
                if reason.startswith("Take-Profit"): wins_count += 1
                
                trade_log.append({
                    "timestamp": str(current_candle.name), "side": position['side'], "entry": position['entry_price'], 
                    "exit": exit_price, "pnl": pnl, "balance": current_capital, "reason": reason, "leverage": position['leverage']
                })
                position = None

                if current_capital <= 0: current_capital = 0
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital == 0: break
        
        if not position:
            trend_allows_long, trend_allows_short, market_is_not_sideways = True, True, True

            if trend_filter_cfg.get('enabled', False) and 'ema_trend' in current_candle and not pd.isna(current_candle['ema_trend']):
                if current_candle['close'] < current_candle['ema_trend']: trend_allows_long = False
                else: trend_allows_short = False
            
            if sideways_filter_cfg.get('enabled', False):
                sideways_max_crosses = sideways_filter_cfg.get('max_crosses', 8)
                if current_candle['sideways_cross_count'] > sideways_max_crosses: market_is_not_sideways = False

            current_atr_pct = current_candle['atr_pct']
            leverage = base_leverage
            if pd.notna(current_atr_pct) and current_atr_pct > 0:
                leverage = base_leverage * (target_atr_pct / current_atr_pct)
            leverage = int(round(max(1.0, min(leverage, max_leverage))))

            if (trend_allows_long and market_is_not_sideways and prev_candle['%k'] < prev_candle['%d'] and 
                current_candle['%k'] > current_candle['%d'] and prev_candle['%k'] < oversold_level):
                entry_price = current_candle['close']
                amount = (current_capital * balance_fraction * leverage) / entry_price
                sl_price = prev_candle['swing_low'] * (1 - sl_buffer_pct)
                position = {'side': 'long', 'entry_price': entry_price, 'amount': amount, 'sl_price': sl_price, 'leverage': leverage}

            elif (trend_allows_short and market_is_not_sideways and prev_candle['%k'] > prev_candle['%d'] and 
                  current_candle['%k'] < current_candle['%d'] and prev_candle['%k'] > overbought_level):
                entry_price = current_candle['close']
                amount = (current_capital * balance_fraction * leverage) / entry_price
                sl_price = prev_candle['swing_high'] * (1 + sl_buffer_pct)
                position = {'side': 'short', 'entry_price': entry_price, 'amount': amount, 'sl_price': sl_price, 'leverage': leverage}

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital / start_capital) - 1) * 100
    
    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count, "win_rate": win_rate, 
        "params": params, "end_capital": current_capital, "max_drawdown_pct": max_drawdown_pct, "trade_log": trade_log
    }
