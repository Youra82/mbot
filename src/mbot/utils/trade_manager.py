# src/mbot/utils/trade_manager.py
"""
Trade Manager fuer mbot.

- Entry mit vollem verfuegbarem Kapital + 20x Hebel
- SL = sl_account_pct / leverage Prozent Preisbewegung  (z.B. 2% / 20 = 0.1% Preis)
- TP = tp_price_pct Prozent Preisbewegung               (z.B. 1.0% Preis = 20% Konto-Gewinn)
- Global State: nur EIN Symbol darf gleichzeitig traden
"""

import os
import sys
import json
import logging
import time
import ccxt
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.telegram import send_message

GLOBAL_STATE_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'global_state.json')
MIN_NOTIONAL_USDT = 5.0


# ============================================================
# Global State Management
# ============================================================

def read_global_state() -> dict:
    """Liest den globalen Trade-Status."""
    if not os.path.exists(GLOBAL_STATE_PATH):
        return _empty_state()
    try:
        with open(GLOBAL_STATE_PATH, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def write_global_state(state: dict):
    """Schreibt den globalen Trade-Status."""
    os.makedirs(os.path.dirname(GLOBAL_STATE_PATH), exist_ok=True)
    with open(GLOBAL_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def clear_global_state():
    """Loescht den aktiven Trade-Status (nach TP/SL)."""
    write_global_state(_empty_state())
    logging.getLogger(__name__).info("Global State geleert - bereit fuer neuen Trade.")


def _empty_state() -> dict:
    return {
        'active_symbol':    None,
        'active_timeframe': None,
        'active_since':     None,
        'entry_price':      None,
        'side':             None,
        'sl_price':         None,
        'tp_price':         None,
        'contracts':        None,
    }


def is_globally_free() -> bool:
    """True wenn kein Symbol gerade tradet."""
    state = read_global_state()
    return state.get('active_symbol') is None


def claim_global_state(symbol: str, timeframe: str, side: str,
                        entry_price: float, sl_price: float,
                        tp_price: float, contracts: float) -> bool:
    """
    Versucht, den Global State fuer dieses Symbol zu beanspruchen.
    Returns True wenn erfolgreich (niemand sonst hat ihn), False wenn schon belegt.
    """
    state = read_global_state()
    if state.get('active_symbol') is not None:
        return False
    write_global_state({
        'active_symbol':    symbol,
        'active_timeframe': timeframe,
        'active_since':     datetime.now(timezone.utc).isoformat(),
        'entry_price':      entry_price,
        'side':             side,
        'sl_price':         sl_price,
        'tp_price':         tp_price,
        'contracts':        contracts,
    })
    return True


# ============================================================
# Positionsgroessen-Berechnung
# ============================================================

def calculate_sl_tp_prices(entry_price: float, side: str,
                             leverage: int, sl_account_pct: float,
                             tp_price_pct: float) -> tuple:
    """
    Berechnet SL und TP Preise.

    sl_account_pct: Prozent des Kontoverlustes (z.B. 2.0)
    leverage:       Hebel (z.B. 20)
    tp_price_pct:   Prozent Preisbewegung fuer TP (z.B. 1.0)

    SL-Preis-Abstand = sl_account_pct / leverage
    Beispiel: 2% / 20 = 0.1% Preisbewegung -> 2% Kontoverlust
    """
    sl_price_pct = sl_account_pct / leverage  # z.B. 0.1%

    if side == 'long':
        sl_price = entry_price * (1.0 - sl_price_pct / 100.0)
        tp_price = entry_price * (1.0 + tp_price_pct / 100.0)
    else:
        sl_price = entry_price * (1.0 + sl_price_pct / 100.0)
        tp_price = entry_price * (1.0 - tp_price_pct / 100.0)

    return sl_price, tp_price


def calculate_contracts(balance_usdt: float, leverage: int,
                          entry_price: float, min_amount: float) -> float:
    """
    Berechnet die Kontraktanzahl fuer den Trade.
    Verwendet das VOLLE verfuegbare Kapital.
    """
    position_value = balance_usdt * leverage  # z.B. 100 USDT * 20 = 2000 USDT
    contracts = position_value / entry_price
    contracts = max(contracts, min_amount)
    return contracts


# ============================================================
# Haupt-Trading-Funktion: Signal-Modus
# ============================================================

def execute_signal_trade(exchange, symbol: str, timeframe: str,
                          signal: dict, risk_config: dict,
                          telegram_config: dict, logger: logging.Logger) -> bool:
    """
    Wird aufgerufen wenn ein Signal erkannt wurde.
    Prueft Global State, platziert Entry + SL + TP.

    Returns True wenn Trade erfolgreich platziert.
    """
    side          = signal['side']
    leverage      = int(risk_config.get('leverage', 20))
    margin_mode   = risk_config.get('margin_mode', 'isolated')
    sl_account_pct = float(risk_config.get('sl_account_pct', 2.0))
    tp_price_pct   = float(risk_config.get('tp_price_pct', 1.0))

    # --- Kapital abrufen ---
    balance = exchange.fetch_balance_usdt()
    if balance < MIN_NOTIONAL_USDT:
        logger.warning(f"Zu wenig Kapital ({balance:.2f} USDT < {MIN_NOTIONAL_USDT} USDT). Kein Trade.")
        return False

    # --- Hebel und Margin setzen ---
    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)

    # --- Entry platzieren ---
    entry_side = 'buy' if side == 'long' else 'sell'
    min_amount = exchange.fetch_min_amount_tradable(symbol)
    current_price = signal['entry_price']
    contracts = calculate_contracts(balance, leverage, current_price, min_amount)

    # Notional-Check
    notional = contracts * current_price
    if notional < MIN_NOTIONAL_USDT:
        logger.warning(f"Notional {notional:.2f} USDT zu klein (< {MIN_NOTIONAL_USDT}). Kein Trade.")
        return False

    logger.info(f"Platziere Entry: {side.upper()} {contracts:.4f} {symbol} "
                f"| Hebel: {leverage}x | Kapital: {balance:.2f} USDT")

    try:
        entry_order = exchange.place_market_order(symbol, entry_side, contracts)
    except Exception as e:
        logger.error(f"Entry fehlgeschlagen: {e}")
        return False

    # Tatsaechlicher Entry-Preis aus Order-Rueckmeldung
    entry_price = float(entry_order.get('average') or entry_order.get('price') or current_price)
    if entry_price <= 0:
        entry_price = current_price
        logger.warning(f"Kein average aus Order, verwende aktuellen Kurs {entry_price}")

    # Tatsaechliche Kontraktanzahl aus Order
    filled = float(entry_order.get('filled') or entry_order.get('amount') or contracts)
    if filled <= 0:
        filled = contracts

    # --- SL / TP berechnen ---
    sl_price, tp_price = calculate_sl_tp_prices(
        entry_price, side, leverage, sl_account_pct, tp_price_pct
    )

    logger.info(f"Entry-Preis: {entry_price:.4f} | SL: {sl_price:.4f} | TP: {tp_price:.4f}")
    logger.info(f"SL-Abstand: {abs(entry_price - sl_price) / entry_price * 100:.3f}% Preis "
                f"= {sl_account_pct:.1f}% Konto-Verlust bei {leverage}x")

    # Kurz warten, damit Entry verarbeitet ist
    time.sleep(1.0)

    # --- SL platzieren (reduceOnly Trigger) ---
    sl_side = 'sell' if side == 'long' else 'buy'
    try:
        exchange.place_trigger_market_order(symbol, sl_side, filled, sl_price, reduce=True)
        logger.info(f"SL platziert @ {sl_price:.4f}")
    except Exception as e:
        logger.error(f"SL konnte nicht platziert werden: {e}. Schliesse Position!")
        try:
            exchange.close_position(symbol)
        except Exception as ce:
            logger.critical(f"Konnte Position nicht schliessen: {ce}")
        return False

    # --- TP platzieren (reduceOnly Trigger) ---
    try:
        exchange.place_trigger_market_order(symbol, sl_side, filled, tp_price, reduce=True)
        logger.info(f"TP platziert @ {tp_price:.4f}")
    except Exception as e:
        logger.error(f"TP konnte nicht platziert werden: {e}")

    # --- Global State beanspruchen ---
    claimed = claim_global_state(symbol, timeframe, side, entry_price,
                                  sl_price, tp_price, filled)
    if not claimed:
        # Ein anderer Prozess hat zwischenzeitlich den State belegt
        logger.warning("Global State wurde von anderem Symbol belegt. Schliesse Position.")
        try:
            exchange.cancel_all_orders_for_symbol(symbol)
            exchange.close_position(symbol)
        except Exception as ce:
            logger.error(f"Fehler beim Schliessen: {ce}")
        return False

    # --- Telegram-Benachrichtigung ---
    sl_dist_pct  = abs(entry_price - sl_price) / entry_price * 100
    tp_dist_pct  = abs(tp_price - entry_price) / entry_price * 100
    account_gain = tp_price_pct * leverage

    msg = (
        f"mbot - NEUER TRADE\n\n"
        f"Symbol:    {symbol} ({timeframe})\n"
        f"Richtung:  {side.upper()}\n"
        f"Entry:     {entry_price:.4f} USDT\n"
        f"SL:        {sl_price:.4f} ({sl_dist_pct:.2f}% Preis = -{sl_account_pct:.1f}% Konto)\n"
        f"TP:        {tp_price:.4f} ({tp_dist_pct:.2f}% Preis = +{account_gain:.0f}% Konto)\n"
        f"Hebel:     {leverage}x | Kapital: {balance:.2f} USDT\n"
        f"Kontrakte: {filled:.4f}\n\n"
        f"Signal:    {signal.get('reason', '')}"
    )
    send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)
    logger.info("Trade erfolgreich platziert und Telegram-Nachricht gesendet.")

    return True


