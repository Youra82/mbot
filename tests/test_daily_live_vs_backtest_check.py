"""Lokale Tests fuer die Log-Auswertung im Tagescheck (keine Bitget-Aufrufe)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import pandas as pd

import daily_live_vs_backtest_check as mod

LOCAL_TZ = datetime.now().astimezone().tzinfo
DECISION = pd.Timestamp('2026-10-22 12:00', tz='UTC')  # Kerzenschluss


def _lines(entries):
    out = []
    for minutes, level, msg in entries:
        ts = (DECISION + pd.Timedelta(minutes=minutes)).tz_convert(LOCAL_TZ)
        out.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S')},123 - {level} - {msg}\n")
    return ''.join(out)


def test_no_signal_and_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'LOG_DIR', str(tmp_path))
    (tmp_path / 'mbot_XRPUSDTUSDT_6h.log').write_text(_lines([
        (0.5, 'INFO', 'MERS-Signal XRP/USDT:USDT: side=None | Entropy-Drop=False | Grund: kein Drop'),
        (0.6, 'INFO', 'Kein MERS-Signal fuer XRP/USDT:USDT.')]), encoding='utf-8')
    assert mod.explain_from_log('XRPUSDTUSDT_6h', DECISION, 'long').startswith('Live-Code sah kein Signal')
    (tmp_path / 'mbot_SOLUSDTUSDT_6h.log').write_text(_lines([
        (0.5, 'INFO', 'Candle-Cooldown aktiv: SOL/USDT:USDT (6h) - kein Re-Entry auf gleicher Kerze.')]),
        encoding='utf-8')
    assert mod.explain_from_log('SOLUSDTUSDT_6h', DECISION).startswith('Kerzen-Sperre nach Exit')


def test_position_limit_from_master_runner(tmp_path, monkeypatch):
    """Strategie selbst ohne Log-Eintrag: master_runner hatte die Signal-Suche abgebrochen."""
    monkeypatch.setattr(mod, 'LOG_DIR', str(tmp_path))
    (tmp_path / 'mbot_DOGEUSDTUSDT_6h.log').write_text(_lines([(-600, 'INFO', 'Kein MERS-Signal fuer DOGE.')]),
                                                     encoding='utf-8')
    (tmp_path / 'master_runner.log').write_text(_lines([
        (0.4, 'INFO', 'Offene Trades: 5/6. Pruefe Signale fuer freie Strategien...'),
        (0.9, 'INFO', 'Max. Positionen (6) erreicht. Stoppe Signal-Suche.')]), encoding='utf-8')
    assert mod.explain_from_log('DOGEUSDTUSDT_6h', DECISION).startswith('Positionsgrenze erreicht')


def test_bot_not_running_and_missing_log(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'LOG_DIR', str(tmp_path))
    (tmp_path / 'mbot_ADAUSDTUSDT_1d.log').write_text(_lines([(-600, 'INFO', 'Kein MERS-Signal fuer ADA.')]),
                                                    encoding='utf-8')
    assert mod.explain_from_log('ADAUSDTUSDT_1d', DECISION).startswith('Bot lief in dem Zeitraum nicht')
    assert mod.explain_from_log('ETHUSDTUSDT_6h', DECISION) == 'kein Bot-Log vorhanden'


def test_active_periods_follow_team_history():
    history = [
        {'valid_from': '2026-10-14T13:00:00+00:00', 'team': {'XRPUSDTUSDT_6h': None, 'SOLUSDTUSDT_6h': None}},
        {'valid_from': '2026-10-21T13:00:00+00:00', 'team': {'XRPUSDTUSDT_6h': None}},
    ]
    ts = pd.Timestamp
    assert mod.active_periods(history, 'SOLUSDTUSDT_6h') == [
        (ts('2026-10-14T13:00:00+00:00'), ts('2026-10-21T13:00:00+00:00'))]


def test_position_without_stop_loss_is_reported():
    pos = [{'symbol': 'XRPUSDT', 'holdSide': 'short', 'markPrice': '1.2781'},
           {'symbol': 'ARBUSDT', 'holdSide': 'long', 'markPrice': '0.168'}]
    plans = [  # XRP: nur Trailing (unter dem Kurs, kein SL); ARB: SL an der Einstiegs-Order
        {'symbol': 'XRPUSDT', 'planType': 'track_plan', 'tradeSide': 'close', 'posSide': 'short', 'triggerPrice': '1.22'},
        {'symbol': 'ARBUSDT', 'planType': 'profit_loss', 'tradeSide': 'close', 'posSide': 'long',
         'triggerPrice': '0.108', 'stopLossTriggerPrice': '0.108'},
    ]
    flags = mod.positions_without_sl(pos, plans)
    assert len(flags) == 1 and 'XRP short OHNE Stop-Loss' in flags[0]
    plans.append({'symbol': 'XRPUSDT', 'planType': 'normal_plan', 'tradeSide': 'close', 'posSide': 'short',
                  'triggerPrice': '1.41'})
    assert mod.positions_without_sl(pos, plans) == []
