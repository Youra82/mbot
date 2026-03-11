# src/mbot/strategy/run.py
"""
mbot - Strategy Runner

Modi:
  --mode signal  : Signal pruefen, Trade platzieren wenn Signal vorhanden
  --mode check   : Offene Position pruefen, Global State loeschen falls geschlossen

Wird vom master_runner.py aufgerufen.
"""

import os
import sys
import json
import logging
import argparse
import ccxt
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.exchange import Exchange
from mbot.utils.telegram import send_message
from mbot.utils.guardian import guardian_decorator
from mbot.utils.trade_manager import (
    is_globally_free,
    execute_signal_trade,
    check_position_status,
    read_global_state,
)
from mbot.strategy.momentum_logic import get_momentum_signal


# ============================================================
# Logging Setup
# ============================================================

def setup_logging(symbol: str, timeframe: str) -> logging.Logger:
    safe_name = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    log_dir   = os.path.join(PROJECT_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file  = os.path.join(log_dir, f'mbot_{safe_name}.log')

    logger_name = f'mbot_{safe_name}'
    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(
            f'%(asctime)s [{safe_name}] %(levelname)s: %(message)s', datefmt='%H:%M:%S'
        ))
        logger.addHandler(ch)
        logger.propagate = False

    return logger


# ============================================================
# Dekorierte Ausfuehrungs-Funktion
# ============================================================

@guardian_decorator
def run_for_account(account: dict, telegram_config: dict,
                     symbol: str, timeframe: str,
                     mode: str, settings: dict, logger: logging.Logger):
    """
    Hauptausfuehrung fuer einen Account.
    mode='signal': Signal pruefen und Trade platzieren
    mode='check':  Offene Position pruefen
    """
    logger.info(f"=== mbot Start | {symbol} ({timeframe}) | Modus: {mode} ===")

    exchange       = Exchange(account)
    risk_config    = settings.get('risk', {})
    signal_config  = settings.get('signal', {})

    if mode == 'check':
        # --- Positions-Check: Ist der Trade noch offen? ---
        check_position_status(exchange, symbol, timeframe, telegram_config, logger)

    elif mode == 'signal':
        # --- Signal-Check: Nur wenn Global State frei ---
        if not is_globally_free():
            state = read_global_state()
            logger.info(
                f"Global State belegt von {state.get('active_symbol')} "
                f"({state.get('active_timeframe')}) - ueberspringe {symbol}."
            )
            return

        # OHLCV-Daten laden
        df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=150)
        if df.empty:
            logger.warning(f"Keine OHLCV-Daten fuer {symbol}. Ueberspringe.")
            return

        # Signal berechnen
        signal = get_momentum_signal(df, signal_config)
        logger.info(
            f"Signal-Check {symbol}: side={signal['side']} | "
            f"Grund: {signal['reason']} | "
            f"Koerper={signal['body_ratio']:.0%} "
            f"Vol={signal['volume_multiplier']:.1f}x "
            f"RSI={signal['rsi']:.0f}"
        )

        if signal['side'] is None:
            logger.info(f"Kein Signal fuer {symbol}.")
            return

        # Nochmal pruefen ob noch frei (race condition minimieren)
        if not is_globally_free():
            logger.info(f"Global State gerade belegt, ueberspringe {symbol}.")
            return

        # Trade ausfuehren
        success = execute_signal_trade(
            exchange, symbol, timeframe, signal,
            risk_config, telegram_config, logger
        )

        if success:
            logger.info(f"Trade fuer {symbol} erfolgreich platziert.")
        else:
            logger.info(f"Trade fuer {symbol} nicht platziert.")

    logger.info(f"=== mbot Ende | {symbol} ({timeframe}) | Modus: {mode} ===")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='mbot Strategy Runner')
    parser.add_argument('--symbol',    required=True, type=str, help='Handelspaar (z.B. BTC/USDT:USDT)')
    parser.add_argument('--timeframe', required=True, type=str, help='Zeitrahmen (z.B. 15m)')
    parser.add_argument('--mode',      required=True, type=str,
                        choices=['signal', 'check'],
                        help='signal=Signal pruefen | check=Position pruefen')
    args = parser.parse_args()

    symbol    = args.symbol
    timeframe = args.timeframe
    mode      = args.mode

    logger = setup_logging(symbol, timeframe)

    try:
        settings_path = os.path.join(PROJECT_ROOT, 'settings.json')
        with open(settings_path, 'r') as f:
            settings = json.load(f)

        secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
        with open(secret_path, 'r') as f:
            secrets = json.load(f)

        accounts = secrets.get('mbot', [])
        if not accounts:
            logger.critical("Keine 'mbot'-Accounts in secret.json gefunden.")
            sys.exit(1)

        telegram_config = secrets.get('telegram', {})

    except FileNotFoundError as e:
        logger.critical(f"Datei nicht gefunden: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.critical(f"JSON-Fehler: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Initialisierungsfehler: {e}", exc_info=True)
        sys.exit(1)

    # Nur ersten Account verwenden (mbot hat einen Account)
    account = accounts[0]
    try:
        run_for_account(account, telegram_config, symbol, timeframe, mode, settings, logger)
    except Exception as e:
        logger.error(f"Fehler beim Ausfuehren fuer {symbol}: {e}", exc_info=True)
        sys.exit(1)

    logger.info(f"mbot-Lauf fuer {symbol} ({timeframe}) abgeschlossen.")


if __name__ == '__main__':
    main()
