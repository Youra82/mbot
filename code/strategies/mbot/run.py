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
from utilities.strategy_logic import calculate_macd_forecast_indicators
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
    logger.info(f">>> Starte mbot Ausführung für {SYMBOL} (MACD Forecast Strategy)")
    
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
        # --- PHASE 1: AUFRÄUMEN ---
        logger.info("Starte Aufräum-Routine: Lösche alte Stop-Loss-Orders...")
        try:
            trigger_orders = bitget.fetch_open_orders(SYMBOL, params={'planType': 'profit_loss'})
            if trigger_orders:
                for order in trigger_orders:
                    bitget.cancel_order(order['id'], SYMBOL)
                    logger.info(f"Alte SL/TP-Order {order['id']} gelöscht.")
            else:
                logger.info("Keine alten Trigger-Orders zum Löschen gefunden.")
        except Exception as e:
            logger.error(f"Fehler beim Aufräumen alter Orders: {e}")

        # --- PHASE 2: DATEN LADEN & INDIKATOREN BERECHNEN ---
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 1000)
        all_params = {**params['strategy'], **params['risk'], **params.get('addons', {})}
        data = calculate_macd_forecast_indicators(data, all_params)
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        open_position = bitget.fetch_open_positions(SYMBOL)
        open_position = open_position[0] if open_position else None

        # --- PHASE 3: ZUSTANDS-MANAGEMENT ---
        if open_position:
            logger.info(f"Position ({open_position['side']}) gefunden. Verwalte Take-Profit und Stop-Loss...")
            
            sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
            
            tp_hit = False
            if open_position['side'] == 'long' and not pd.isna(current_candle['upper_forecast']):
                if current_candle['high'] >= current_candle['upper_forecast']:
                    tp_hit = True
                    logger.info(f"🟢 Take-Profit-Signal für LONG erkannt. Preis hat obere Prognose erreicht.")
            elif open_position['side'] == 'short' and not pd.isna(current_candle['lower_forecast']):
                if current_candle['low'] <= current_candle['lower_forecast']:
                    tp_hit = True
                    logger.info(f"🟢 Take-Profit-Signal für SHORT erkannt. Preis hat untere Prognose erreicht.")

            if tp_hit:
                bitget.create_market_order(SYMBOL, sl_side, float(open_position['contracts']), 0, open_position['marginMode'], params={'reduceOnly': True})
                send_telegram_message(bot_token, chat_id, f"✅ mbot: Position *{SYMBOL}* ({open_position['side']}) durch Take-Profit geschlossen.")
                time.sleep(2)
                remaining_orders = bitget.fetch_open_orders(SYMBOL, params={'planType': 'profit_loss'})
                for order in remaining_orders:
                    bitget.cancel_order(order['id'], SYMBOL)
        
        else: # Keine Position offen
            logger.info("Keine Position offen. Suche nach neuem Einstieg...")
            
            long_signal = prev_candle['macd'] < prev_candle['signal'] and current_candle['macd'] > current_candle['signal']
            short_signal = prev_candle['macd'] > prev_candle['signal'] and current_candle['macd'] < current_candle['signal']

            impulse_cfg = params.get('addons', {}).get('impulse_macd_filter', {})
            if impulse_cfg.get('enabled', False):
                if 'impulse_md' not in current_candle:
                    logger.warning("Impulse MACD Filter ist aktiviert, aber 'impulse_md' Spalte nicht gefunden. Überspringe Filter.")
                else:
                    logger.info("Prüfe Signal mit Impulse MACD Filter...")
                    original_long, original_short = long_signal, short_signal
                    long_signal = long_signal and (current_candle['impulse_md'] > 0)
                    short_signal = short_signal and (current_candle['impulse_md'] < 0)
                    if (original_long and not long_signal) or (original_short and not short_signal):
                        logger.info("Signal wurde durch Impulse MACD Filter blockiert.")

            side = None
            if long_signal and params['behavior']['use_longs']:
                side = 'buy'
            elif short_signal and params['behavior']['use_shorts']:
                side = 'sell'
            
            if side:
                logger.info(f"🔥 NEUES EINSTIEGSSIGNAL: {side.upper()} für {SYMBOL} erkannt.")
                balance_info = bitget.fetch_balance()
                usdt_balance = balance_info['USDT']['free']
                leverage = params['risk']['leverage']
                amount_in_usdt = usdt_balance * (params['risk']['balance_fraction_pct'] / 100)
                amount_in_contracts = (amount_in_usdt * leverage) / current_candle['close']
                
                bitget.create_market_order(SYMBOL, side, amount_in_contracts, leverage, params['risk']['margin_mode'])
                send_telegram_message(bot_token, chat_id, f"🔥 mbot: Eröffne *{side.upper()}*-Position für *{SYMBOL}*.")
                time.sleep(2)

                new_position = bitget.fetch_open_positions(SYMBOL)
                if new_position:
                    new_position = new_position[0]
                    sl_side = 'sell' if new_position['side'] == 'long' else 'buy'
                    if new_position['side'] == 'long':
                        sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
                    else:
                        sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
                    bitget.place_stop_order(SYMBOL, sl_side, float(new_position['contracts']), sl_price)
                    logger.info(f"✅ Stop-Loss für die neue {new_position['side']}-Position platziert bei {sl_price:.4f}.")

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im mbot Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im mbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< mbot Ausführung abgeschlossen\n")
