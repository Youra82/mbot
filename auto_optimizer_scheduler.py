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
from datetime import datetime, timedelta, date

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

SETTINGS_FILE    = os.path.join(PROJECT_ROOT, 'settings.json')
SECRET_FILE      = os.path.join(PROJECT_ROOT, 'secret.json')
CONFIGS_DIR      = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
CACHE_DIR        = os.path.join(PROJECT_ROOT, 'artifacts', 'cache')
LAST_RUN_FILE    = os.path.join(CACHE_DIR, '.last_optimization_run')
IN_PROGRESS_FILE = os.path.join(CACHE_DIR, '.optimization_in_progress')
OPTIMIZER_PY     = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'analysis', 'optimizer.py')
PYTHON_EXE       = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'python3')

# Lookback je Timeframe (Tage)
LOOKBACK_MAP = {
    '5m': 60,  '15m': 60,
    '30m': 365, '1h': 365,
    '2h': 730,  '4h': 730,
    '6h': 1095, '1d': 1095,
}

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


def _resolve_pairs(opt_settings: dict, live_settings: dict) -> list:
    """Gibt [(symbol, timeframe)] zurück — aus settings oder active_strategies."""
    sym_cfg = opt_settings.get('symbols_to_optimize', 'auto')
    tf_cfg  = opt_settings.get('timeframes_to_optimize', 'auto')

    # Explizite Konfiguration: alle Kombinationen
    if str(sym_cfg).lower() != 'auto' and str(tf_cfg).lower() != 'auto':
        syms = sym_cfg if isinstance(sym_cfg, list) else [sym_cfg]
        tfs  = tf_cfg  if isinstance(tf_cfg,  list) else [tf_cfg]
        pairs = []
        for sym in syms:
            if '/' not in sym:
                sym = f"{sym.upper()}/USDT:USDT"
            for tf in tfs:
                pairs.append((sym, tf))
        return pairs

    # Auto: aus active_strategies lesen
    pairs, seen = [], set()
    for s in live_settings.get('active_strategies', []):
        if not s.get('active', True):
            continue
        sym = s.get('symbol', '')
        tf  = s.get('timeframe', '')
        if sym and tf and (sym, tf) not in seen:
            pairs.append((sym, tf))
            seen.add((sym, tf))
    return pairs or [('BTC/USDT:USDT', '15m'), ('ETH/USDT:USDT', '15m')]


def _resolve_lookback(value, timeframes: list) -> int:
    if str(value).lower() != 'auto':
        return int(value)
    return max((LOOKBACK_MAP.get(tf, 365) for tf in timeframes), default=365)


