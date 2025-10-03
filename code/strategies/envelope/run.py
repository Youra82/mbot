# code/strategies/envelope/run.py for mbot

import os
import sys
import json
import logging
import traceback
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_mbot_indicators
from utilities.telegram_handler import send_telegram_message

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
TIMEFRAME = params['market']['timeframe']

def main():
    logger.info(f">>> Starte mbot Ausführung für {SYMBOL} (v1.0 - Impulse & Forecast)")
    
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets['mbot']
        telegram_config = secrets.get('telegram', {})
        bot_token = telegram_config.get('bot_token')
        chat_id = telegram_config.get('chat_id')
    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel: {e}"); sys.exit(1)

    bitget = BitgetFutures(api_setup)
    
    try:
        logger.info("Starte Aufräum-Routine: Lösche alle offenen Orders...")
        try:
            bitget.cancel_all_orders(SYMBOL)
            logger.info("Alle alten Orders erfolgreich gelöscht.")
        except Exception as e:
            logger.error(f"Fehler beim Aufräumen alter Orders: {e}")

        data = bitget.fetch_recent_ohlcv(SYMBOL, TIMEFRAME, 500)
        data = calculate_mbot_indicators(data, params)
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        open_position = bitget.fetch_open_positions(SYMBOL)
        open_position = open_position[0] if open_position else None

        if open_position:
            side = open_position['side']
            entry_price = float(open_position['entryPrice'])
            contracts = float(open_position['contracts'])
            
            logger.info(f"Position ({side}) gefunden. Aktualisiere Stop-Loss und prüfe Take-Profit.")
            
            sl_side = 'sell' if side == 'long' else 'buy'
            if side == 'long':
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
            else:
                sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
            
            bitget.place_stop_order(SYMBOL, sl_side, contracts, sl_price)
            logger.info(f"✅ Stop-Loss für {side}-Position auf {sl_price:.4f} gesetzt.")

            tp_atr_dist = current_candle['tp_atr_distance']
            if side == 'long' and current_candle['high'] >= entry_price + tp_atr_dist:
                logger.info(f"🟢 Take-Profit-Signal für LONG erkannt. Schließe Position.")
                bitget.create_market_order(SYMBOL, 'sell', contracts, 0, 'isolated', params={'reduceOnly': True})
                send_telegram_message(bot_token, chat_id, f"✅ Position *{SYMBOL}* ({side}) durch Take-Profit geschlossen.")
            elif side == 'short' and current_candle['low'] <= entry_price - tp_atr_dist:
                logger.info(f"🟢 Take-Profit-Signal für SHORT erkannt. Schließe Position.")
                bitget.create_market_order(SYMBOL, 'buy', contracts, 0, 'isolated', params={'reduceOnly': True})
                send_telegram_message(bot_token, chat_id, f"✅ Position *{SYMBOL}* ({side}) durch Take-Profit geschlossen.")
            else:
                logger.info("Kein Take-Profit-Signal.")
        else:
            logger.info("Keine Position offen. Suche nach neuem Einstieg...")
            
            long_entry_signal = (
                current_candle['impulse_histo'] > 0 and prev_candle['impulse_histo'] < 0 and
                current_candle['impulse_macd'] > 0 and
                current_candle['macd_uptrend'] == 1
            )
            short_entry_signal = (
                current_candle['impulse_histo'] < 0 and prev_candle['impulse_histo'] > 0 and
                current_candle['impulse_macd'] < 0 and
                current_candle['macd_uptrend'] == 0
            )

            trade_side = None
            if long_entry_signal and params['behavior']['use_longs']:
                trade_side = 'long'
            elif short_entry_signal and params['behavior']['use_shorts']:
                trade_side = 'short'

            if trade_side:
                logger.info(f"🚀 {trade_side.upper()}-Einstiegssignal erkannt! Eröffne Trade...")
                balance_info = bitget.fetch_balance()
                usdt_balance = float(balance_info['USDT']['free'])
                leverage = params['risk']['base_leverage']
                entry_price = current_candle['close']
                trade_capital = usdt_balance * (params['risk']['balance_fraction_pct'] / 100)
                amount = (trade_capital * leverage) / entry_price
                
                bitget.create_market_order(SYMBOL, trade_side, amount, leverage, params['risk']['margin_mode'])
                logger.info(f"✅ {trade_side.upper()}-Order platziert für {amount:.4f} {SYMBOL.split('/')[0]}.")
                send_telegram_message(bot_token, chat_id, f"🚀 Neue Position eröffnet: *{SYMBOL}* ({trade_side.upper()})")
            else:
                logger.info("Kein gültiges Einstiegssignal gefunden.")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler im mbot Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im mbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< mbot Ausführung abgeschlossen\n")
