# tests/test_workflow.py
"""
mbot Live-Workflow-Test

Testet den kompletten Handelszyklus auf Bitget mit PEPE (kleines Minimum).
Benoetigt secret.json mit gueltigen API-Keys.
"""

import pytest
import os
import sys
import json
import logging
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.exchange import Exchange
from mbot.utils.trade_manager import (
    read_active_positions, write_active_positions, claim_position, clear_position,
    calculate_sl_tp_prices, calculate_contracts,
)
from mbot.utils.telegram import send_message


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope='module')
def test_setup():
    print('\n--- mbot Live-Workflow-Test ---')

    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    if not os.path.exists(secret_path):
        pytest.skip('secret.json nicht gefunden. Ueberspringe Live-Test.')

    with open(secret_path, 'r') as f:
        secrets = json.load(f)

    accounts = secrets.get('mbot', [])
    if not accounts:
        pytest.skip("Keine 'mbot'-Accounts in secret.json. Ueberspringe Live-Test.")

    try:
        exchange = Exchange(accounts[0])
        if not exchange.markets:
            pytest.fail('Exchange konnte nicht initialisiert werden.')
    except Exception as e:
        pytest.fail(f'Exchange-Fehler: {e}')

    test_logger = logging.getLogger('test-mbot')
    test_logger.setLevel(logging.INFO)
    if not test_logger.handlers:
        test_logger.addHandler(logging.StreamHandler(sys.stdout))

    symbol    = 'PEPE/USDT:USDT'  # Kleine Mindestgrösse
    timeframe = '15m'
    leverage  = 20
    margin_mode = 'isolated'

    # Ausgangszustand bereinigen
    print(f'[Setup] Bereinige Ausgangszustand fuer {symbol}...')
    try:
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(1)
        positions = exchange.fetch_open_positions(symbol)
        if positions:
            pos  = positions[0]
            side = 'sell' if pos['side'] == 'long' else 'buy'
            amt  = float(pos.get('contracts') or pos.get('contractSize', 0))
            if amt > 0:
                exchange.place_market_order(symbol, side, amt, reduce=True)
                time.sleep(3)
        print('[Setup] Ausgangszustand ist sauber.')
    except Exception as e:
        pytest.fail(f'Fehler beim Setup-Bereinigen: {e}')

    # Active Positions zuruecksetzen
    write_active_positions([])

    telegram_config = secrets.get('telegram', {})

    yield exchange, symbol, timeframe, leverage, margin_mode, test_logger, telegram_config

    # Teardown
    print('\n[Teardown] Raeume nach dem Test auf...')
    try:
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)
        positions = exchange.fetch_open_positions(symbol)
        if positions:
            pos  = positions[0]
            side = 'sell' if pos['side'] == 'long' else 'buy'
            amt  = float(pos.get('contracts') or pos.get('contractSize', 0))
            if amt > 0:
                exchange.place_market_order(symbol, side, amt, reduce=True)
                time.sleep(3)
        exchange.cancel_all_orders_for_symbol(symbol)
        print('[Teardown] Abgeschlossen.')
    except Exception as e:
        print(f'FEHLER beim Teardown: {e}')
    finally:
        write_active_positions([])


# ============================================================
# Unit Tests (kein API-Zugriff nötig)
# ============================================================

def test_sl_tp_calculation():
    """Prueft SL/TP-Berechnung: 2% Kontoverlust mit 20x = 0.1% Preis"""
    entry    = 100.0
    leverage = 20
    sl_acc   = 2.0
    tp_prc   = 1.0

    sl_long, tp_long = calculate_sl_tp_prices(entry, 'long', leverage, sl_acc, tp_prc)
    assert abs(sl_long - 99.9) < 0.001,  f'SL Long erwartet 99.9, got {sl_long}'
    assert abs(tp_long - 101.0) < 0.001, f'TP Long erwartet 101.0, got {tp_long}'

    sl_short, tp_short = calculate_sl_tp_prices(entry, 'short', leverage, sl_acc, tp_prc)
    assert abs(sl_short - 100.1) < 0.001, f'SL Short erwartet 100.1, got {sl_short}'
    assert abs(tp_short - 99.0) < 0.001,  f'TP Short erwartet 99.0, got {tp_short}'

    print(f'SL/TP-Berechnung korrekt: Long SL={sl_long}, TP={tp_long} | Short SL={sl_short}, TP={tp_short}')


