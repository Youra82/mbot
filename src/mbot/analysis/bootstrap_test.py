#!/usr/bin/env python3
"""
analysis/bootstrap_test.py — Bootstrap Signifikanztest (Port von dnabot)

dnabot testet Genome-Win-Raten gegen 50% (Binomial-Test). mbot hat keine
Genome-DB -- stattdessen wird pro aktivem (symbol, timeframe) CONFIG getestet
ob dessen Win-Rate statistisch signifikant ueber dem Zufallsniveau (50%) liegt.

Frage: Welche der aktiven Strategien koennen ihr Ergebnis NICHT durch Zufall
erklaeren?
"""
import os
import sys
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-samples', type=int,   default=10)
    parser.add_argument('--alpha',       type=float, default=0.05)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  mbot — Bootstrap Signifikanztest\n{'='*60}")
    print(f"  Min. Samples: {args.min_samples} | Alpha: {args.alpha}\n")

    try:
        from scipy import stats
    except ImportError:
        print(f"  {R}scipy fehlt: pip install scipy{NC}")
        sys.exit(1)

    pair_results = [r for r in load_trades() if len(r['trades']) >= args.min_samples]
    if not pair_results:
        print(f"  {R}Keine Pairs mit >= {args.min_samples} Trades gefunden.{NC}"); sys.exit(1)

    print(f"  {len(pair_results)} aktive Pairs mit >={args.min_samples} Trades geladen.")

    results = []
    for r in pair_results:
        trades = r['trades']
        n = len(trades)
        w = sum(1 for t in trades if t.get('result') == 'win')
        wr = w / n
        res = stats.binomtest(w, n, p=0.5, alternative='greater')
        results.append({'label': f"{r['coin']}/{r['timeframe']}", 'market': r['market'],
                         'tf': r['timeframe'], 'wr': wr, 'n': n, 'p': res.pvalue})

    sig  = [r for r in results if r['p'] < args.alpha]
    nsig = [r for r in results if r['p'] >= args.alpha]

    print(f"  Signifikant (p<{args.alpha}): {G}{len(sig)}{NC} / {len(results)} "
          f"({len(sig)/len(results)*100:.1f}%)")
    print(f"  Nicht signifikant:         {Y}{len(nsig)}{NC} / {len(results)}\n")

    print(f"  Alle Pairs (sortiert nach p-Wert):")
    for r in sorted(results, key=lambda x: x['p']):
        col = G if r['p'] < args.alpha else Y
        print(f"  {col}p={r['p']:.4f}{NC} | WR={r['wr']:.1%} | n={r['n']:4d} | {r['label']}")

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    wrs = [r['wr'] for r in results]
    ps  = [r['p']  for r in results]
    ns  = [r['n']  for r in results]
    colors_p = ['#16a34a' if p < args.alpha else '#64748b' for p in ps]

    ax1.scatter(ns, wrs, c=colors_p, alpha=0.7, s=60)
    for r in results:
        ax1.annotate(r['label'], (r['n'], r['wr']), fontsize=7, color='#94a3b8',
                     xytext=(4, 4), textcoords='offset points')
    ax1.axhline(0.5, color='#ef4444', linestyle='--', linewidth=1.5, label='Baseline 50%')
    ax1.set_xlabel('Sample-Groesse (n)')
    ax1.set_ylabel('Win-Rate')
    ax1.set_title(f'Win-Rate vs. Sample-Groesse\n'
                  f'{len(sig)} signifikant (gruen), {len(nsig)} nicht signifikant (grau)')
    ax1.legend(facecolor='#1e293b', labelcolor='white')

    ax2.bar([r['label'] for r in results], ps, color=colors_p, alpha=0.85)
    ax2.axhline(args.alpha, color='#ef4444', linewidth=2, linestyle='--', label=f'α = {args.alpha}')
    ax2.set_xlabel('Pair')
    ax2.set_ylabel('p-Wert')
    ax2.set_title('p-Wert pro aktivem Pair')
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
    ax2.legend(facecolor='#1e293b', labelcolor='white')

    pct = len(sig) / len(results) * 100
    fig.suptitle(f'mbot Bootstrap Signifikanztest | {len(results)} Pairs | '
                 f'{pct:.1f}% statistisch signifikant (p<{args.alpha})',
                 color='white', fontsize=11)
    plt.tight_layout()

    caption = (f"mbot Bootstrap Signifikanztest\n"
               f"{len(results)} aktive Pairs analysiert\n"
               f"Signifikant (p<{args.alpha}): {len(sig)} ({pct:.1f}%)\n"
               f"Nicht signifikant: {len(nsig)} ({100-pct:.1f}%)\n"
               f"Interpretation: Nur signifikante Pairs haben statistisch echte Vorhersagekraft.")
    save_send(fig, 'bootstrap_test', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
