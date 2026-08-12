#!/usr/bin/env python3
"""
analysis/monte_carlo.py — Monte Carlo Simulation (Port von dnabot)

Simuliert N zufaellige Trade-Reihenfolgen auf Basis der echten pnl_pct/result-
Verteilung aus den Backtest-Daten (mbot-Trades tragen bereits realisiertes
pnl_pct, kein Risk%/RR-Modell noetig wie bei dnabot).

Beantwortet:
  - Was ist das schlechteste realistisch moegliche Ergebnis?
  - Mit welcher Wahrscheinlichkeit verliert man mehr als X%?
  - Wie hoch ist die Ruin-Wahrscheinlichkeit (Equity < 50% Start)?

Ausfuehrung:
  python3 -m mbot.analysis.monte_carlo
  python3 -m mbot.analysis.monte_carlo --simulations 10000 --capital 100 --no-telegram
"""
import os
import sys
import random
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *


def simulate_path(pnl_pcts, capital):
    """Simuliert einen einzelnen Pfad mit zufaelliger Trade-Reihenfolge (compounding)."""
    shuffled = list(pnl_pcts)
    random.shuffle(shuffled)
    equity = capital
    peak   = equity
    max_dd = 0.0
    for pnl_pct in shuffled:
        equity *= (1.0 + pnl_pct / 100.0)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return equity, max_dd


def create_chart(final_equities, max_dds, capital, n_sims, n_trades):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    pnl_pcts = [(e - capital) / capital * 100 for e in final_equities]

    # Hinweis: equity *= (1+pnl_pct/100) ist kommutativ -- die Trade-REIHENFOLGE
    # aendert die finale Equity praktisch nicht (nur den Pfad/Drawdown dazwischen).
    # Bins entsprechend an die tatsaechliche Streuung anpassen, sonst crasht
    # np.histogram bei (nahezu) einem einzigen Wert.
    pnl_range = max(pnl_pcts) - min(pnl_pcts) if pnl_pcts else 0.0
    bins1 = 80 if pnl_range > 1e-6 else 1
    ax1.hist(pnl_pcts, bins=bins1, color='#2563eb', alpha=0.7, edgecolor='none')
    p5  = float(np.percentile(pnl_pcts, 5))
    p50 = float(np.percentile(pnl_pcts, 50))
    p95 = float(np.percentile(pnl_pcts, 95))
    ax1.axvline(p5,  color='#ef4444', linewidth=2, linestyle='--', label=f'5. Perzentil: {p5:+.0f}%')
    ax1.axvline(p50, color='#fbbf24', linewidth=2, linestyle='-',  label=f'Median: {p50:+.0f}%')
    ax1.axvline(p95, color='#16a34a', linewidth=2, linestyle='--', label=f'95. Perzentil: {p95:+.0f}%')
    ax1.axvline(0, color='white', linewidth=1, alpha=0.4)
    ax1.set_xlabel('PnL% nach allen Trades')
    ax1.set_ylabel('Haeufigkeit')
    ax1.set_title('Verteilung der Endkapitale', color='white')
    ax1.legend(fontsize=9, facecolor='#1e293b', labelcolor='white', framealpha=0.5)

    dd_range = max(max_dds) - min(max_dds) if max_dds else 0.0
    bins2 = 60 if dd_range > 1e-6 else 1
    ax2.hist(max_dds, bins=bins2, color='#dc2626', alpha=0.7, edgecolor='none')
    dd50 = float(np.percentile(max_dds, 50))
    dd95 = float(np.percentile(max_dds, 95))
    ax2.axvline(dd50, color='#fbbf24', linewidth=2, linestyle='-',  label=f'Median MaxDD: {dd50:.1f}%')
    ax2.axvline(dd95, color='#ef4444', linewidth=2, linestyle='--', label=f'95. Perzentil MaxDD: {dd95:.1f}%')
    ax2.set_xlabel('Maximaler Drawdown (%)')
    ax2.set_ylabel('Haeufigkeit')
    ax2.set_title('Verteilung der Max Drawdowns', color='white')
    ax2.legend(fontsize=9, facecolor='#1e293b', labelcolor='white', framealpha=0.5)

    ruin = sum(1 for e in final_equities if e < capital * 0.5) / n_sims * 100
    fig.suptitle(f'mbot Monte Carlo | {n_sims:,} Simulationen | {n_trades} Trades | '
                 f'Ruin-Wahrsch. (<50%): {ruin:.1f}%', color='white', fontsize=11)
    plt.tight_layout()
    return fig


