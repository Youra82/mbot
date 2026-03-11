# src/mbot/utils/guardian.py
import logging
from functools import wraps
from mbot.utils.telegram import send_message


def guardian_decorator(func):
    """
    Decorator: Fängt alle unerwarteten Ausnahmen ab, loggt sie und
    sendet eine Telegram-Warnung.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger = None
        telegram_config = {}
        symbol = 'Unbekannt'

        for arg in args:
            if isinstance(arg, logging.Logger):
                logger = arg
            if isinstance(arg, dict) and 'bot_token' in arg:
                telegram_config = arg
            if isinstance(arg, dict) and 'symbol' in arg:
                symbol = arg.get('symbol', 'Unbekannt')

        if not logger:
            logger = logging.getLogger("guardian_fallback")
            if not logger.handlers:
                logger.addHandler(logging.StreamHandler())

        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.critical("!!! KRITISCHER FEHLER IM GUARDIAN !!!")
            logger.critical(f"!!! Symbol: {symbol}")
            logger.critical(f"!!! Fehler: {e}", exc_info=True)

            try:
                msg = (
                    f"KRITISCHER FEHLER im mbot Guardian fuer {symbol}:\n\n"
                    f"{e.__class__.__name__}: {e}\n\nProzess wird beendet."
                )
                send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)
            except Exception as tel_e:
                logger.error(f"Telegram-Fehlermeldung konnte nicht gesendet werden: {tel_e}")

            raise e

    return wrapper
