#!/usr/bin/env python3
"""
analysis/sensitivity.py — Parameter Sensitivity Analysis (Port von dnabot, verbessert)

dnabot approximiert Sensitivitaet aus vorhandenen Trades (Neu-Scan der Genome-DB
waere teuer). mbots Backtester ist dagegen guenstig direkt aufrufbar -- daher
wird hier ECHTE Sensitivitaet gemessen: fuer jeden aktiven (symbol, timeframe)
Config wird je ein MERS-Parameter um +-30% variiert und komplett neu gebacktestet
(genauer als dnabots Trade-Filter-Approximation, da tatsaechliche Signal-
Aenderungen erfasst werden, nicht nur nachtraeglich gefilterte Trades).

Ausfuehrung:
  python3 -m mbot.analysis.sensitivity
  python3 -m mbot.analysis.sensitivity --pairs 1 --no-telegram   (schneller Test)
"""
import os
import sys
import json
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *
from mbot.utils.exchange import Exchange
from mbot.analysis.backtester import load_data, run_backtest, FINE_TF_MAP, LazyFineData

PARAM_RANGES = {
    'entropy_window':       0.30,
    'min_entropy_drop_pct': 0.30,
    'min_energy_rise_pct':  0.30,
    'atr_sl_mult':          0.30,
    'atr_tp_mult':          0.30,
    'regime_window':        0.30,
}


def vary(base_val, pct):
    return [round(base_val * (1 - pct), 6), base_val, round(base_val * (1 + pct), 6)]


def _make_exchange():
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    return Exchange(secrets['mbot'][0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--pairs',       type=int,   default=None,
                        help='Nur die ersten N aktiven Pairs testen (Schnelltest)')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  mbot — Parameter Sensitivity Analysis (echte Re-Backtests)\n{'='*60}")

    active = sorted(load_active_pairs())
    if args.pairs:
        active = active[:args.pairs]
    if not active:
        print(f"  {R}Keine aktiven Strategien in settings.json.{NC}"); sys.exit(1)

    exchange = _make_exchange()

    agg = {p: {'low': [], 'high': []} for p in PARAM_RANGES}

    for symbol, tf in active:
        try:
            cfg = load_config(symbol, tf)
        except Exception as e:
            print(f"  {Y}Config fehlt fuer {symbol} {tf}: {e}{NC}"); continue

        meta    = cfg.get('_meta', {})
        sig_cfg = cfg.get('signal', {})
        start_date = meta.get('start_date', '2024-01-01')
        end_date   = meta.get('end_date', '2099-01-01')

        print(f"\n  {C}{symbol} ({tf}){NC} | {start_date} -> {end_date}")
        df = load_data(exchange, symbol, tf, start_date, end_date)
        if df is None or df.empty:
            print(f"  {Y}Keine Daten, skip.{NC}"); continue

        fine_tf   = FINE_TF_MAP.get(tf)
        fine_data = LazyFineData(symbol, fine_tf) if fine_tf else None
        risk_config = {'risk_per_trade_pct': sig_cfg.get('risk_per_trade_pct', 1.0)}

        base_result = run_backtest(df, sig_cfg, risk_config, args.capital, symbol, fine_data=fine_data)
        base_pnl = base_result.get('total_pnl_pct', 0.0)
        print(f"    Basis PnL: {base_pnl:+.1f}% | Trades: {base_result.get('total_trades', 0)}")

        for pname, pct in PARAM_RANGES.items():
            base_val = sig_cfg.get(pname)
            if base_val is None:
                continue
            values = vary(base_val, pct)
            deltas = []
            for v in values:
                trial_cfg = {**sig_cfg, pname: v}
                r = run_backtest(df, trial_cfg, risk_config, args.capital, symbol, fine_data=fine_data)
                deltas.append(r.get('total_pnl_pct', 0.0) - base_pnl)
            low_delta, high_delta = deltas[0], deltas[2]
            agg[pname]['low'].append(low_delta)
            agg[pname]['high'].append(high_delta)
            print(f"    {pname:<24} -{pct*100:.0f}%: {low_delta:+7.1f}pp  "
                  f"+{pct*100:.0f}%: {high_delta:+7.1f}pp")

    sensitivity = {p: {'low': sum(d['low'])/len(d['low']), 'high': sum(d['high'])/len(d['high'])}
                   for p, d in agg.items() if d['low']}

    if not sensitivity:
        print(f"\n  {R}Keine Sensitivitaets-Daten erzeugt.{NC}"); sys.exit(1)

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, max(5, len(sensitivity) * 1.2 + 2)))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax)

    order = sorted(sensitivity, key=lambda p: abs(sensitivity[p]['low']) + abs(sensitivity[p]['high']), reverse=True)
    names = [p.replace('_', ' ') for p in order]
    lows  = [sensitivity[p]['low']  for p in order]
    highs = [sensitivity[p]['high'] for p in order]

    y = list(range(len(names)))
    ax.barh(y, highs, left=0, color='#16a34a', alpha=0.8, label='+30% Variation')
    ax.barh(y, lows,  left=0, color='#ef4444', alpha=0.8, label='-30% Variation')
    ax.axvline(0, color='white', linewidth=1.5)
    ax.set_yticks(y)
    ax.set_yticklabels(names, color='white')
    ax.set_xlabel('Ø PnL%-Aenderung ggue. Basis (ueber alle getesteten Pairs)')
    ax.set_title('Parameter Sensitivity — Tornado Diagramm (echte Re-Backtests)\n'
                 'Breiter Balken = groessere Sensitivitaet = fragiler Parameter',
                 color='white')
    ax.legend(facecolor='#1e293b', labelcolor='white')

    fig.suptitle(f'mbot Parameter Sensitivity | {len(active)} Pair(s) getestet',
                 color='white', fontsize=11)
    plt.tight_layout()

    lines = [f"mbot Parameter Sensitivity (echte Re-Backtests)", f"{len(active)} Pair(s) getestet\n"]
    for n, l, h in zip(names, lows, highs):
        span = abs(h) + abs(l)
        robust = "robust" if span < 20 else ("sensitiv" if span < 60 else "FRAGIL")
        lines.append(f"{n}: Ø-Span {span:.0f}pp → {robust}")
    save_send(fig, 'sensitivity', "\n".join(lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
