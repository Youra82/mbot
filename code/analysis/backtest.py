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

def load_data(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), '..', 'analysis', 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    
    # mbot benötigt ca. 200 Tage Vordaten für ein gutes "Gedächtnis"
    required_days_before = 200
    download_start_dt = pd.to_datetime(start_date_str) - timedelta(days=required_days_before)
    
    if os.path.exists(cache_file):
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)
        # Prüfen, ob der Cache genügend Daten enthält
        if data.index.min() <= download_start_dt.tz_localize('UTC') and data.index.max() >= pd.to_datetime(end_date_str).tz_localize('UTC'):
            # Gesamten benötigten Zeitraum zurückgeben
            return data.loc[download_start_dt.strftime('%Y-%m-%d'):end_date_str]

    # Daten herunterladen, wenn Cache nicht ausreicht
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets.get('envelope', secrets.get('bitget_example'))
        bitget = BitgetFutures(api_setup)
        
        download_start_str = download_start_dt.strftime('%Y-%m-%d')
        download_end_str = (pd.to_datetime(end_date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
        
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start_str, download_end_str)
        if full_data is not None and not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data.loc[download_start_dt.strftime('%Y-%m-%d'):end_date_str]
        else: return pd.DataFrame()
    except Exception as e:
        print(f"Fehler beim Daten-Download für {timeframe}: {e}"); return pd.DataFrame()

def run_backtest(data, params):
    start_date = params.get("start_date_str")
    data_for_backtest = data.loc[start_date:]

    leverage = params.get('leverage', 5.0)
    balance_fraction = params.get('balance_fraction_pct', 100) / 100
    fee_pct = 0.05 / 100
    start_capital = params.get('start_capital', 1000)
    sl_buffer_pct = params.get('sl_buffer_pct', 0.5) / 100

    current_capital = start_capital
    trades_count, wins_count = 0, 0
    trade_log = []
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    position = None

    for i in range(1, len(data_for_backtest)):
        prev_candle = data_for_backtest.iloc[i-1]
        current_candle = data_for_backtest.iloc[i]

        if position:
            exit_price, reason = None, None
            
            if position['side'] == 'long' and current_candle['low'] <= position['sl_price']:
                exit_price, reason = position['sl_price'], "Stop-Loss"
            elif position['side'] == 'short' and current_candle['high'] >= position['sl_price']:
                exit_price, reason = position['sl_price'], "Stop-Loss"
            
            if not exit_price:
                if position['side'] == 'long' and not pd.isna(current_candle['upper_forecast']) and current_candle['high'] >= current_candle['upper_forecast']:
                    exit_price, reason = current_candle['upper_forecast'], "Take-Profit (Forecast)"
                elif position['side'] == 'short' and not pd.isna(current_candle['lower_forecast']) and current_candle['low'] <= current_candle['lower_forecast']:
                    exit_price, reason = current_candle['lower_forecast'], "Take-Profit (Forecast)"

            if exit_price is not None:
                pnl = (exit_price - position['entry_price']) * position['amount'] if position['side'] == 'long' else (position['entry_price'] - exit_price) * position['amount']
                notional_value = position['entry_price'] * position['amount'] + exit_price * position['amount']
                pnl -= notional_value * fee_pct
                
                current_capital += pnl
                trades_count += 1
                if pnl > 0: wins_count += 1
                
                trade_log.append({
                    "timestamp": str(current_candle.name), "side": position['side'], "entry": position['entry_price'],
                    "exit": exit_price, "pnl": pnl, "balance": current_capital, "reason": reason, "leverage": leverage
                })
                position = None

                if current_capital <= 0: current_capital = 0
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital == 0: break

        if not position:
            long_signal = prev_candle['macd'] < prev_candle['signal'] and current_candle['macd'] > current_candle['signal']
            short_signal = prev_candle['macd'] > prev_candle['signal'] and current_candle['macd'] < current_candle['signal']

            side = None
            if long_signal:
                side = 'long'
                entry_price = current_candle['close']
                sl_price = prev_candle['swing_low'] * (1 - sl_buffer_pct)
            elif short_signal:
                side = 'short'
                entry_price = current_candle['close']
                sl_price = prev_candle['swing_high'] * (1 + sl_buffer_pct)

            if side:
                amount = (current_capital * balance_fraction * leverage) / entry_price
                position = {'side': side, 'entry_price': entry_price, 'amount': amount, 'sl_price': sl_price}

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital / start_capital) - 1) * 100
    
    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count, "win_rate": win_rate,
        "params": params, "end_capital": current_capital, "max_drawdown_pct": max_drawdown_pct, "trade_log": trade_log
    }
