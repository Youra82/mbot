# master_runner.py
"""
mbot Master Runner

Logik:
  1. Liest settings.json -> aktive Symbole
  2. Liest global_state.json -> ist gerade ein Symbol aktiv?

  FALL A: Ein Symbol ist aktiv (offener Trade)
    -> Nur fuer dieses Symbol 'run.py --mode check' ausfuehren
    -> Prueft ob Position noch offen
    -> Falls geschlossen: Global State wird in run.py geloescht

  FALL B: Kein Symbol aktiv (kein offener Trade)
    -> Fuer jedes Symbol SEQUENZIELL 'run.py --mode signal' ausfuehren
    -> Sobald ein Symbol den Global State beansprucht hat: Schleife abbrechen
    -> Andere Symbole werden in dieser Runde nicht mehr geprueft

Wird per Cronjob alle 1-5 Minuten ausgefuehrt (je nach Timeframe der Strategien).
"""

import json
import subprocess
import sys
import os
import time
import logging

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# Logging
log_dir  = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'master_runner.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ]
)

GLOBAL_STATE_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'global_state.json')
RUN_SCRIPT        = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'run.py')


def read_global_state() -> dict:
    if not os.path.exists(GLOBAL_STATE_PATH):
        return {'active_symbol': None, 'active_timeframe': None}
    try:
        with open(GLOBAL_STATE_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {'active_symbol': None, 'active_timeframe': None}


def run_strategy(python_exe: str, symbol: str, timeframe: str, mode: str, wait: bool = True):
    """Startet run.py als Subprocess. Falls wait=True: wartet bis Prozess fertig."""
    cmd = [python_exe, RUN_SCRIPT, '--symbol', symbol, '--timeframe', timeframe, '--mode', mode]
    logging.info(f"Starte: {' '.join(cmd)}")
    try:
        if wait:
            result = subprocess.run(cmd, timeout=120)
            if result.returncode != 0:
                logging.warning(f"run.py fuer {symbol} beendet mit Code {result.returncode}")
        else:
            subprocess.Popen(cmd)
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout bei {symbol} ({mode}) nach 120s")
    except Exception as e:
        logging.error(f"Fehler beim Starten von run.py fuer {symbol}: {e}")


def main():
    logging.info("=" * 55)
    logging.info("mbot Master Runner")
    logging.info("=" * 55)

    # --- Python-Interpreter ---
    python_exe = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'python3')
    if not os.path.exists(python_exe):
        python_exe = os.path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')  # Windows
    if not os.path.exists(python_exe):
        python_exe = sys.executable  # Fallback: aktueller Interpreter
        logging.warning(f"Kein .venv gefunden, verwende: {python_exe}")

    # --- Settings laden ---
    try:
        with open(os.path.join(PROJECT_ROOT, 'settings.json'), 'r') as f:
            settings = json.load(f)
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)
    except FileNotFoundError as e:
        logging.critical(f"Datei nicht gefunden: {e}")
        return
    except json.JSONDecodeError as e:
        logging.critical(f"JSON-Fehler: {e}")
        return

    if not secrets.get('mbot'):
        logging.critical("Keine 'mbot'-Accounts in secret.json gefunden.")
        return

    active_strategies = settings.get('live_trading_settings', {}).get('active_strategies', [])
    active_strategies = [s for s in active_strategies if isinstance(s, dict) and s.get('active')]

    if not active_strategies:
        logging.warning("Keine aktiven Strategien in settings.json.")
        return

    logging.info(f"Aktive Strategien: {len(active_strategies)}")

    # --- Global State lesen ---
    state = read_global_state()
    active_symbol    = state.get('active_symbol')
    active_timeframe = state.get('active_timeframe')

    # =========================================================
    # FALL A: Ein Symbol ist gerade aktiv -> Position pruefen
    # =========================================================
    if active_symbol:
        logging.info(f"Aktiver Trade: {active_symbol} ({active_timeframe}) -> Position pruefen")
        run_strategy(python_exe, active_symbol, active_timeframe, mode='check', wait=True)

        # Nach dem Check schauen ob der State geloescht wurde
        state_after = read_global_state()
        if state_after.get('active_symbol') is None:
            logging.info(f"Trade fuer {active_symbol} wurde geschlossen. Bereit fuer neues Signal.")
        else:
            logging.info(f"Trade fuer {active_symbol} ist noch offen.")

        logging.info("Master Runner beendet (Position-Check-Modus).")
        return

    # =========================================================
    # FALL B: Kein aktiver Trade -> Alle Symbole auf Signal pruefen
    # =========================================================
    logging.info("Kein aktiver Trade. Pruefe alle Symbole auf Signal...")

    for strategy in active_strategies:
        symbol    = strategy.get('symbol')
        timeframe = strategy.get('timeframe')

        if not symbol or not timeframe:
            logging.warning(f"Unvollstaendige Strategie: {strategy}")
            continue

        logging.info(f"--- Signal-Check: {symbol} ({timeframe}) ---")
        run_strategy(python_exe, symbol, timeframe, mode='signal', wait=True)

        # Pruefen ob dieser Durchlauf einen Trade ausgeloest hat
        state_after = read_global_state()
        if state_after.get('active_symbol') is not None:
            logging.info(
                f"Signal gefunden! {state_after['active_symbol']} ({state_after['active_timeframe']}) "
                f"ist jetzt aktiv. Breche weitere Pruefungen ab."
            )
            break

        time.sleep(1)  # Kurze Pause zwischen Symbolen

    logging.info("Master Runner beendet.")


if __name__ == '__main__':
    main()
