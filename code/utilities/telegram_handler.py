# code/utilities/telegram_handler.py
# Diese Datei wird unverändert von stbot übernommen.

import requests
import logging

logger = logging.getLogger(__name__)

def send_telegram_message(bot_token, chat_id, message):
    """
    Sendet eine Nachricht an einen Telegram-Chat.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht in secret.json konfiguriert. Überspringe Benachrichtigung.")
        return

    # Telegram erfordert das Escapen bestimmter Zeichen für den MarkdownV2-Modus.
    escape_chars = '_*[]()~`>#+-=|{}.!'
    # Erstelle eine temporäre Nachrichtenkopie zum Escapen
    escaped_message = message
    for char in escape_chars:
        escaped_message = escaped_message.replace(char, f'\\{char}')

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': escaped_message,
        'parse_mode': 'MarkdownV2'
    }
    
    try:
        response = requests.post(api_url, data=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Fehler beim Senden der Telegram-Nachricht: {response.text}")
    except Exception as e:
        logger.error(f"Ausnahme beim Senden der Telegram-Nachricht: {e}")