def main():
    parser = argparse.ArgumentParser(description='mbot Monte Carlo Simulation')
    parser.add_argument('--simulations', type=int,   default=10000)
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'=' * 60}\n  mbot — Monte Carlo Simulation\n{'=' * 60}")
    print(f"  Simulationen: {args.simulations:,}")
    print(f"  Startkapital: {args.capital} USDT")
    print(f"  Hinweis: Da equity multiplikativ compoundet (equity *= 1+pnl_pct/100),")
    print(f"  aendert die Trade-Reihenfolge die FINALE Equity kaum (Multiplikation")
    print(f"  ist kommutativ) -- nur der Drawdown-Pfad ist reihenfolge-abhaengig.\n")

    trades = all_trades_flat()
    if not trades:
        print(f"  {R}Keine Backtest-Daten. Erst run_backtest.py ausfuehren.{NC}"); sys.exit(1)

    pnl_pcts_raw = [t.get('pnl_pct', 0.0) for t in trades]
    wins = sum(1 for t in trades if t.get('result') == 'win')
    n    = len(trades)
    print(f"  {n} Trades | WR {wins/n*100:.1f}%")

    print(f"  Simuliere {args.simulations:,} Pfade...", end='', flush=True)
    random.seed(42)
    final_equities, max_dds = [], []
    for _ in range(args.simulations):
        eq, dd = simulate_path(pnl_pcts_raw, args.capital)
        final_equities.append(eq)
        max_dds.append(dd)
    print(" fertig.\n")

    pnl_pcts = sorted((e - args.capital) / args.capital * 100 for e in final_equities)
    max_dds_sorted = sorted(max_dds)

    p5  = pnl_pcts[int(0.05 * len(pnl_pcts))]
    p25 = pnl_pcts[int(0.25 * len(pnl_pcts))]
    p50 = pnl_pcts[int(0.50 * len(pnl_pcts))]
    p75 = pnl_pcts[int(0.75 * len(pnl_pcts))]
    p95 = pnl_pcts[int(0.95 * len(pnl_pcts))]
    dd50 = max_dds_sorted[int(0.50 * len(max_dds_sorted))]
    dd95 = max_dds_sorted[int(0.95 * len(max_dds_sorted))]

    ruin       = sum(1 for e in final_equities if e < args.capital * 0.5) / args.simulations * 100
    profitable = sum(1 for p in pnl_pcts if p > 0) / args.simulations * 100

    print(f"  {'─' * 50}")
    print(f"  PnL% Verteilung ({args.simulations:,} Simulationen):")
    print(f"  {'5. Perzentil (schlechteste 5%):':<36} {p5:>+8.1f}%")
    print(f"  {'25. Perzentil:':<36} {p25:>+8.1f}%")
    print(f"  {'Median (50. Perzentil):':<36} {p50:>+8.1f}%")
    print(f"  {'75. Perzentil:':<36} {p75:>+8.1f}%")
    print(f"  {'95. Perzentil (beste 5%):':<36} {p95:>+8.1f}%")
    print(f"  {'─' * 50}")
    print(f"  {'Max Drawdown Median:':<36} {dd50:>8.1f}%")
    print(f"  {'Max Drawdown 95. Perzentil:':<36} {dd95:>8.1f}%")
    print(f"  {'─' * 50}")
    col_ruin = R if ruin > 10 else (Y if ruin > 2 else G)
    col_prof = G if profitable > 70 else (Y if profitable > 50 else R)
    print(f"  {'Ruin-Wahrscheinlichkeit (<50%):':36} {col_ruin}{ruin:>8.1f}%{NC}")
    print(f"  {'Profitabel-Wahrscheinlichkeit:':<36} {col_prof}{profitable:>8.1f}%{NC}")
    print(f"  {'─' * 50}\n")

    if ruin < 1:
        print(f"  {G}✓ Sehr geringes Ruin-Risiko (<1%). Strategie ist robust.{NC}")
    elif ruin < 5:
        print(f"  {Y}⚠ Moderates Ruin-Risiko ({ruin:.1f}%). Risiko pruefen.{NC}")
    else:
        print(f"  {R}✗ Hohes Ruin-Risiko ({ruin:.1f}%). Risiko reduzieren!{NC}")

    fig = create_chart(final_equities, max_dds, args.capital, args.simulations, n)
    if fig is not None:
        caption = (f"mbot Monte Carlo | {args.simulations:,} Simulationen\n"
                   f"{n} Trades | WR {wins/n*100:.1f}%\n\n"
                   f"5. Perz.:  {p5:+.0f}%\n"
                   f"Median:    {p50:+.0f}%\n"
                   f"95. Perz.: {p95:+.0f}%\n"
                   f"MaxDD 95%: {dd95:.1f}%\n"
                   f"Ruin-Wahrsch.: {ruin:.1f}%")
        save_send(fig, 'monte_carlo', caption, args.no_telegram)

    print(f"\n  {G}Monte Carlo abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
