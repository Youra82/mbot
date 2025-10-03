# code/strategies/envelope/run.py

import os
import sys
import json
import logging
import pandas as pd
import traceback
import sqlite3
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_stochrsi_indicators
from utilities.telegram_handler import send_telegram_message

# --- SETUP ---
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'stbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('stbot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f: return json.load(f)

params = load_config()
SYMBOL = params['market']['symbol']

# --- CORE LOGIC ---
def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v5.0 - Final)")
    
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
            # Wir machen trotzdem weiter, da der nächste Schritt den Zustand korrigiert

        # --- PHASE 2: DATEN LADEN & ZUSTAND PRÜFEN ---
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        open_position = bitget.fetch_open_positions(SYMBOL)
        open_position = open_position[0] if open_position else None

        # --- PHASE 3: ZUSTAND VERWALTEN ODER NEUEN TRADE SUCHEN ---
        if open_position:
            logger.info(f"Position ({open_position['side']}) gefunden. Platziere/Aktualisiere Stop-Loss...")
            
            # Platziere IMMER einen neuen, korrekten SL
            sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
            if open_position['side'] == 'long':
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
            else:
                sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
            
            bitget.place_stop_order(SYMBOL, sl_side, float(open_position['contracts']), sl_price)
            logger.info("✅ Stop-Loss erfolgreich platziert/aktualisiert.")

            # Prüfe auf Take-Profit
            logger.info("Prüfe auf Take-Profit-Signal...")
            oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
            if (open_position['side'] == 'long' and current_candle['%k'] > overbought) or \
               (open_position['side'] == 'short' and current_candle['%k'] < oversold):
                
                logger.info("🟢 Take-Profit-Signal erkannt. Schließe Position und räume auf...")
                bitget.create_market_order(SYMBOL, sl_side, float(open_position['contracts']), 0, open_position['marginMode'], params={'reduceOnly': True})
                time.sleep(2)
                # Nach dem Schließen nochmals alle verbleibenden Trigger-Orders löschen
                remaining_triggers = bitget.fetch_open_trigger_orders(SYMBOL)
                for order in remaining_triggers:
                    bitget.cancel_trigger_order(order['id'], SYMBOL)
                
                send_telegram_message(bot_token, chat_id, f"✅ Position *{SYMBOL}* ({open_position['side']}) durch Take-Profit geschlossen.")
            else:
                logger.info("Kein Take-Profit-Signal.")

        else: # Keine Position offen
            logger.info("Keine Position offen. Suche nach neuem Einstieg...")
            # ... (Ihre bestehende, funktionierende Logik zum Eröffnen eines neuen Trades) ...
            
    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im stbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
