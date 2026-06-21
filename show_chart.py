#!/usr/bin/env python3
"""
show_chart.py — Simuliert einen MERS-Chart und sendet ihn per Telegram.

Laedt OHLCV-Daten, berechnet MERS-Signal und schickt einen PNG-Chart
mit simulierten Entry/SL/TP-Levels. Kein echter Trade wird platziert.

Aufruf:
    .venv/bin/python show_chart.py
    .venv/bin/python show_chart.py --symbol SOL/USDT:USDT --timeframe 1h
    .venv/bin/python show_chart.py --symbol SOL/USDT:USDT --timeframe 1h --side long
"""
import argparse
import json
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from mbot.utils.exchange import Exchange
from mbot.utils.trade_manager import _generate_mers_chart_png
from mbot.utils.telegram import send_photo, send_message
from mbot.strategy.mers_signal import get_mers_signal

logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
TMP_DIR     = os.path.join(PROJECT_ROOT, 'artifacts', 'tmp')


def _load_secrets():
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        return json.load(f)


def _load_settings():
    with open(os.path.join(PROJECT_ROOT, 'settings.json')) as f:
        return json.load(f)


def _load_signal_config(symbol: str, timeframe: str, settings: dict) -> dict:
    safe = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    path = os.path.join(CONFIGS_DIR, f'config_{safe}_mers.json')
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg = json.load(f)
            return cfg.get('signal', {})
        except Exception:
            pass
    return settings.get('signal', {})


def generate_and_send(exchange: Exchange, symbol: str, timeframe: str,
                      force_side: str, settings: dict, tg: dict) -> bool:
    signal_config = _load_signal_config(symbol, timeframe, settings)

    print(f"  Lade OHLCV {symbol} ({timeframe})...")
    df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=200)
    if df is None or df.empty or len(df) < 50:
        print(f"  WARNUNG: Nicht genug Daten.")
        return False

    # Letzte (offene) Kerze entfernen — wie im Live-Bot
    df = df.iloc[:-1]

    # MERS-Signal berechnen
    signal = get_mers_signal(df, signal_config)

    # Wenn kein echtes Signal: simuliere mit ATR aus Config
    if signal['side'] is None or force_side:
        atr_sl_mult  = float(signal_config.get('atr_sl_mult', 1.5))
        atr_tp_mult  = float(signal_config.get('atr_tp_mult', 3.0))
        entry_price  = float(df['close'].iloc[-1])
        # ATR schaetzen aus letzten 14 Kerzen
        from mbot.strategy.mdef_analysis import calc_atr
        atr_ser = calc_atr(df, period=int(signal_config.get('atr_period', 14)))
        atr = float(atr_ser.iloc[-1]) if not atr_ser.empty else entry_price * 0.01

        side = force_side or 'long'
        if side == 'long':
            sl_price = entry_price - atr_sl_mult * atr
            tp_price = entry_price + atr_tp_mult * atr
        else:
            sl_price = entry_price + atr_sl_mult * atr
            tp_price = entry_price - atr_tp_mult * atr

        signal = {
            'side': side,
            'entry_price': entry_price,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'atr': atr,
            'atr_sl_mult': atr_sl_mult,
            'atr_tp_mult': atr_tp_mult,
            'entropy_drop': signal.get('entropy_drop'),
            'energy_rise':  signal.get('energy_rise'),
            'regime':       signal.get('regime', 'n/a'),
            'reason':       '[SIMULATION]',
        }
        print(f"  Kein Live-Signal — simuliere {side.upper()} mit ATR={atr:.6g}")
    else:
        entry_price = signal['entry_price']
        sl_price    = signal['sl_price']
        tp_price    = signal['tp_price']
        print(f"  Echtes Signal: {signal['side'].upper()} | {signal['reason']}")

    print(f"  Entry: {entry_price:.6g} | SL: {sl_price:.6g} | TP: {tp_price:.6g}")

    os.makedirs(TMP_DIR, exist_ok=True)
    path = _generate_mers_chart_png(df, signal, symbol, timeframe,
                                    entry_price, sl_price, tp_price)

    if not path or not os.path.exists(path):
        print("  FEHLER: PNG konnte nicht erstellt werden.")
        return False

    side_label = 'LONG' if signal['side'] == 'long' else 'SHORT'
    caption = (
        f"[SIMULATION] MBOT | {symbol} ({timeframe})\n"
        f"{side_label} @ {entry_price:.6g}  |  SL: {sl_price:.6g}  |  TP: {tp_price:.6g}"
    )
    send_photo(tg['bot_token'], tg['chat_id'], path, caption)
    os.remove(path)
    print("  Chart gesendet.")
    return True


def main():
    parser = argparse.ArgumentParser(description='MERS-Chart simulieren und per Telegram senden')
    parser.add_argument('--symbol',    type=str, help='Symbol (z.B. SOL/USDT:USDT)')
    parser.add_argument('--timeframe', type=str, help='Timeframe (z.B. 1h)')
    parser.add_argument('--side',      type=str, default='',
                        choices=['long', 'short', ''], help='Richtung erzwingen (default: MERS-Signal)')
    args = parser.parse_args()

    secrets  = _load_secrets()
    settings = _load_settings()

    tg = secrets.get('telegram', {})
    if not tg.get('bot_token') or not tg.get('chat_id'):
        print("FEHLER: Kein Telegram-Token/Chat-ID in secret.json.")
        sys.exit(1)

    accounts = secrets.get('mbot', [])
    if not accounts:
        print("FEHLER: Kein 'mbot'-Account in secret.json.")
        sys.exit(1)

    print("Initialisiere Exchange...")
    exchange = Exchange(accounts[0])
    if not exchange.markets:
        print("FEHLER: Exchange konnte nicht initialisiert werden.")
        sys.exit(1)

    active = settings['live_trading_settings']['active_strategies']

    if args.symbol or args.timeframe:
        targets = [
            s for s in active
            if (not args.symbol    or s['symbol']    == args.symbol)
            and (not args.timeframe or s['timeframe'] == args.timeframe)
        ]
    else:
        targets = [s for s in active if s.get('active', False)]

    if not targets:
        print("Keine passenden Strategien gefunden.")
        sys.exit(1)

    print(f"\n{len(targets)} Strategie(n) — generiere Charts...\n")
    send_message(tg['bot_token'], tg['chat_id'],
                 f"MBOT Chart-Simulation ({len(targets)} Strategie(n))")

    ok = 0
    for s in targets:
        symbol    = s['symbol']
        timeframe = s['timeframe']
        print(f"[{symbol} / {timeframe}]")
        try:
            if generate_and_send(exchange, symbol, timeframe, args.side, settings, tg):
                ok += 1
        except Exception as e:
            print(f"  FEHLER: {e}")

    print(f"\nFertig: {ok}/{len(targets)} Charts gesendet.")


if __name__ == '__main__':
    main()
