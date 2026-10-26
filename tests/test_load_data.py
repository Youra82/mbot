# tests/test_load_data.py
# Kerzen-Download: ueberlappend paginiert (keine verlorene Kerze an Seitengrenzen), 429 mit Retry,
# dauerhafte Fehler -> keine Daten statt still abgeschnittener Historie.

import os
import sys
from unittest.mock import MagicMock, patch

import ccxt
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from mbot.analysis.backtester import load_data
from mbot.utils.exchange import Exchange

H = 3600 * 1000
T0 = int(pd.Timestamp('2026-01-01', tz='UTC').timestamp() * 1000)


def _fake_bitget(n_total, fail_first=0, fail_always=False):
    """Bitget-Attrappe: Seiten zu 200 Kerzen ab `since` (inklusive), optional mit 429-Fehlern."""
    calls = {'n': 0}

    def fetch_ohlcv(symbol, tf, since, limit):
        calls['n'] += 1
        if fail_always or calls['n'] <= fail_first:
            raise ccxt.RateLimitExceeded('429 Too Many Requests')
        start = max(0, (since - T0) // H)
        return [[T0 + i * H, 1, 2, 0.5, 1.5, 10] for i in range(start, min(start + limit, n_total))]

    ex = MagicMock()
    ex.exchange.parse8601.side_effect = lambda s: int(pd.Timestamp(s).timestamp() * 1000)
    ex.exchange.parse_timeframe.return_value = 3600
    ex.exchange.milliseconds.return_value = T0 + 10_000 * H
    ex.exchange.rateLimit = 0
    ex.exchange.fetch_ohlcv.side_effect = fetch_ohlcv
    return ex


@patch('time.sleep', lambda s: None)
def test_load_data_complete_over_page_boundaries():
    ex = _fake_bitget(n_total=650)
    df = load_data(ex, 'SOL/USDT:USDT', '1h', '2026-01-01', '2026-02-15')
    assert len(df) == 650
    assert (df.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()


@patch('time.sleep', lambda s: None)
def test_load_data_retries_on_429():
    ex = _fake_bitget(n_total=450, fail_first=3)
    df = load_data(ex, 'SOL/USDT:USDT', '1h', '2026-01-01', '2026-02-15')
    assert len(df) == 450


@patch('time.sleep', lambda s: None)
def test_load_data_persistent_error_returns_empty_not_truncated():
    ex = _fake_bitget(n_total=450, fail_always=True)
    df = load_data(ex, 'SOL/USDT:USDT', '1h', '2026-01-01', '2026-02-15')
    assert df.empty


@patch('time.sleep', lambda s: None)
def test_fetch_recent_ohlcv_no_lost_candle():
    fake = _fake_bitget(n_total=10_000)
    ex = Exchange.__new__(Exchange)
    ex.exchange = fake.exchange
    ex.markets = {'SOL/USDT:USDT': {}}
    df = ex.fetch_recent_ohlcv('SOL/USDT:USDT', '1h', limit=200)
    assert len(df) == 200
    assert (df.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
