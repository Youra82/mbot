#!/usr/bin/env python3
"""
analysis/kelly_sizing.py — Kelly Position Sizing (Port von dnabot)

dnabot berechnet Half-Kelly PRO GENOME mit einem festen RR-Assumption. mbot
hat keine Sub-Pair-Genome-Aufteilung -- stattdessen wird Half-Kelly PRO
(symbol, timeframe) CONFIG berechnet, mit dem tatsaechlichen asymmetrischen
Payoff-Verhaeltnis b = Ø Gewinn% / Ø Verlust% aus den realisierten Trades
(kein fixes RR-Assumption noetig, da mbot-Trades bereits realisiertes
asymmetrisches pnl_pct tragen).

Kelly% = p - (1-p)/b   wobei p = Win-Rate, b = Ø-Gewinn / Ø-Verlust (Betrag)
"""
import os
import sys
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *


def kelly(wr, b):
    """Volle Kelly-Fraction in % fuer asymmetrisches Payoff-Verhaeltnis b."""
    if b <= 0:
        return 0.0
    k = wr - (1 - wr) / b
    return max(k * 100.0, 0.0)


def simulate_rescaled(trades, capital, target_risk_pct):
    """Skaliert jeden Trade von seinem ORIGINALEN risk_per_trade_pct auf
    target_risk_pct um (lineare Positionsgroessen-Annahme) und compoundet."""
    equity = capital
    peak   = equity
    max_dd = 0.0
    wins   = 0
    for t in sorted(trades, key=lambda x: x['entry_dt']):
        orig_risk = t.get('risk_per_trade_pct', 1.0) or 1.0
        scaled_pnl_pct = t.get('pnl_pct', 0.0) * (target_risk_pct / orig_risk)
        equity *= (1.0 + scaled_pnl_pct / 100.0)
        if t.get('result') == 'win':
            wins += 1
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    n = len(trades)
    pnl_pct = (equity - capital) / capital * 100.0 if capital > 0 else 0.0
    wr = wins / n if n > 0 else 0.0
    calmar = pnl_pct / max_dd if max_dd > 0 else pnl_pct
    return {'equity': equity, 'pnl_pct': pnl_pct, 'max_dd': max_dd, 'calmar': calmar, 'wr': wr, 'n': n}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--half-kelly',  action='store_true', default=True,
                        help='Half Kelly (sicherer, Standard: an)')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  mbot — Kelly Position Sizing\n{'='*60}")
    print(f"  Half-Kelly: {args.half_kelly}")

    pair_results = load_trades()
    if not pair_results:
        print(f"  {R}Keine Backtest-Daten.{NC}"); sys.exit(1)

    pair_stats = []
    for r in pair_results:
        trades = r['trades']
        if len(trades) < 5:
            continue
        wins_pnl   = [t['pnl_pct'] for t in trades if t.get('result') == 'win']
        losses_pnl = [abs(t['pnl_pct']) for t in trades if t.get('result') == 'loss']
        wr = len(wins_pnl) / len(trades)
        avg_win  = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0.0
        avg_loss = sum(losses_pnl) / len(losses_pnl) if losses_pnl else 0.01
        b = avg_win / avg_loss if avg_loss > 0 else 0.0
        k = kelly(wr, b)
        if args.half_kelly:
            k /= 2.0

        avg_orig_risk = sum(t.get('risk_per_trade_pct', 1.0) for t in trades) / len(trades)
        fixed_r = simulate(trades, args.capital)
        kelly_r = simulate_rescaled(trades, args.capital, k) if k > 0 else fixed_r
        pair_stats.append({
            'label':        f"{r['coin']}/{r['timeframe']}",
            'wr':           wr,
            'b':            b,
            'kelly_pct':    k,
            'orig_risk':    avg_orig_risk,
            'fixed_calmar': fixed_r['calmar'],
            'kelly_calmar': kelly_r['calmar'],
            'fixed_pnl':    fixed_r['pnl_pct'],
            'kelly_pnl':    kelly_r['pnl_pct'],
        })

    if not pair_stats:
        print(f"  {R}Zu wenig Trades pro Pair (min. 5 benoetigt).{NC}"); sys.exit(1)

    pair_stats.sort(key=lambda x: x['kelly_pct'], reverse=True)

    print(f"\n  {'Pair':<18} {'WR':>6} {'b(W/L)':>7} {'Kelly%':>8} {'Aktuell%':>9} {'Calmar(akt)':>12} {'Calmar(kelly)':>14}")
    print(f"  {'-'*80}")
    for p in pair_stats:
        diff = p['kelly_calmar'] - p['fixed_calmar']
        col  = G if diff > 0 else R
        print(f"  {p['label']:<18} {p['wr']:>5.1%} {p['b']:>7.2f} {p['kelly_pct']:>7.1f}%  "
              f"{p['orig_risk']:>8.1f}%  {p['fixed_calmar']:>11.1f}  {col}{p['kelly_calmar']:>13.1f}{NC}")

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    n      = len(pair_stats)
    labels = [p['label'] for p in pair_stats]
    x      = np.arange(n)
    w      = 0.35

    ax1.bar(x - w/2, [p['fixed_calmar'] for p in pair_stats], w, label='Aktuelles Risiko%',
            color='#2563eb', alpha=0.8)
    ax1.bar(x + w/2, [p['kelly_calmar'] for p in pair_stats], w, label='Kelly%',
            color='#16a34a', alpha=0.8)
    ax1.set_xticks(x); ax1.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax1.set_ylabel('Calmar Score')
    ax1.set_title(f'Calmar: Aktuell vs. {"Half-" if args.half_kelly else ""}Kelly%')
    ax1.legend(facecolor='#1e293b', labelcolor='white')

    ax2.bar(labels, [p['kelly_pct'] for p in pair_stats], color='#7c3aed', alpha=0.8)
    ax2.bar(labels, [p['orig_risk'] for p in pair_stats], color='#ef4444', alpha=0.4,
            label='Aktuelles Risiko%')
    ax2.set_xticks(range(n)); ax2.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax2.set_ylabel('Risiko%')
    ax2.set_title(f'{"Half-" if args.half_kelly else ""}Kelly Position Size pro Pair')
    ax2.legend(facecolor='#1e293b', labelcolor='white')

    fig.suptitle(f'mbot Kelly Position Sizing | {len(pair_stats)} Pairs', color='white', fontsize=11)
    plt.tight_layout()

    improved = sum(1 for p in pair_stats if p['kelly_calmar'] > p['fixed_calmar'])
    caption = (f"mbot Kelly Criterion Analyse\n"
               f"{'Half-Kelly' if args.half_kelly else 'Full Kelly'}\n"
               f"Verbessert durch Kelly: {improved}/{len(pair_stats)} Pairs\n\n"
               f"Kelly% pro Pair:\n"
               + "\n".join(f"{p['label']}: {p['kelly_pct']:.1f}% (WR {p['wr']:.1%}, b={p['b']:.2f}) "
                            f"vs aktuell {p['orig_risk']:.1f}%" for p in pair_stats))
    save_send(fig, 'kelly_sizing', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
