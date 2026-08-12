#!/usr/bin/env python3
"""
analysis/regime_analysis.py — Regime Performance Analysis (Port von dnabot)

dnabot liest Regime-Spalten aus der Genome-DB (occ_trend/wins_trend etc.).
mbot hat keine Genome-DB -- stattdessen traegt jeder Trade seit Step 0 ein
'regime'-Feld ('trend'|'range'|'chaos'), das der Backtester pro Kerze aus der
MDEF-Phasenraum-Klassifikation vorberechnet.

Hinweis: use_regime_filter blockt per Default bereits 'chaos' (und optional
'range'), daher werden Live-Configs meist ueberwiegend 'trend'/'range' Trades
zeigen. Fehlende 'chaos'-Trades sind daher normal, kein Fehler.
"""
import os
import sys
import argparse
from collections import defaultdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *

REGIMES = ['trend', 'range', 'chaos']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-samples', type=int, default=10)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  mbot — Regime Performance Analysis\n{'='*60}")
    print(f"  Hinweis: use_regime_filter blockt 'chaos' (und optional 'range') per")
    print(f"  Default -- fehlende Chaos-Trades sind daher normal, kein Fehler.\n")

    pair_results = load_trades()
    if not pair_results:
        print(f"  {R}Keine Backtest-Daten.{NC}"); sys.exit(1)

    market_regime = defaultdict(lambda: {r: {'wins': 0, 'occ': 0} for r in REGIMES})
    for r in pair_results:
        key = f"{r['coin']}/{r['timeframe']}"
        for t in r['trades']:
            reg = t.get('regime', 'n/a')
            if reg not in REGIMES:
                continue
            market_regime[key][reg]['occ']  += 1
            if t.get('result') == 'win':
                market_regime[key][reg]['wins'] += 1

    markets = sorted(market_regime.keys())
    wr_matrix = []
    for m in markets:
        row = []
        for reg in REGIMES:
            d = market_regime[m][reg]
            row.append(d['wins'] / d['occ'] if d['occ'] >= args.min_samples else float('nan'))
        wr_matrix.append(row)

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, max(6, len(markets) * 0.4 + 2)))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    mat = np.array(wr_matrix, dtype=float)
    masked = np.ma.masked_invalid(mat)
    im = ax1.imshow(masked, cmap='RdYlGn', vmin=0.3, vmax=0.7, aspect='auto')
    ax1.set_xticks(range(len(REGIMES))); ax1.set_xticklabels(REGIMES, color='white')
    ax1.set_yticks(range(len(markets))); ax1.set_yticklabels(markets, fontsize=8, color='white')
    plt.colorbar(im, ax=ax1, label='Win-Rate')
    for i in range(len(markets)):
        for j in range(len(REGIMES)):
            if not np.isnan(mat[i, j]):
                ax1.text(j, i, f'{mat[i,j]:.0%}', ha='center', va='center',
                         color='black' if 0.3 < mat[i, j] < 0.7 else 'white', fontsize=7)
    ax1.set_title('Win-Rate Heatmap: Pair × Regime\n(gruen=gut, rot=schlecht, grau=zu wenig Daten)')

    avg_by_regime = {}
    for j, reg in enumerate(REGIMES):
        vals = [mat[i, j] for i in range(len(markets)) if not np.isnan(mat[i, j])]
        avg_by_regime[reg] = sum(vals) / len(vals) if vals else 0

    valid_regimes = [reg for reg in REGIMES if avg_by_regime[reg] > 0]
    ax2.bar(valid_regimes, [avg_by_regime[r] for r in valid_regimes],
            color=['#2563eb', '#16a34a', '#f59e0b'][:len(valid_regimes)], alpha=0.8)
    ax2.axhline(0.5, color='#ef4444', linestyle='--', linewidth=1.5, label='Break-Even 50%')
    for i, reg in enumerate(valid_regimes):
        ax2.text(i, avg_by_regime[reg] + 0.005, f'{avg_by_regime[reg]:.1%}',
                  ha='center', va='bottom', color='white', fontsize=11)
    ax2.set_xlabel('Markt-Regime')
    ax2.set_ylabel('Durchschnittliche Win-Rate')
    ax2.set_title('Ø Win-Rate pro Regime (alle Pairs)')
    ax2.legend(facecolor='#1e293b', labelcolor='white')
    if valid_regimes:
        ax2.set_ylim(0, max(avg_by_regime[r] for r in valid_regimes) * 1.15)

    fig.suptitle(f'mbot Regime Performance | {len(markets)} Pairs | '
                 f'min_samples={args.min_samples}', color='white', fontsize=11)
    plt.tight_layout()

    if not valid_regimes:
        print(f"  {R}Keine Regime-Daten mit min_samples={args.min_samples}.{NC}"); sys.exit(1)
    best_regime = max(valid_regimes, key=lambda r: avg_by_regime[r])
    print(f"  {len(markets)} Pairs analysiert.")
    for reg in REGIMES:
        print(f"  {reg:<8} Ø Win-Rate: {avg_by_regime.get(reg, 0):.1%}" if avg_by_regime.get(reg, 0) > 0
              else f"  {reg:<8} (zu wenig Daten, min_samples={args.min_samples})")
    print(f"\n  Bestes Regime: {best_regime} ({avg_by_regime[best_regime]:.1%})")

    caption = (f"mbot Regime Performance Analysis\n"
               f"{len(markets)} Pairs | min_samples={args.min_samples}\n\n"
               f"Ø Win-Rate pro Regime:\n"
               + "\n".join(f"  {reg}: {avg_by_regime[reg]:.1%}" for reg in valid_regimes)
               + f"\n\nBestes Regime: {best_regime} ({avg_by_regime[best_regime]:.1%})")
    save_send(fig, 'regime_analysis', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