def test_global_state_read_write():
    """Prueft Active Positions Lesen/Schreiben/Loeschen (Multi-Position-API)"""
    write_active_positions([])
    assert read_active_positions() == []

    claim_position('BTC/USDT:USDT', '15m', 'long', 50000.0, 49950.0, 50500.0, 0.04)
    positions = read_active_positions()
    assert len(positions) == 1
    pos = positions[0]
    assert pos['symbol']      == 'BTC/USDT:USDT'
    assert pos['timeframe']   == '15m'
    assert pos['side']        == 'long'
    assert pos['entry_price'] == 50000.0

    clear_position('BTC/USDT:USDT', '15m')
    assert read_active_positions() == []
    print('Active Positions Lesen/Schreiben/Loeschen: OK')


def test_contracts_calculation():
    """Prueft Kontraktberechnung: risiko-basiert (risk_pct% des Kapitals / SL-Abstand)"""
    balance          = 100.0
    risk_per_trade   = 1.0     # 1%
    entry_price      = 50000.0
    sl_price         = 49950.0  # 0.1% Abstand
    min_amt          = 0.001

    contracts = calculate_contracts(balance, entry_price, sl_price, min_amt, risk_per_trade)
    sl_dist   = abs(entry_price - sl_price)  # 50.0
    expected  = (balance * risk_per_trade / 100.0) / sl_dist  # 1.0 / 50 = 0.02
    assert abs(contracts - expected) < 1e-6, f'Erwartet {expected:.6f}, got {contracts:.6f}'
    print(f'Kontraktberechnung korrekt: {contracts:.4f} Kontrakte | Risiko={risk_per_trade}% | SL-Abstand={sl_dist} USDT')


# ============================================================
# Live Test (erfordert secret.json)
# ============================================================

