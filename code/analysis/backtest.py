# code/analysis/backtest.py for mbot

import os
import sys
import json
import pandas as pd
import warnings
from datetime import timedelta

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_mbot_indicators

def load_data(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), '..', 'analysis', 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    required_start = pd.to_datetime(start_date_str, utc=True)
    required_end = pd.to_datetime(end_date_str, utc=True)

    if os.path.exists(cache_file):
        print(f"Lade Daten für {symbol} aus dem Cache...")
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)
        
        if not data.empty and data.index.min() <= required_start and data.index.max() >= required_end:
            print("Cache ist ausreichend aktuell. Verwende Cache-Daten.")
            return data.loc[start_date_str:end_date_str]
        else:
            print("Cache ist unvollständig oder veraltet. Starte neuen Download.")

    try:
        print(f"\033[94mVersuche, historische Daten für {symbol} ({timeframe}) von der Börse herunterzuladen...\033[0m")
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets.get('mbot', secrets.get('bitget_example'))
        bitget = BitgetFutures(api_setup)
        
        download_start = (required_start - timedelta(days=50)).strftime('%Y-%m-%d')
        download_end = (required_end + timedelta(days=1)).strftime('%Y-%m-%d')
        
        full_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start, download_end)
        
        if full_data is not None and not full_data.empty:
            print(f"\033[92mDownload erfolgreich! {len(full_data)} Kerzen erhalten. Speichere im Cache...\033[0m")
            full_data.to_csv(cache_file)
            return full_data.loc[start_date_str:end_date_str]
        else:
            print(f"\033[91mFEHLER: Es konnten keine Daten für {symbol} heruntergeladen werden.\033[0m")
            pd.DataFrame().to_csv(cache_file)
            return pd.DataFrame()
            
    except Exception as e:
        print(f"\033[91mEin kritischer Fehler ist beim Daten-Download aufgetreten: {e}\033[0m")
        return pd.DataFrame()

def run_mbot_backtest(data, params):
    risk = params.get('risk', {})
    forecast = params.get('forecast', {})
    base_leverage = risk.get('base_leverage', 5)
    balance_fraction = risk.get('balance_fraction_pct', 100) / 100
    sl_buffer_pct = risk.get('sl_buffer_pct', 0.5) / 100
    tp_atr_multiplier = forecast.get('tp_atr_multiplier', 4.0)
    start_capital = params.get('start_capital', 1000)
    fee_pct = 0.05 / 100
    current_capital = start_capital
    trades_count, wins_count = 0, 0
    trade_log = []
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    position = None

    for i in range(1, len(data)):
        prev_candle, current_candle = data.iloc[i-1], data.iloc[i]

        if position:
            exit_price, reason = None, None
            if position['side'] == 'long' and current_candle['low'] <= position['sl_price']: exit_price, reason = position['sl_price'], "Stop-Loss"
            elif position['side'] == 'short' and current_candle['high'] >= position['sl_price']: exit_price, reason = position['sl_price'], "Stop-Loss"
            elif position['side'] == 'long' and current_candle['high'] >= position['tp_price']: exit_price, reason = position['tp_price'], "Take-Profit"
            elif position['side'] == 'short' and current_candle['low'] <= position['tp_price']: exit_price, reason = position['tp_price'], "Take-Profit"

            if exit_price is not None:
                pnl = (exit_price - position['entry_price']) * position['amount'] if position['side'] == 'long' else (position['entry_price'] - exit_price) * position['amount']
                notional_value = position['entry_price'] * position['amount'] + exit_price * position['amount']
                pnl -= notional_value * fee_pct
                current_capital += pnl
                trades_count += 1
                if pnl > 0: wins_count += 1
                trade_log.append({"timestamp": str(current_candle.name), "side": position['side'], "pnl": pnl, "balance": current_capital, "reason": reason, "leverage": position['leverage']})
                position = None
                if current_capital <= 0: current_capital = 0
                peak_capital = max(peak_capital, current_capital)
                drawdown = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)
                if current_capital == 0: break
        
        if not position:
            long_entry = (current_candle['impulse_histo'] > 0 and prev_candle['impulse_histo'] <= 0 and current_candle['impulse_macd'] > 0 and current_candle['macd_uptrend'] == 1)
            short_entry = (current_candle['impulse_histo'] < 0 and prev_candle['impulse_histo'] >= 0 and current_candle['impulse_macd'] < 0 and current_candle['macd_uptrend'] == 0)
            trade_side = 'long' if long_entry else 'short' if short_entry else None

            if trade_side:
                entry_price = current_candle['close']
                amount = (current_capital * balance_fraction * base_leverage) / entry_price
                if trade_side == 'long':
                    sl_price, tp_price = prev_candle['swing_low'] * (1 - sl_buffer_pct), entry_price + current_candle['tp_atr_distance']
                else:
                    sl_price, tp_price = prev_candle['swing_high'] * (1 + sl_buffer_pct), entry_price - current_candle['tp_atr_distance']
                position = {'side': trade_side, 'entry_price': entry_price, 'amount': amount, 'sl_price': sl_price, 'tp_price': tp_price, 'leverage': base_leverage}
    
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital / start_capital) - 1) * 100
    return {"total_pnl_pct": final_pnl_pct, "trades_count": trades_count, "win_rate": win_rate, "params": params, "end_capital": current_capital, "max_drawdown_pct": max_drawdown_pct, "trade_log": trade_log}