def _read_config_pnl(sym: str, tf: str) -> float | None:
    """Liest den PnL aus einer bestehenden Config-Datei (_meta.pnl_pct)."""
    safe = f"{sym.replace('/', '').replace(':', '')}_{tf}"
    path = os.path.join(CONFIGS_DIR, f"config_{safe}_mers.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get('_meta', {}).get('pnl_pct')
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='mbot Auto-Optimizer-Scheduler')
    parser.add_argument('--force', action='store_true',
                        help='Optimierung sofort erzwingen (ignoriert enabled + Schedule)')
    args = parser.parse_args()

    settings      = _load_settings()
    opt_settings  = settings.get('optimization_settings', {})
    live_settings = settings.get('live_trading_settings', {})

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

    send_tg     = opt_settings.get('send_telegram_on_completion', False)
    pairs       = _resolve_pairs(opt_settings, live_settings)
    timeframes  = list({tf for _, tf in pairs})
    lookback    = _resolve_lookback(opt_settings.get('lookback_days', 'auto'), timeframes)
    n_trials    = int(opt_settings.get('num_trials', 200))
    cpu_cores   = int(opt_settings.get('cpu_cores', 1))
    capital     = float(opt_settings.get('start_capital', 1000))
    constraints = opt_settings.get('constraints', {})
    max_dd      = float(constraints.get('max_drawdown_pct', 30))
    min_wr      = float(constraints.get('min_win_rate_pct', 50))
    min_pnl     = float(constraints.get('min_pnl_pct', 0))
    mode        = opt_settings.get('mode', 'strict')
    date_from   = (date.today() - timedelta(days=lookback)).strftime('%Y-%m-%d')
    date_to     = date.today().strftime('%Y-%m-%d')

    pairs_str = ', '.join(f"{s.split('/')[0]}/{t}" for s, t in pairs)
    log.info(f"Paare: {pairs_str}")
    log.info(f"Kapital={capital} USDT | MaxDD={max_dd}% | MinWR={min_wr}% | "
             f"MinPnL={min_pnl}% | Trials={n_trials} | Jobs={cpu_cores} | "
             f"Zeitraum: {date_from} → {date_to}")

    # Alten PnL VOR Optimierung lesen (für Vergleich in Telegram-Nachricht)
    old_pnl = {(sym, tf): _read_config_pnl(sym, tf) for sym, tf in pairs}

    start_time = datetime.now()

    if send_tg:
        _telegram_send(bot_token, chat_id,
            f"🚀 mbot Auto-Optimizer GESTARTET\n"
            f"Paare: {pairs_str}\n"
            f"Trials: {n_trials}\n"
            f"Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # In-progress Marker setzen
    os.makedirs(CACHE_DIR, exist_ok=True)
    open(IN_PROGRESS_FILE, 'w').close()

    python_exe = PYTHON_EXE if os.path.exists(PYTHON_EXE) else sys.executable
    opt_failed = set()

    try:
        # Ein Subprocess pro Paar — bessere Fehler-Isolation
        for sym, tf in pairs:
            coin = sym.split('/')[0]
            log.info(f"Optimiere {sym} ({tf}) ...")
            cmd = [
                python_exe, OPTIMIZER_PY,
                '--symbols',       coin,
                '--timeframes',    tf,
                '--start_date',    date_from,
                '--end_date',      date_to,
                '--start_capital', str(capital),
                '--trials',        str(n_trials),
                '--jobs',          str(cpu_cores),
                '--max_drawdown',  str(max_dd),
                '--min_win_rate',  str(min_wr),
                '--min_pnl',       str(min_pnl),
                '--mode',          mode,
            ]
            try:
                proc = subprocess.run(
                    cmd, cwd=PROJECT_ROOT,
                    capture_output=True, text=True, timeout=7200,
                )
                if proc.returncode != 0:
                    log.error(f"optimizer.py Fehler für {sym}/{tf} "
                              f"(rc={proc.returncode}):\n{proc.stderr[-500:]}")
                    opt_failed.add((sym, tf))
                else:
                    log.info(f"  {sym} ({tf}) — Optimierung abgeschlossen.")
                    if proc.stdout:
                        out = proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout
                        log.debug(f"  Output:\n{out}")
            except subprocess.TimeoutExpired:
                log.error(f"Timeout bei {sym}/{tf} nach 7200s.")
                opt_failed.add((sym, tf))

        if opt_failed:
            log.warning(f"Optimizer fehlgeschlagen für: "
                        f"{[f'{s.split(\"/\")[0]}/{t}' for s, t in opt_failed]}")

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
            total = len(pairs)
            lines = [f"✅ mbot Auto-Optimizer abgeschlossen (Dauer: {dur_str})", ""]

            kept_lines   = []
            failed_lines = []
            for sym, tf in pairs:
                coin    = sym.split('/')[0]
                new_pnl = _read_config_pnl(sym, tf)
                old_val = old_pnl.get((sym, tf))
                safe    = f"{sym.replace('/', '').replace(':', '')}_{tf}"
                fn      = f"config_{safe}_mers.json"

                if (sym, tf) in opt_failed or new_pnl is None:
                    failed_lines.append(f"• {coin}/{tf}: Optimizer fehlgeschlagen")
                elif old_val is not None and new_pnl <= old_val:
                    failed_lines.append(f"• {coin}/{tf}: existing_better_{old_val:.2f}pct")
                else:
                    sign = '+' if new_pnl >= 0 else ''
                    kept_lines.append(f"• {coin}/{tf}: {sign}{new_pnl:.2f}% → {fn}")

            lines.append(f"✔ Gespeichert ({len(kept_lines)}/{total}):")
            lines.extend(kept_lines if kept_lines else ["  — keine Verbesserung"])
            if failed_lines:
                lines.append("")
                lines.append(f"❌ Fehlgeschlagen ({len(failed_lines)}/{total}):")
                lines.extend(failed_lines)

            _telegram_send(bot_token, chat_id, '\n'.join(lines))

    except Exception as e:
        log.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        if send_tg:
            _telegram_send(bot_token, chat_id, f"mbot Auto-Optimierung FEHLER: {e}")
    finally:
        if os.path.exists(IN_PROGRESS_FILE):
            os.remove(IN_PROGRESS_FILE)


if __name__ == '__main__':
    main()
