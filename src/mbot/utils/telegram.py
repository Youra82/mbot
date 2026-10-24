# src/mbot/utils/telegram.py
import requests
import logging

logger = logging.getLogger(__name__)


def send_photo(bot_token, chat_id, file_path, caption=""):
    """Sendet ein Bild (PNG/JPG) an einen Telegram-Chat."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return
    api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        with open(file_path, 'rb') as img:
            response = requests.post(api_url,
                                     data={'chat_id': chat_id, 'caption': caption},
                                     files={'photo': img}, timeout=30)
            response.raise_for_status()
    except FileNotFoundError:
        logger.error(f"Bild nicht gefunden: {file_path}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden des Fotos: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Foto-Versand: {e}")


def send_message(bot_token, chat_id, message):
    """Sendet eine Textnachricht an einen Telegram-Chat."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert. Nachricht nicht gesendet.")
        return

    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped = message
    for char in escape_chars:
        escaped = escaped.replace(char, f'\\{char}')

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': escaped, 'parse_mode': 'MarkdownV2'}

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        response.raise_for_status()
        logger.debug(f"Telegram-Nachricht erfolgreich gesendet.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden der Telegram-Nachricht: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Telegram-Versand: {e}")


def send_document(bot_token, chat_id, file_path, caption=""):
    """Sendet ein Dokument (z.B. eine CSV-Datei) an einen Telegram-Chat."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return

    api_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    payload = {
        'chat_id': chat_id,
        'caption': caption
    }

    try:
        with open(file_path, 'rb') as doc:
            files = {'document': doc}
            response = requests.post(api_url, data=payload, files=files, timeout=30) # Timeout für Upload erhöht
            response.raise_for_status() # Prüft auf HTTP-Fehler
            if response.status_code != 200:
                 logger.error(f"Fehler beim Senden des Dokuments via Telegram (Status {response.status_code}): {response.text}")
            # Optional: Erfolgsmeldung
            # logger.debug(f"Dokument '{os.path.basename(file_path)}' erfolgreich an Chat {chat_id} gesendet.")

    except FileNotFoundError:
        logger.error(f"Zu sendende Datei nicht gefunden: {file_path}")
    except requests.exceptions.RequestException as e:
         logger.error(f"Netzwerkfehler beim Senden des Dokuments via Telegram: {e}")
    except Exception as e:
        logger.error(f"Allgemeiner Fehler beim Senden des Dokuments via Telegram: {e}")
