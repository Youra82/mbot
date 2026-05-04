#!/usr/bin/env python3
"""
auto_optimizer_scheduler.py — mbot Auto-Optimizer-Scheduler

Wird von master_runner.py beim Start non-blocking aufgerufen.
Prüft ob eine MERS-Optimierung fällig ist und führt sie automatisch
aus. Sendet Telegram-Benachrichtigungen bei Start und Ende.

Aufruf:
  python3 auto_optimizer_scheduler.py           # normale Prüfung
  python3 auto_optimizer_scheduler.py --force   # sofort erzwingen
"""

import os
import sys
import json
import logging
import argparse
import subprocess
from datetime import datetime

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

SETTINGS_FILE    = os.path.join(PROJECT_ROOT, 'settings.json')
SECRET_FILE      = os.path.join(PROJECT_ROOT, 'secret.json')
CACHE_DIR        = os.path.join(PROJECT_ROOT, 'artifacts', 'cache')
LAST_RUN_FILE    = os.path.join(CACHE_DIR, '.last_optimization_run')
IN_PROGRESS_FILE = os.path.join(CACHE_DIR, '.optimization_in_progress')
PORTFOLIO_SCRIPT = os.path.join(PROJECT_ROOT, 'run_portfolio_optimizer.py')

log_dir = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'auto_optimizer.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception as e:
        log.error(f"settings.json lesen fehlgeschlagen: {e}")
        return {}


def _interval_seconds(interval: dict) -> int:
    value = int(interval.get('value', 7))
    unit  = interval.get('unit', 'days')
    mult  = {'minutes': 60, 'hours': 3600, 'days': 86400, 'weeks': 604800}
    return value * mult.get(unit, 86400)


def _is_due(schedule: dict) -> tuple[bool, str]:
    """Gibt (fällig, grund) zurück."""
    now = datetime.now()

    # Stale-Lock-Erkennung (max. 2h Laufzeit)
    if os.path.exists(IN_PROGRESS_FILE):
        age = now.timestamp() - os.path.getmtime(IN_PROGRESS_FILE)
        if age < 7200:
            return False, 'in_progress'
        os.remove(IN_PROGRESS_FILE)
        log.warning("Stale In-Progress-Lock entfernt.")

    # Erster Lauf
    if not os.path.exists(LAST_RUN_FILE):
        return True, 'first_run'

    with open(LAST_RUN_FILE) as f:
        last_run = datetime.fromisoformat(f.read().strip())

    # Interval-Check
    interval_s = _interval_seconds(schedule.get('interval', {'value': 7, 'unit': 'days'}))
    elapsed    = (now - last_run).total_seconds()
    if elapsed >= interval_s:
        return True, f'interval ({elapsed / 3600:.1f}h seit letztem Lauf)'

    # Wochenplan-Check (15-Min-Fenster, zentriert)
    dow    = schedule.get('day_of_week', -1)
    hour   = schedule.get('hour', -1)
    minute = schedule.get('minute', 0)
    if dow >= 0 and now.weekday() == dow and now.hour == hour:
        window_start = now.replace(minute=minute, second=0, microsecond=0)
        if abs((now - window_start).total_seconds()) <= 900:
            if (now.date() - last_run.date()).days >= 1:
                return True, f'scheduled (Wochentag {dow}, {hour:02d}:{minute:02d})'

    return False, 'not_due'


