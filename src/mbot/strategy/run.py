# src/mbot/strategy/run.py
"""
mbot - Strategy Runner (MDEF-MERS Hybrid)

Modi:
  --mode signal  : Signal pruefen (MERS), Trade platzieren wenn Signal vorhanden
  --mode check   : Offene Position pruefen, State-Exit pruefen, Global State loeschen falls geschlossen

Signal-Parameter werden aus der generierten Config-Datei geladen:
  src/mbot/strategy/configs/config_BTCUSDTUSDT_15m_mers.json
  (erstellt von run_pipeline.sh via optimizer.py)

Risiko-Parameter (Hebel, Margin-Mode) kommen aus settings.json.
SL/TP werden ATR-basiert direkt vom MERS-Signal berechnet.

Wird vom master_runner.py aufgerufen.
"""

import os
import sys
import json
import logging
import argparse
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
    clear_global_state,
)
from mbot.strategy.mers_signal import get_mers_signal, check_mers_exit


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
    mode='signal': MERS-Signal pruefen und Trade platzieren
    mode='check':  Offene Position pruefen, state-basierten Exit auswerten
    """
    logger.info(f"=== mbot MERS Start | {symbol} ({timeframe}) | Modus: {mode} ===")

    exchange    = Exchange(account)
    risk_config = settings.get('risk', {})

    # MERS Signal-Parameter aus generierter Config-Datei laden
    safe_name   = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    config_path = os.path.join(
        PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs',
        f'config_{safe_name}_mers.json'
    )
    if os.path.exists(config_path):
        with open(config_path, 'r') as cf:
            loaded_cfg = json.load(cf)
        signal_config = loaded_cfg.get('signal', {})
        # risk_per_trade_pct aus Config in risk_config uebernehmen (Config hat Vorrang)
        if 'risk_per_trade_pct' in signal_config:
            risk_config = dict(risk_config)
            risk_config['risk_per_trade_pct'] = signal_config['risk_per_trade_pct']
        logger.info(f"Config geladen: config_{safe_name}_mers.json "
                    f"(PnL: {loaded_cfg.get('_meta', {}).get('pnl_pct', '?')}% | "
                    f"Risk/Trade: {risk_config.get('risk_per_trade_pct', 100):.0f}%)")
    else:
        signal_config = settings.get('signal', {})
        logger.warning(f"Keine MERS-Config gefunden fuer {symbol} ({timeframe}). "
                       f"Verwende Defaults aus settings.json. "
                       f"Bitte zuerst run_pipeline.sh ausfuehren.")

    if mode == 'check':
        # --- State-basierter Exit (MERS): Entropy steigt oder Acc dreht ---
        state = read_global_state()
        if state.get('active_symbol') == symbol:
            entry_side = state.get('side')
            df_check = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=200)
            if not df_check.empty and entry_side:
                if check_mers_exit(df_check, signal_config, entry_side):
                    logger.info(
                        f"MERS State-Exit ausgeloest fuer {symbol}: "
                        f"Entropy steigt oder Beschleunigung gedreht."
                    )
                    try:
                        exchange.cancel_all_orders_for_symbol(symbol)
                        exchange.close_position(symbol)
                        logger.info(f"Position {symbol} manuell geschlossen (State-Exit).")
                    except Exception as e:
                        logger.error(f"Fehler beim State-Exit-Schliessen: {e}")

                    send_message(
                        telegram_config.get('bot_token'),
                        telegram_config.get('chat_id'),
                        f"mbot MERS - STATE EXIT\n\n"
                        f"Symbol:  {symbol} ({timeframe})\n"
                        f"Seite:   {entry_side.upper() if entry_side else '?'}\n"
                        f"Grund:   Entropy steigt / Beschleunigung dreht\n"
                        f"(MERS state-basierter Exit vor SL/TP)"
                    )
                    clear_global_state()
                    return

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

        # OHLCV-Daten laden (mehr Kerzen benoetigt fuer Entropy-Berechnung)
        df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=200)
        if df.empty:
            logger.warning(f"Keine OHLCV-Daten fuer {symbol}. Ueberspringe.")
            return

        # MERS-Signal berechnen
        signal = get_mers_signal(df, signal_config)
        logger.info(
            f"MERS-Signal {symbol}: side={signal['side']} | "
            f"Entropy-Drop={signal['entropy_drop']} | "
            f"Energie-Rise={signal['energy_rise']} | "
            f"Acc={signal['acceleration']} | "
            f"Grund: {signal['reason']}"
        )

        if signal['side'] is None:
            logger.info(f"Kein MERS-Signal fuer {symbol}.")
            return

        # Nochmal pruefen ob noch frei (race condition minimieren)
        if not is_globally_free():
            logger.info(f"Global State gerade belegt, ueberspringe {symbol}.")
            return

        # Trade ausfuehren (SL/TP ATR-basiert aus signal['sl_price'] / signal['tp_price'])
        success = execute_signal_trade(
            exchange, symbol, timeframe, signal,
            risk_config, telegram_config, logger
        )

        if success:
            logger.info(f"MERS Trade fuer {symbol} erfolgreich platziert.")
        else:
            logger.info(f"MERS Trade fuer {symbol} nicht platziert.")

    logger.info(f"=== mbot MERS Ende | {symbol} ({timeframe}) | Modus: {mode} ===")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='mbot MERS Strategy Runner')
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

    account = accounts[0]
    try:
        run_for_account(account, telegram_config, symbol, timeframe, mode, settings, logger)
    except Exception as e:
        logger.error(f"Fehler beim Ausfuehren fuer {symbol}: {e}", exc_info=True)
        sys.exit(1)

    logger.info(f"mbot-Lauf fuer {symbol} ({timeframe}) abgeschlossen.")


if __name__ == '__main__':
    main()
