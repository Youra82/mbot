#!/usr/bin/env python3
"""
analysis/volatility_filter.py — Volatilitaets-Filter Optimierung (Port von dnabot, EXPLORATIV)

dnabot optimiert einen ATR-Multiple-Schwellwert fuer sein HIGH_VOL Genome-
Regime-Blocking. mbot hat aktuell KEIN aequivalentes explizites "block wenn
ATR-Ratio > X" Gate -- classify_phase_regime() handhabt Volatilitaet bereits
implizit ueber Phasenraum-Dynamik, nicht ueber einen rohen ATR-Ratio-Cutoff.

Dieses Skript testet die SPIRIT der dnabot-Analyse als Was-waere-wenn-Frage:
Haette ein ZUSAETZLICHES, NOCH NICHT IMPLEMENTIERTES ATR-Ratio-Gate
(aktueller ATR / rollierender ATR-MA, Schwellwert 1.5x/2.0x/2.5x/3.0x, Signal
blocken wenn ueberschritten) die OOS-Ergebnisse verbessert? Es ist ein
Post-hoc-Filter auf der bestehenden Trade-Liste (kein Re-Backtest -- ATR wird
aus bereits geladenen OHLCV-Daten pro Pair berechnet, daher billiger als
sensitivity.py/param_optimizer.py). EXPLORATIV: testet einen hypothetischen
Filter, keine bereits im Code existierende Funktion.
"""
import os
import sys
import json
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *
from mbot.utils.exchange import Exchange
from mbot.analysis.backtester import load_data
from mbot.strategy.mdef_analysis import calc_atr

ATR_MA_WINDOW = 50
THRESHOLDS = [1.5, 2.0, 2.5, 3.0]


def _make_exchange():
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    return Exchange(secrets['mbot'][0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  mbot — Volatilitaets-Filter Optimierung (EXPLORATIV)\n{'='*60}")
    print(f"  Testet ein hypothetisches, NICHT implementiertes ATR-Ratio-Gate.\n")

    pair_results = load_trades()
    if not pair_results:
        print(f"  {R}Keine Backtest-Daten.{NC}"); sys.exit(1)

    exchange = _make_exchange()
    enriched = []
    ratios_all = []

    for r in pair_results:
        symbol, tf = r['market'], r['timeframe']
        try:
            cfg = load_config(symbol, tf)
        except Exception as e:
            print(f"  {Y}Config fehlt fuer {symbol} {tf}: {e}{NC}"); continue

        sig_cfg = cfg.get('signal', {})
        atr_period = int(sig_cfg.get('atr_period', 14))
        meta = cfg.get('_meta', {})
        start_date = meta.get('start_date', '2024-01-01')
        end_date   = meta.get('end_date', '2099-01-01')

        df = load_data(exchange, symbol, tf, start_date, end_date)
        if df is None or df.empty:
            print(f"  {Y}Keine Daten fuer {symbol} {tf}, skip.{NC}"); continue

        atr_ser = calc_atr(df, period=atr_period)
        atr_ma  = atr_ser.rolling(ATR_MA_WINDOW, min_periods=1).mean()
        ratio_ser = atr_ser / atr_ma.replace(0, float('nan'))

        for t in r['trades']:
            pos = df.index.searchsorted(t['entry_dt'], side='right') - 1
            if pos < 0 or pos >= len(df):
                continue
            ratio = ratio_ser.iloc[pos]
            if ratio != ratio:  # NaN check
                continue
            enriched.append((t, float(ratio)))
            ratios_all.append(float(ratio))

    if not enriched:
        print(f"  {R}Keine Trades mit ATR-Ratio angereichert.{NC}"); sys.exit(1)

    print(f"  {len(enriched)} Trades mit ATR-Ratio angereichert "
          f"(Median: {sorted(ratios_all)[len(ratios_all)//2]:.2f}).")

    baseline_trades = [t for t, _ in enriched]
    base_r = simulate(baseline_trades, args.capital)
    print(f"\n  Baseline (kein Filter): PnL {base_r['pnl_pct']:+.1f}% | "
          f"Calmar {base_r['calmar']:.1f} | n={base_r['n']}")

    print(f"\n  {'ATR-Ratio<=':>12} {'Trades':>8} {'WR':>7} {'PnL%':>8} {'Calmar':>8}")
    print(f"  {'-'*48}")
    results = []
    for thresh in THRESHOLDS:
        filtered = [t for t, ratio in enriched if ratio <= thresh]
        if len(filtered) < 5:
            continue
        r = simulate(filtered, args.capital)
        results.append({'thresh': thresh, **r})
        col = G if r['pnl_pct'] > 0 else R
        print(f"  {thresh:>11.1f}x {r['n']:>8}  {r['wr']:>6.1%}  {col}{r['pnl_pct']:>+7.1f}%{NC}  {r['calmar']:>8.1f}")

    if not results:
        print(f"\n  {R}Keine Schwellwerte mit genug Trades.{NC}"); sys.exit(1)
    best = max(results, key=lambda x: x['calmar'])

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(*axes)

    axes[0].hist(ratios_all, bins=50, color='#2563eb', alpha=0.8, edgecolor='none')
    axes[0].axvline(best['thresh'], color='#fbbf24', linewidth=2, linestyle='--',
                     label=f'Optimal (hypothetisch): {best["thresh"]:.1f}x')
    axes[0].set_xlabel('ATR / ATR-MA(50) Ratio')
    axes[0].set_ylabel('Anzahl Trades')
    axes[0].set_title('Verteilung der ATR-Ratios bei Entry')
    axes[0].legend(facecolor='#1e293b', labelcolor='white')

    lbls    = [f"{r['thresh']:.1f}x" for r in results]
    calmars = [r['calmar'] for r in results]
    bar_cols = ['#fbbf24' if r['thresh'] == best['thresh'] else '#2563eb' for r in results]
    axes[1].bar(lbls, calmars, color=bar_cols, alpha=0.8)
    axes[1].axhline(base_r['calmar'], color='#ef4444', linestyle='--', linewidth=1.5,
                     label=f'Baseline (kein Filter): {base_r["calmar"]:.1f}')
    axes[1].set_xlabel('Hypothetischer ATR-Ratio-Schwellwert')
    axes[1].set_ylabel('Calmar')
    axes[1].set_title('Calmar pro Schwellwert (gelb = optimal)\nEXPLORATIV -- Filter nicht implementiert')
    axes[1].legend(facecolor='#1e293b', labelcolor='white')

    fig.suptitle(f'mbot Volatilitaets-Filter (EXPLORATIV) | {len(enriched)} Trades | '
                 f'Optimal: ATR-Ratio<={best["thresh"]:.1f}x', color='white', fontsize=11)
    plt.tight_layout()

    caption = (f"mbot Volatilitaets-Filter Optimierung (EXPLORATIV -- Filter nicht implementiert)\n"
               f"{len(enriched)} Trades | ATR/ATR-MA(50) als Proxy\n\n"
               f"Baseline: Calmar {base_r['calmar']:.1f} | PnL {base_r['pnl_pct']:+.1f}%\n"
               + "\n".join(f"ATR-Ratio<={r['thresh']:.1f}x: Calmar {r['calmar']:.1f} | "
                            f"WR {r['wr']:.1%} | {r['n']} Trades" for r in results)
               + f"\n\n★ Optimal (hypothetisch): {best['thresh']:.1f}x (Calmar {best['calmar']:.1f})")
    save_send(fig, 'volatility_filter', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