# ============================================================
# Positions-Check-Funktion: Check-Modus
# ============================================================

def check_position_status(exchange, symbol: str, timeframe: str,
                           telegram_config: dict, logger: logging.Logger):
    """
    Prueft ob die aktive Position noch offen ist.
    Falls nicht mehr offen: Global State loeschen, Telegram-Nachricht senden.
    """
    state = read_global_state()

    if state.get('active_symbol') != symbol:
        logger.debug(f"check_position_status: {symbol} ist nicht das aktive Symbol, ueberspringe.")
        return

    positions = exchange.fetch_open_positions(symbol)

    if positions:
        # Position noch offen
        pos       = positions[0]
        pos_side  = pos.get('side', '?')
        unr_pnl   = pos.get('unrealizedPnl', 0.0)
        entry_p   = state.get('entry_price', '?')
        logger.info(
            f"Position fuer {symbol} noch offen: {pos_side.upper()} "
            f"| Entry: {entry_p} | Unrealized PnL: {unr_pnl:.2f} USDT"
        )
        return

    # Position nicht mehr offen -> TP oder SL wurde getroffen
    logger.info(f"Position fuer {symbol} wurde geschlossen (TP oder SL getroffen).")

    # Alle verbleibenden Trigger-Orders stornieren (z.B. SL falls TP getroffen)
    try:
        exchange.cancel_all_orders_for_symbol(symbol)
        logger.info(f"Verbleibende Orders fuer {symbol} storniert.")
    except Exception as e:
        logger.warning(f"Fehler beim Stornieren verbleibender Orders: {e}")

    entry_p  = state.get('entry_price', '?')
    sl_p     = state.get('sl_price', '?')
    tp_p     = state.get('tp_price', '?')
    side_str = state.get('side', '?')
    since    = state.get('active_since', '?')

    msg = (
        f"mbot - TRADE GESCHLOSSEN\n\n"
        f"Symbol:  {symbol} ({timeframe})\n"
        f"Seite:   {side_str.upper() if side_str else '?'}\n"
        f"Entry:   {entry_p}\n"
        f"SL:      {sl_p}\n"
        f"TP:      {tp_p}\n"
        f"Geoeffnet seit: {since}\n\n"
        f"Warte auf naechstes Signal..."
    )
    send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)

    # Global State loeschen -> alle Symbole koennen wieder signalisieren
    clear_global_state()
