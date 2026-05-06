# master_runner.py
"""
mbot Master Runner (Multi-Position)

Logik:
  1. Startet Auto-Optimizer-Scheduler im Hintergrund (prueft ob Optimierung faellig)
  2. Liest settings.json -> aktive Symbole aus active_strategies (immer)
  3. Liest active_positions.json -> welche Strategien haben offene Trades?

  FALL A: Aktive Positionen vorhanden
    -> Fuer JEDE aktive Position 'run.py --mode check' ausfuehren
    -> Prueft ob Position noch offen
    -> Falls geschlossen: Position wird in run.py aus State entfernt

  FALL B: Freie Strategien vorhanden (max_open_positions nicht erreicht)
    -> Fuer jede FREIE Strategie 'run.py --mode signal' ausfuehren
    -> Mehrere Strategien koennen gleichzeitig Signale finden und traden
    -> Stoppe wenn max_open_positions erreicht

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

ACTIVE_POSITIONS_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'active_positions.json')
RUN_SCRIPT            = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'run.py')
CONFIGS_DIR           = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
AUTO_OPT_SCRIPT       = os.path.join(PROJECT_ROOT, 'auto_optimizer_scheduler.py')


def read_active_positions() -> list:
    """Liest alle aktiven Positionen aus active_positions.json."""
    if not os.path.exists(ACTIVE_POSITIONS_PATH):
        return []
    try:
        with open(ACTIVE_POSITIONS_PATH, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_strategies_from_configs() -> list:
    """Laedt aktive Strategien aus den generierten Config-Dateien."""
    if not os.path.exists(CONFIGS_DIR):
        return []
    strategies = []
    for fn in sorted(os.listdir(CONFIGS_DIR)):
        if not fn.startswith('config_') or not fn.endswith('_mers.json'):
            continue
        path = os.path.join(CONFIGS_DIR, fn)
        try:
            with open(path, 'r') as f:
                cfg = json.load(f)
            market = cfg.get('market', {})
            symbol = market.get('symbol')
            tf     = market.get('timeframe')
            if symbol and tf:
                strategies.append({'symbol': symbol, 'timeframe': tf, 'active': True})
        except Exception as e:
            logging.warning(f"Fehler beim Lesen von {fn}: {e}")
    return strategies


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
    logging.info("mbot Master Runner (Multi-Position)")
    logging.info("=" * 55)

    # --- Python-Interpreter ---
    python_exe = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'python3')
    if not os.path.exists(python_exe):
        python_exe = os.path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
    if not os.path.exists(python_exe):
        python_exe = sys.executable
        logging.warning(f"Kein .venv gefunden, verwende: {python_exe}")

    # --- Auto-Optimizer im Hintergrund pruefen ---
    if os.path.exists(AUTO_OPT_SCRIPT):
        logging.info("[Auto-Optimizer] Pruefe ob Optimierung faellig...")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'auto_optimizer_trigger.log'), 'a') as log_f:
            subprocess.Popen(
                [python_exe, AUTO_OPT_SCRIPT],
                stdout=log_f,
                stderr=subprocess.STDOUT,
            )

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

    live_settings      = settings.get('live_trading_settings', {})
    use_auto_optimizer = live_settings.get('use_auto_optimizer_results', False)
    max_open_positions = int(live_settings.get('max_open_positions', 10))

    if use_auto_optimizer:
        logging.info("Modus: Auto-Optimizer (Strategien aus settings.json active_strategies).")
    else:
        logging.info("Modus: Manuell (Strategien aus settings.json active_strategies).")
    active_strategies = [s for s in live_settings.get('active_strategies', [])
                         if isinstance(s, dict) and s.get('active')]

    if not active_strategies:
        logging.warning("Keine aktiven Strategien gefunden.")
        return

    logging.info(f"Aktive Strategien: {len(active_strategies)} | Max. Positionen: {max_open_positions}")

    active_strategy_keys = {(s['symbol'], s['timeframe']) for s in active_strategies}

    # ==========================================================
    # FALL A: Position-Check nur fuer Positionen in active_strategies
    # ==========================================================
    all_positions     = read_active_positions()
    relevant_positions = [p for p in all_positions
                          if (p.get('symbol'), p.get('timeframe')) in active_strategy_keys]
    orphaned           = [p for p in all_positions if p not in relevant_positions]

    for p in orphaned:
        logging.warning(f"  Ignoriere verwaiste Position (nicht in settings): {p.get('symbol')} ({p.get('timeframe')})")

    if relevant_positions:
        logging.info(f"Offene Trades: {len(relevant_positions)} -> Pruefe alle Positionen...")
        for pos in relevant_positions:
            sym = pos.get('symbol')
            tf  = pos.get('timeframe')
            if not sym or not tf:
                continue
            logging.info(f"  Position-Check: {sym} ({tf})")
            run_strategy(python_exe, sym, tf, mode='check', wait=True)
    else:
        logging.info("Keine offenen Trades.")

    # ==========================================================
    # FALL B: Signal-Check fuer freie Strategien
    # ==========================================================
    # Aktuellen State nach den Checks neu einlesen (nur relevante)
    all_positions    = read_active_positions()
    active_positions = [p for p in all_positions
                        if (p.get('symbol'), p.get('timeframe')) in active_strategy_keys]
    active_keys      = {(p['symbol'], p['timeframe']) for p in active_positions}
    num_open         = len(active_keys)

    if num_open >= max_open_positions:
        logging.info(f"Max. Positionen ({max_open_positions}) belegt. Kein Signal-Check.")
        logging.info("Master Runner beendet.")
        return

    logging.info(
        f"Offene Trades: {num_open}/{max_open_positions}. "
        f"Pruefe Signale fuer freie Strategien..."
    )

    for strategy in active_strategies:
        sym = strategy.get('symbol')
        tf  = strategy.get('timeframe')

        if not sym or not tf:
            logging.warning(f"Unvollstaendige Strategie: {strategy}")
            continue

        if (sym, tf) in active_keys:
            logging.info(f"  {sym} ({tf}): bereits in Trade, ueberspringe.")
            continue

        logging.info(f"  Signal-Check: {sym} ({tf})")
        run_strategy(python_exe, sym, tf, mode='signal', wait=True)

        # State neu einlesen um aktuellen Stand zu kennen
        active_positions = read_active_positions()
        active_keys      = {(p['symbol'], p['timeframe']) for p in active_positions}
        num_open         = len(active_keys)

        if num_open >= max_open_positions:
            logging.info(f"Max. Positionen ({max_open_positions}) erreicht. Stoppe Signal-Suche.")
            break

        time.sleep(1)

    logging.info("Master Runner beendet.")


if __name__ == '__main__':
    main()
