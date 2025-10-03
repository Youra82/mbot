# code/strategies/macd/run.py

import os
import sys
import json
import logging
import pandas as pd
import traceback
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_macd_indicators
from utilities.telegram_handler import send_telegram_message

# --- SETUP ---
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'mbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('mbot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f: return json.load(f)

params = load_config()
SYMBOL = params['market']['symbol']

# --- CORE LOGIC ---
def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (mbot v1.0 - MACD)")
    
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets['envelope'] 
        telegram_config = secrets.get('telegram', {})
        bot_token = telegram_config.get('bot_token')
        chat_id = telegram_config.get('chat_id')
    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel: {e}"); sys.exit(1)

    bitget = BitgetFutures(api_setup)
    
    try:
        # --- PHASE 1: RADIKALES AUFRÄUMEN ZUERST ---
        logger.info("Starte Aufräum-Routine: Lösche alle alten Stop-Loss-Orders...")
        try:
            trigger_orders = bitget.fetch_open_trigger_orders(SYMBOL)
            if trigger_orders:
                for order in trigger_orders:
                    bitget.cancel_trigger_order(order['id'], SYMBOL)
                    logger.info(f"Alte SL-Order {order['id']} gelöscht.")
            else:
                logger.info("Keine alten SL-Orders zum Löschen gefunden.")
        except Exception as e:
            logger.error(f"Fehler beim Aufräumen alter SL-Orders: {e}")

        # --- PHASE 2: DATEN LADEN & ZUSTAND PRÜFEN ---
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_macd_indicators(data, params['strategy'])
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        open_position = bitget.fetch_open_positions(SYMBOL)
        open_position = open_position[0] if open_position else None

        # --- PHASE 3: ZUSTAND VERWALTEN ODER NEUEN TRADE SUCHEN ---
        if open_position:
            logger.info(f"Position ({open_position['side']}) gefunden. Platziere/Aktualisiere Stop-Loss...")
            
            sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
            if open_position['side'] == 'long':
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
            else:
                sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
            
            bitget.place_stop_order(SYMBOL, sl_side, float(open_position['contracts']), sl_price)
            logger.info("✅ Stop-Loss erfolgreich platziert/aktualisiert.")

            logger.info("Prüfe auf Take-Profit-Signal (MACD-Gegenkreuzung)...")
            long_tp_signal = prev_candle['macd'] > prev_candle['macd_signal'] and current_candle['macd'] < current_candle['macd_signal']
            short_tp_signal = prev_candle['macd'] < prev_candle['macd_signal'] and current_candle['macd'] > current_candle['macd_signal']

            if (open_position['side'] == 'long' and long_tp_signal) or \
               (open_position['side'] == 'short' and short_tp_signal):
                
                logger.info("🟢 Take-Profit-Signal erkannt. Schließe Position...")
                bitget.create_market_order(SYMBOL, sl_side, float(open_position['contracts']), 0, open_position['marginMode'], params={'reduceOnly': True})
                time.sleep(2)
                
                remaining_triggers = bitget.fetch_open_trigger_orders(SYMBOL)
                for order in remaining_triggers:
                    bitget.cancel_trigger_order(order['id'], SYMBOL)
                
                send_telegram_message(bot_token, chat_id, f"✅ Position *{SYMBOL}* ({open_position['side']}) durch Take-Profit (MACD) geschlossen.")
            else:
                logger.info("Kein Take-Profit-Signal.")

        else: # Keine Position offen
            logger.info("Keine Position offen. Suche nach neuem Einstieg...")
            
            trend_allows_long = current_candle['close'] > current_candle['ema_trend'] if params['strategy']['trend_filter']['enabled'] else True
            trend_allows_short = current_candle['close'] < current_candle['ema_trend'] if params['strategy']['trend_filter']['enabled'] else True
            
            long_entry_signal = trend_allows_long and prev_candle['macd'] < prev_candle['macd_signal'] and current_candle['macd'] > current_candle['macd_signal']
            short_entry_signal = trend_allows_short and prev_candle['macd'] > prev_candle['macd_signal'] and current_candle['macd'] < current_candle['macd_signal']

            trade_side = None
            if long_entry_signal and params['behavior']['use_longs']:
                trade_side = 'buy'
                logger.info("📈 LONG-Einstiegssignal (MACD) gefunden. Bereite Trade vor...")
            elif short_entry_signal and params['behavior']['use_shorts']:
                trade_side = 'sell'
                logger.info("📉 SHORT-Einstiegssignal (MACD) gefunden. Bereite Trade vor...")

            if trade_side:
                try:
                    balance_info = bitget.fetch_balance()
                    usdt_balance = balance_info['USDT']['free']
                    
                    if usdt_balance < 10: # Mindestguthaben-Check
                        logger.warning(f"Nicht genügend Guthaben ({usdt_balance:.2f} USDT) für neuen Trade.")
                        return

                    leverage = params['risk']['base_leverage']
                    if pd.notna(current_candle['atr_pct']) and current_candle['atr_pct'] > 0:
                        leverage = params['risk']['base_leverage'] * (params['risk']['target_atr_pct'] / current_candle['atr_pct'])
                    leverage = int(round(max(1.0, min(leverage, params['risk']['max_leverage']))))

                    trade_capital = usdt_balance * (params['risk']['balance_fraction_pct'] / 100)
                    position_size_usd = trade_capital * leverage
                    position_size_contracts = position_size_usd / current_candle['close']

                    logger.info(f"Eröffne {trade_side.upper()}-Position: {position_size_contracts:.4f} Contracts für {SYMBOL} mit Hebel {leverage}x")
                    
                    bitget.create_market_order(
                        SYMBOL, 
                        trade_side, 
                        position_size_contracts, 
                        leverage, 
                        params['risk']['margin_mode']
                    )
                    
                    send_telegram_message(bot_token, chat_id, f"🚀 Neue Position eröffnet: *{SYMBOL}* ({trade_side.upper()}) @ {current_candle['close']} mit Hebel {leverage}x")

                except Exception as e:
                    logger.error(f"Fehler beim Eröffnen der Position: {e}", exc_info=True)
                    send_telegram_message(bot_token, chat_id, f"🔥 FEHLER beim Trade für *{SYMBOL}*! Konnte Position nicht eröffnen. Grund: `{e}`")

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im mbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