def test_full_mbot_workflow_on_bitget(test_setup):
    """Vollstaendiger Live-Test: Entry + SL/TP + Schliessen auf Bitget (PEPE)"""
    exchange, symbol, timeframe, leverage, margin_mode, logger, telegram_config = test_setup

    bal = exchange.fetch_balance_usdt()
    print(f'\nVerfuegbares Guthaben: {bal:.4f} USDT')

    if bal < 5.0:
        pytest.skip(f'Zu wenig Guthaben ({bal:.2f} USDT < 5 USDT) fuer Live-Test.')

    # --- Margin + Hebel setzen ---
    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)
    time.sleep(1)

    # --- Entry berechnen (risiko-basiert, kleines Exposure fuer Test) ---
    simulated_balance  = 50.0   # Fixer Testwert – unabhaengig vom echten Kontostand
    risk_per_trade_pct = 0.1    # 0.1% Risiko
    sl_pct             = 0.8    # 0.8% SL-Abstand -> Notional = 50*0.001/0.008 = 6.25 USDT

    min_amount = exchange.fetch_min_amount_tradable(symbol)
    ticker     = exchange.exchange.fetch_ticker(symbol)
    price      = float(ticker['last'])
    sl_price   = price * (1 - sl_pct / 100)
    contracts  = calculate_contracts(simulated_balance, price, sl_price, min_amount, risk_per_trade_pct)
    notional   = contracts * price

    print(f'[Schritt 1/3] Entry: LONG {contracts:.2f} PEPE @ ~{price:.6f} | Notional: {notional:.2f} USDT')

    # --- Entry platzieren ---
    try:
        entry_order = exchange.place_market_order(symbol, 'buy', contracts, margin_mode=margin_mode)
    except Exception as e:
        pytest.fail(f'Entry fehlgeschlagen: {e}')

    entry_price = float(entry_order.get('average') or entry_order.get('price') or price)
    filled      = float(entry_order.get('filled')  or entry_order.get('amount') or contracts)
    print(f'Entry ausgefuehrt: {filled:.2f} PEPE @ {entry_price:.6f}')
    time.sleep(2)

    # --- SL/TP berechnen und platzieren ---
    tp_price = entry_price * (1 + sl_pct * 2 / 100)  # 2:1 R:R
    sl_price_final = entry_price * (1 - sl_pct / 100)
    print(f'[Schritt 2/3] SL={sl_price_final:.6f} | TP={tp_price:.6f}')

    # --- Telegram-Benachrichtigung ---
    sl_dist_pct = abs(entry_price - sl_price_final) / entry_price * 100
    tp_dist_pct = abs(tp_price - entry_price) / entry_price * 100
    risk_usdt   = simulated_balance * risk_per_trade_pct / 100.0
    rr_ratio    = tp_dist_pct / sl_dist_pct if sl_dist_pct > 0 else 0
    tg_msg = (
        f"🚀 mbot SIGNAL: {symbol} ({timeframe})\n"
        f"{'─' * 32}\n"
        f"🟢 Richtung: LONG\n"
        f"💰 Entry:   ${entry_price:.6f}\n"
        f"🛑 SL:      ${sl_price_final:.6f} (-{sl_dist_pct:.2f}%)\n"
        f"🎯 TP:      ${tp_price:.6f} (+{tp_dist_pct:.2f}%)\n"
        f"📊 R:R:     1:{rr_ratio:.1f}\n"
        f"⚙️ Hebel:   {leverage}x\n"
        f"🛡️ Risiko:  {risk_per_trade_pct:.1f}% ({risk_usdt:.2f} USDT)\n"
        f"📦 Kontr.:  {filled:.0f}\n"
        f"{'─' * 32}\n"
        f"🔍 Signal:  [TEST]"
    )
    try:
        send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), tg_msg)
        print('Telegram-Benachrichtigung gesendet.')
    except Exception as e:
        print(f'WARNUNG: Telegram fehlgeschlagen: {e}')

    try:
        exchange.place_trigger_market_order(symbol, 'sell', filled, sl_price_final, reduce=True)
        time.sleep(1)
        exchange.place_trigger_market_order(symbol, 'sell', filled, tp_price, reduce=True)
    except Exception as e:
        pytest.fail(f'SL/TP-Platzierung fehlgeschlagen: {e}')

    time.sleep(3)

    # --- Position pruefen ---
    positions = exchange.fetch_open_positions(symbol)
    assert positions, f'Position nicht gefunden nach Entry (Guthaben war {bal:.2f} USDT)'
    print(f'Position offen: {positions[0]["side"].upper()} | Kontrakte: {positions[0].get("contracts")}')

    # --- Position sauber schliessen ---
    print('[Schritt 3/3] Schliesse Position...')
    exchange.cancel_all_orders_for_symbol(symbol)
    time.sleep(2)

    pos       = positions[0]
    amt       = abs(float(pos.get('contracts') or pos.get('contractSize', 0)))
    close_ord = exchange.place_market_order(symbol, 'sell', amt, reduce=True)
    assert close_ord, 'Schliessen fehlgeschlagen!'
    time.sleep(4)

    exchange.cancel_all_orders_for_symbol(symbol)
    time.sleep(2)

    # --- Finale Checks ---
    final_pos    = exchange.fetch_open_positions(symbol)
    final_orders = exchange.exchange.fetch_open_orders(
        symbol, params={'stop': True, 'productType': 'USDT-FUTURES'}
    )

    assert len(final_pos) == 0,    f'Position sollte geschlossen sein, aber noch offen: {len(final_pos)}'
    assert len(final_orders) == 0, f'Trigger-Orders sollten leer sein: {len(final_orders)}'

    print('\n--- WORKFLOW-TEST ERFOLGREICH ---')
