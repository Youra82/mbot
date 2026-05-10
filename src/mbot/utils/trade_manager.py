# src/mbot/utils/trade_manager.py
"""
Trade Manager fuer mbot (Multi-Position).

- Entry mit risiko-basierter Positionsgroesse (wie dnabot)
- Positionsgroesse = (Kapital * risk_per_trade_pct%) / SL-Abstand (Preis)
- SL = sl_account_pct / leverage Prozent Preisbewegung
- TP = tp_price_pct Prozent Preisbewegung
- Multi-Position State: mehrere Symbole koennen gleichzeitig traden
  (max_open_positions aus settings.json)
"""

import os
import sys
import json
import logging
import math
import time
import ccxt
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.telegram import send_message

ACTIVE_POSITIONS_PATH  = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'active_positions.json')
CANDLE_COOLDOWNS_PATH  = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'candle_cooldowns.json')
# Legacy-Pfad fuer Migration
_LEGACY_STATE_PATH     = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'global_state.json')
MIN_NOTIONAL_USDT      = 5.0

# Timeframe -> Sekunden
_TF_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
    '12h': 43200, '1d': 86400, '3d': 259200, '1w': 604800,
}


# ============================================================
# Candle-Cooldown (eine Position pro Kerze)
# ============================================================

def _read_candle_cooldowns() -> list:
    if not os.path.exists(CANDLE_COOLDOWNS_PATH):
        return []
    try:
        with open(CANDLE_COOLDOWNS_PATH, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_candle_cooldowns(cooldowns: list):
    os.makedirs(os.path.dirname(CANDLE_COOLDOWNS_PATH), exist_ok=True)
    with open(CANDLE_COOLDOWNS_PATH, 'w') as f:
        json.dump(cooldowns, f, indent=2)


def set_candle_cooldown(symbol: str, timeframe: str):
    """Setzt einen Cooldown bis zum Ende der aktuellen Kerze fuer symbol+timeframe."""
    tf_secs = _TF_SECONDS.get(timeframe)
    if tf_secs is None:
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    candle_end_ts = math.ceil(now_ts / tf_secs) * tf_secs
    blocked_until = datetime.fromtimestamp(candle_end_ts, tz=timezone.utc).isoformat()

    cooldowns = [c for c in _read_candle_cooldowns()
                 if not (c.get('symbol') == symbol and c.get('timeframe') == timeframe)]
    cooldowns.append({'symbol': symbol, 'timeframe': timeframe, 'blocked_until': blocked_until})
    _write_candle_cooldowns(cooldowns)
    logging.getLogger(__name__).info(
        f"Candle-Cooldown gesetzt: {symbol} ({timeframe}) gesperrt bis {blocked_until}"
    )


def is_candle_cooldown_active(symbol: str, timeframe: str) -> bool:
    """True wenn fuer diese Strategie noch kein neuer Entry auf der aktuellen Kerze erlaubt ist."""
    now = datetime.now(timezone.utc)
    for c in _read_candle_cooldowns():
        if c.get('symbol') == symbol and c.get('timeframe') == timeframe:
            try:
                blocked_until = datetime.fromisoformat(c['blocked_until'])
                return now < blocked_until
            except (KeyError, ValueError):
                return False
    return False


# ============================================================
# Multi-Position State Management
# ============================================================

def read_active_positions() -> list:
    """
    Liest alle aktiven Positionen.
    Beim ersten Aufruf wird ggf. die alte global_state.json migriert.
    """
    if not os.path.exists(ACTIVE_POSITIONS_PATH):
        _migrate_legacy_state()
    if not os.path.exists(ACTIVE_POSITIONS_PATH):
        return []
    try:
        with open(ACTIVE_POSITIONS_PATH, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def write_active_positions(positions: list):
    """Schreibt alle aktiven Positionen."""
    os.makedirs(os.path.dirname(ACTIVE_POSITIONS_PATH), exist_ok=True)
    with open(ACTIVE_POSITIONS_PATH, 'w') as f:
        json.dump(positions, f, indent=2)


def read_position(symbol: str, timeframe: str) -> dict | None:
    """Gibt die aktive Position fuer symbol+timeframe zurueck, oder None."""
    for pos in read_active_positions():
        if pos.get('symbol') == symbol and pos.get('timeframe') == timeframe:
            return pos
    return None


def is_strategy_free(symbol: str, timeframe: str) -> bool:
    """True wenn diese Strategie (Symbol+TF) keinen offenen Trade hat."""
    return read_position(symbol, timeframe) is None


def claim_position(symbol: str, timeframe: str, side: str,
                   entry_price: float, sl_price: float,
                   tp_price: float, contracts: float) -> bool:
    """
    Fuegt eine neue aktive Position hinzu.
    Returns False wenn diese Strategie bereits aktiv ist.
    """
    positions = read_active_positions()
    for pos in positions:
        if pos.get('symbol') == symbol and pos.get('timeframe') == timeframe:
            return False
    positions.append({
        'symbol':       symbol,
        'timeframe':    timeframe,
        'side':         side,
        'entry_price':  entry_price,
        'sl_price':     sl_price,
        'tp_price':     tp_price,
        'contracts':    contracts,
        'active_since': datetime.now(timezone.utc).isoformat(),
    })
    write_active_positions(positions)
    return True


def clear_position(symbol: str, timeframe: str):
    """Entfernt eine Position nach Trade-Abschluss."""
    positions = read_active_positions()
    positions = [p for p in positions
                 if not (p.get('symbol') == symbol and p.get('timeframe') == timeframe)]
    write_active_positions(positions)
    logging.getLogger(__name__).info(f"Position {symbol} ({timeframe}) entfernt.")


def _migrate_legacy_state():
    """Einmalige Migration von global_state.json -> active_positions.json."""
    if not os.path.exists(_LEGACY_STATE_PATH):
        return
    try:
        with open(_LEGACY_STATE_PATH, 'r') as f:
            old = json.load(f)
        sym = old.get('active_symbol')
        tf  = old.get('active_timeframe')
        if sym and tf:
            pos = {
                'symbol':       sym,
                'timeframe':    tf,
                'side':         old.get('side'),
                'entry_price':  old.get('entry_price'),
                'sl_price':     old.get('sl_price'),
                'tp_price':     old.get('tp_price'),
                'contracts':    old.get('contracts'),
                'active_since': old.get('active_since'),
            }
            write_active_positions([pos])
            logging.getLogger(__name__).info(
                f"Migration: global_state.json -> active_positions.json ({sym} {tf})"
            )
        os.remove(_LEGACY_STATE_PATH)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration fehlgeschlagen: {e}")


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


def calculate_contracts(balance_usdt: float, entry_price: float,
                          sl_price: float, min_amount: float,
                          risk_per_trade_pct: float = 1.0) -> float:
    """
    Berechnet die Kontraktanzahl risiko-basiert (wie dnabot).
    risk_per_trade_pct: Anteil des Kapitals der riskiert wird (z.B. 1.0 = 1%).
    Positionsgroesse = (balance * risk_pct%) / SL-Abstand-in-Preis
    """
    risk_amount  = balance_usdt * risk_per_trade_pct / 100.0
    sl_distance  = abs(entry_price - sl_price)
    if sl_distance <= 0:
        return min_amount
    contracts = risk_amount / sl_distance
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
    Prueft ob Strategie frei ist, platziert Entry + SL + TP.

    Returns True wenn Trade erfolgreich platziert.
    """
    side               = signal['side']
    leverage           = int(risk_config.get('leverage', 5))
    margin_mode        = risk_config.get('margin_mode', 'isolated')
    sl_account_pct     = float(risk_config.get('sl_account_pct', 2.0))
    tp_price_pct       = float(risk_config.get('tp_price_pct', 1.0))
    risk_per_trade_pct = float(risk_config.get('risk_per_trade_pct', 1.0))

    # --- Kapital abrufen ---
    balance = exchange.fetch_balance_usdt()
    if balance < MIN_NOTIONAL_USDT:
        logger.warning(f"Zu wenig Kapital ({balance:.2f} USDT < {MIN_NOTIONAL_USDT} USDT). Kein Trade.")
        return False

    # --- Hebel und Margin setzen ---
    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)

    entry_side    = 'buy' if side == 'long' else 'sell'
    min_amount    = exchange.fetch_min_amount_tradable(symbol)
    current_price = signal['entry_price']

    atr         = signal.get('atr')
    atr_sl_mult = signal.get('atr_sl_mult')
    atr_tp_mult = signal.get('atr_tp_mult')

    # --- SL-Preis vorab schaetzen (fuer risiko-basierte Positionsgroesse) ---
    if atr and atr > 0 and atr_sl_mult:
        if side == 'long':
            est_sl_price = current_price - atr_sl_mult * atr
        else:
            est_sl_price = current_price + atr_sl_mult * atr
    else:
        sl_price_pct = sl_account_pct / leverage
        if side == 'long':
            est_sl_price = current_price * (1.0 - sl_price_pct / 100.0)
        else:
            est_sl_price = current_price * (1.0 + sl_price_pct / 100.0)

    # --- Positionsgroesse: risiko-basiert (wie dnabot) ---
    contracts = calculate_contracts(balance, current_price, est_sl_price, min_amount,
                                    risk_per_trade_pct)

    # Margin-Cap: Kontrakte duerfen verfuegbare Margin nicht ueberschreiten
    max_contracts_by_margin = (balance * leverage) / current_price * 0.99  # 1% Puffer
    if contracts > max_contracts_by_margin:
        logger.warning(
            f"Kontrakte {contracts:.4f} > Margin-Cap {max_contracts_by_margin:.4f} "
            f"(Balance={balance:.2f} USDT, Hebel={leverage}x). Reduziere auf Cap."
        )
        contracts = max_contracts_by_margin

    # Notional-Check
    notional = contracts * current_price
    if notional < MIN_NOTIONAL_USDT:
        logger.warning(f"Notional {notional:.2f} USDT zu klein (< {MIN_NOTIONAL_USDT}). Kein Trade.")
        return False

    logger.info(f"Platziere Entry: {side.upper()} {contracts:.4f} {symbol} "
                f"| Hebel: {leverage}x | Kapital: {balance:.2f} USDT | Risiko: {risk_per_trade_pct}%")

    try:
        entry_order = exchange.place_market_order(symbol, entry_side, contracts,
                                                   margin_mode=margin_mode)
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

    # --- SL / TP mit tatsaechlichem Fill-Preis berechnen ---
    if atr and atr > 0 and atr_sl_mult and atr_tp_mult:
        if side == 'long':
            sl_price = entry_price - atr_sl_mult * atr
            tp_price = entry_price + atr_tp_mult * atr
        else:
            sl_price = entry_price + atr_sl_mult * atr
            tp_price = entry_price - atr_tp_mult * atr
        logger.info(f"SL/TP ATR-basiert: ATR={atr:.4f} | SL-Mult={atr_sl_mult} | TP-Mult={atr_tp_mult}")
    else:
        sl_price, tp_price = calculate_sl_tp_prices(
            entry_price, side, leverage, sl_account_pct, tp_price_pct
        )
        logger.info(f"SL/TP prozentbasiert: SL={sl_account_pct}%/Konto | TP={tp_price_pct}%/Preis")

    logger.info(f"Entry-Preis: {entry_price:.4f} | SL: {sl_price:.4f} | TP: {tp_price:.4f}")
    sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100
    tp_dist_pct = abs(tp_price - entry_price) / entry_price * 100
    logger.info(f"SL-Abstand: {sl_dist_pct:.3f}% | TP-Abstand: {tp_dist_pct:.3f}% | R:R=1:{tp_dist_pct/sl_dist_pct:.1f}")

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

    # --- Position in State eintragen ---
    claimed = claim_position(symbol, timeframe, side, entry_price,
                              sl_price, tp_price, filled)
    if not claimed:
        logger.warning("Strategie wurde parallel von anderem Prozess belegt. Schliesse Position.")
        try:
            exchange.cancel_all_orders_for_symbol(symbol)
            exchange.close_position(symbol)
        except Exception as ce:
            logger.error(f"Fehler beim Schliessen: {ce}")
        return False

    # --- Telegram-Benachrichtigung ---
    sl_dist_pct  = abs(entry_price - sl_price) / entry_price * 100
    tp_dist_pct  = abs(tp_price - entry_price) / entry_price * 100
    rr_ratio     = tp_dist_pct / sl_dist_pct if sl_dist_pct > 0 else 0

    direction_emoji = "🟢" if side == 'long' else "🔴"
    risk_usdt = balance * risk_per_trade_pct / 100.0
    msg = (
        f"🚀 mbot SIGNAL: {symbol} ({timeframe})\n"
        f"{'─' * 32}\n"
        f"{direction_emoji} Richtung: {side.upper()}\n"
        f"💰 Entry:   ${entry_price:.6f}\n"
        f"🛑 SL:      ${sl_price:.6f} (-{sl_dist_pct:.2f}%)\n"
        f"🎯 TP:      ${tp_price:.6f} (+{tp_dist_pct:.2f}%)\n"
        f"📊 R:R:     1:{rr_ratio:.1f}\n"
        f"⚙️ Hebel:   {leverage}x\n"
        f"🛡️ Risiko:  {risk_per_trade_pct:.1f}% ({risk_usdt:.2f} USDT)\n"
        f"📦 Kontr.:  {filled:.0f}\n"
        f"{'─' * 32}\n"
        f"🔍 Signal:  {signal.get('reason', '')}"
    )
    send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)
    logger.info("Trade erfolgreich platziert und Telegram-Nachricht gesendet.")

    return True


# ============================================================
# Positions-Check-Funktion: Check-Modus
# ============================================================

# ============================================================
# Housekeeper
# ============================================================

def housekeeper_routine(exchange, symbol: str, logger: logging.Logger) -> bool:
    """Storniert alle verbleibenden Orders und schliesst verwaiste Positionen.
    Wird aufgerufen wenn keine offene Position mehr existiert."""
    try:
        logger.info(f"Housekeeper: Starte Aufraeumroutine fuer {symbol}...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(1)

        position = exchange.fetch_open_positions(symbol)
        if position:
            pos_info = position[0]
            close_side = 'sell' if pos_info['side'] == 'long' else 'buy'
            logger.warning(f"Housekeeper: Verwaiste Position ({pos_info['side']}) — schliesse...")
            exchange.place_market_order(symbol, close_side, float(pos_info['contracts']), reduce=True)
            time.sleep(3)

        if exchange.fetch_open_positions(symbol):
            logger.error("Housekeeper: Position konnte nicht geschlossen werden!")
        else:
            logger.info(f"Housekeeper: {symbol} ist sauber.")
        return True
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}", exc_info=True)
        return False


def check_position_status(exchange, symbol: str, timeframe: str,
                           telegram_config: dict, logger: logging.Logger):
    """
    Prueft ob die aktive Position noch offen ist.
    Falls nicht mehr offen: Housekeeper ausfuehren (loescht auch Ghost-Trigger),
    dann Position aus State entfernen und Telegram-Nachricht senden.
    """
    pos       = read_position(symbol, timeframe)
    positions = exchange.fetch_open_positions(symbol)

    if positions:
        if pos is None:
            logger.debug(f"check_position_status: Keine aktive Position fuer {symbol} ({timeframe}).")
            return
        p        = positions[0]
        pos_side = p.get('side', '?')
        unr_pnl  = p.get('unrealizedPnl', 0.0)
        entry_p  = pos.get('entry_price', '?')
        logger.info(
            f"Position fuer {symbol} noch offen: {pos_side.upper()} "
            f"| Entry: {entry_p} | Unrealized PnL: {unr_pnl:.2f} USDT"
        )
        return

    # Keine offene Position auf der Exchange -> Ghost-Trigger und verwaiste Orders bereinigen
    housekeeper_routine(exchange, symbol, logger)

    if pos is None:
        logger.debug(f"check_position_status: Keine aktive Position fuer {symbol} ({timeframe}).")
        return

    # Getrackte Position wurde geschlossen -> Telegram + State bereinigen
    logger.info(f"Position fuer {symbol} wurde geschlossen (TP oder SL getroffen).")

    entry_p  = pos.get('entry_price', '?')
    sl_p     = pos.get('sl_price', '?')
    tp_p     = pos.get('tp_price', '?')
    side_str = pos.get('side', '?')
    since    = pos.get('active_since', '?')

    direction_emoji = "🟢" if side_str == 'long' else "🔴"
    msg = (
        f"✅ mbot TRADE GESCHLOSSEN\n"
        f"{'─' * 32}\n"
        f"{direction_emoji} {side_str.upper() if side_str else '?'} | {symbol} ({timeframe})\n"
        f"💰 Entry:  ${entry_p}\n"
        f"🛑 SL:     ${sl_p}\n"
        f"🎯 TP:     ${tp_p}\n"
        f"🕐 Seit:   {since}\n"
        f"{'─' * 32}\n"
        f"⏳ Warte auf naechstes Signal..."
    )
    send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)

    clear_position(symbol, timeframe)
    set_candle_cooldown(symbol, timeframe)