def _telegram_send(bot_token: str, chat_id: str, message: str):
    if not bot_token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={'chat_id': chat_id, 'text': message},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram-Fehler: {e}")



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='mbot Auto-Optimizer-Scheduler')
    parser.add_argument('--force', action='store_true',
                        help='Optimierung sofort erzwingen (ignoriert enabled + Schedule)')
    args = parser.parse_args()

    settings     = _load_settings()
    opt_settings = settings.get('optimization_settings', {})

    if args.force:
        log.info("--force gesetzt: Optimierung wird sofort gestartet.")
        reason = 'force'
    else:
        if not opt_settings.get('enabled', False):
            log.info("Auto-Optimizer deaktiviert (enabled: false).")
            return

        schedule = opt_settings.get('schedule', {})
        due, reason = _is_due(schedule)
        if not due:
            log.info(f"Optimierung nicht fällig ({reason}).")
            return

    log.info("=" * 55)
    log.info(f"Starte Auto-Optimierung — Grund: {reason}")
    log.info("=" * 55)

    # Telegram-Credentials
    bot_token, chat_id = '', ''
    try:
        with open(SECRET_FILE) as f:
            secrets = json.load(f)
        tg        = secrets.get('telegram', {})
        bot_token = tg.get('bot_token', '')
        chat_id   = tg.get('chat_id', '')
    except Exception:
        pass

    send_tg    = opt_settings.get('send_telegram_on_completion', False)
    capital    = float(opt_settings.get('start_capital', 1000))
    max_dd     = float(opt_settings.get('constraints', {}).get('max_drawdown_pct', 30))
    start_date = opt_settings.get('start_date', 'auto')
    end_date   = opt_settings.get('end_date',   'auto')
    start_time = datetime.now()

    if send_tg:
        _telegram_send(bot_token, chat_id,
            f"🔍 mbot Portfolio-Optimizer GESTARTET\n"
            f"Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Führt frische Backtests aller Configs durch und wählt bestes Portfolio.")

    # In-progress Marker setzen
    os.makedirs(CACHE_DIR, exist_ok=True)
    open(IN_PROGRESS_FILE, 'w').close()

    try:
        cmd = [sys.executable, PORTFOLIO_SCRIPT,
               '--capital', str(capital), '--max-dd', str(max_dd), '--auto-write']
        if start_date not in ('auto', '', None):
            cmd += ['--start-date', start_date]
        if end_date not in ('auto', '', None):
            cmd += ['--end-date', end_date]
        log.info(f"Starte Portfolio-Optimizer: {' '.join(str(x) for x in cmd)}")
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, timeout=7200)
        rc   = proc.returncode
        log.info(f"Portfolio-Optimizer beendet (rc={rc}).")

        # Last-run Timestamp speichern
        with open(LAST_RUN_FILE, 'w') as f:
            f.write(datetime.now().isoformat())

        elapsed = (datetime.now() - start_time).total_seconds()
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        dur_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
        log.info(f"Auto-Optimierung abgeschlossen in {elapsed / 60:.1f} min.")

        if send_tg:
            if rc == 0:
                try:
                    with open(SETTINGS_FILE) as sf:
                        stg = json.load(sf)
                    active = [s for s in stg.get('live_trading_settings', {})
                              .get('active_strategies', []) if s.get('active')]
                    lines = [f"✅ mbot Portfolio-Optimizer abgeschlossen (Dauer: {dur_str})"]
                    if active:
                        lines.append(f"\n✔ Aktives Portfolio ({len(active)} Strategie(n)):")
                        for s in active:
                            lines.append(f"• {s['symbol'].split('/')[0]}/{s['timeframe']}")
                    _telegram_send(bot_token, chat_id, '\n'.join(lines))
                except Exception:
                    _telegram_send(bot_token, chat_id,
                        f"✅ mbot Portfolio-Optimizer abgeschlossen (Dauer: {dur_str})")
            else:
                _telegram_send(bot_token, chat_id,
                    f"❌ mbot Portfolio-Optimizer FEHLER (rc={rc}, Dauer: {dur_str})")

    except subprocess.TimeoutExpired:
        log.error("Timeout: Portfolio-Optimizer hat zu lange gedauert.")
        if send_tg:
            _telegram_send(bot_token, chat_id, "mbot Portfolio-Optimierung: Timeout!")
    except Exception as e:
        log.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        if send_tg:
            _telegram_send(bot_token, chat_id, f"mbot Portfolio-Optimierung FEHLER: {e}")
    finally:
        if os.path.exists(IN_PROGRESS_FILE):
            os.remove(IN_PROGRESS_FILE)


if __name__ == '__main__':
    main()
