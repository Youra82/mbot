# code/utilities/bitget_futures.py

import ccxt
import logging
import pandas as pd
from datetime import datetime, timezone

logger = logging.getLogger('mbot')

class BitgetFutures:
    def __init__(self, api_setup=None):
        if api_setup:
            self.session = ccxt.bitget({
                'apiKey': api_setup['apiKey'],
                'secret': api_setup['secret'],
                'password': api_setup['password'],
                'options': { 'defaultType': 'swap' },
            })
        else:
            self.session = ccxt.bitget({'options': { 'defaultType': 'swap' }})
        self.session.load_markets()

    def fetch_balance(self):
        try:
            return self.session.fetch_balance()
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Guthabens: {e}")
            raise

    def fetch_recent_ohlcv(self, symbol, timeframe, limit):
        try:
            ohlcv = self.session.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Fehler beim Laden der Kerzendaten: {e}")
            raise

    def fetch_historical_ohlcv(self, symbol: str, timeframe: str, start_date_str: str, end_date_str: str) -> pd.DataFrame:
        try:
            start_ts = int(pd.to_datetime(start_date_str, utc=True).timestamp() * 1000)
            end_ts = int(pd.to_datetime(end_date_str, utc=True).timestamp() * 1000)
            all_ohlcv = []
            limit = 1000
            while start_ts < end_ts:
                ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since=start_ts, limit=limit)
                if not ohlcv: break
                all_ohlcv.extend(ohlcv)
                last_timestamp = ohlcv[-1][0]
                if last_timestamp >= end_ts: break
                start_ts = last_timestamp + 1
            if not all_ohlcv: return pd.DataFrame()
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df = df[(df['timestamp'] >= pd.to_datetime(start_date_str, utc=True)) & (df['timestamp'] <= pd.to_datetime(end_date_str, utc=True))]
            df.set_index('timestamp', inplace=True)
            df = df[~df.index.duplicated(keep='first')]
            df.sort_index(inplace=True)
            return df
        except Exception as e:
            logger.error(f"Fehler beim Laden historischer Daten: {e}")
            raise

    def fetch_open_positions(self, symbol: str):
        try:
            all_positions = self.session.fetch_positions([symbol])
            return [p for p in all_positions if p.get('contracts') is not None and float(p['contracts']) > 0]
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der offenen Positionen: {e}")
            raise

    def place_stop_order(self, symbol: str, side: str, amount: float, stop_price: float):
        try:
            params = {'stopPrice': self.session.price_to_precision(symbol, stop_price), 'reduceOnly': True}
            return self.session.create_order(symbol, 'market', side, amount, params=params)
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Stop-Order: {e}")
            raise

    def create_market_order(self, symbol: str, side: str, amount: float, leverage: int, margin_mode: str, params={}):
        try:
            order_params = params.copy()
            order_params['marginMode'] = margin_mode.lower()
            if leverage > 0: self.session.set_leverage(leverage, symbol)
            return self.session.create_order(symbol, 'market', side, amount, params=order_params)
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Market-Order: {e}")
            raise

    def cancel_all_orders(self, symbol: str):
        try:
            return self.session.cancel_all_orders(symbol)
        except Exception as e:
            logger.error(f"Fehler beim Löschen aller Orders für {symbol}: {e}")
            raise
