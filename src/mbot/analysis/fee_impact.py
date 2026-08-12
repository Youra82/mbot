#!/usr/bin/env python3
"""
analysis/fee_impact.py — Slippage & Fee Impact Analyse (Port von dnabot)

Zeigt wie eine ZUSAETZLICHE Gebuehr/Slippage (oben auf die im Backtest bereits
gebackene Bitget-Gebuehr aus risk.fee_rate_pct) die Performance beeinflusst.
Break-Even: ab welcher zusaetzlichen Gebuehr wird der Bot unrentabel?

Positionsgroesse wird pro Trade aus risk_per_trade_pct und SL-Abstand
rekonstruiert (mbot speichert entry_price/sl_price direkt, keinen sl_pct).

Ausfuehrung:
  python3 -m mbot.analysis.fee_impact
  python3 -m mbot.analysis.fee_impact --capital 100 --no-telegram
"""
import os
import sys
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *

# Getestete zusaetzliche Gebuehren pro Seite (oben auf Bitget 0.06% Taker)
FEE_LEVELS = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]
# Zusaetzliche Slippage bei SL-Execution (% des Notionals)
SLIPPAGE_LEVELS = [0.0, 0.05, 0.10, 0.15, 0.20]


def simulate_with_fees(all_trades, capital, extra_fee_pct, extra_slippage_pct):
    """Simuliert chronologisch mit zusaetzlicher Gebuehr/Slippage oben auf pnl_pct."""
    equity = capital
    peak   = equity
    max_dd = 0.0
    wins   = 0
    for t in all_trades:
        entry    = t.get('entry_price', 0.0)
        sl       = t.get('sl_price', entry)
        risk_pct = t.get('risk_per_trade_pct', 1.0)
        sl_dist  = abs(entry - sl)
        risk_amount   = equity * risk_pct / 100.0
        pos_contracts = risk_amount / sl_dist if sl_dist > 0 else 0.0
        notional      = pos_contracts * entry

        pnl_usdt = equity * t.get('pnl_pct', 0.0) / 100.0
        pnl_usdt -= notional * (extra_fee_pct / 100.0) * 2.0
        if t.get('result') == 'loss':
            pnl_usdt -= notional * (extra_slippage_pct / 100.0)
        else:
            wins += 1

        equity = max(equity + pnl_usdt, 0.0)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

    n = len(all_trades)
    pnl_pct = (equity - capital) / capital * 100.0 if capital > 0 else 0.0
    wr = wins / n * 100.0 if n > 0 else 0.0
    calmar = pnl_pct / max_dd if max_dd > 0 else pnl_pct
    return {'equity': equity, 'pnl_pct': pnl_pct, 'max_dd': max_dd,
            'calmar': calmar, 'wr': wr, 'n': n}


def create_chart(results_fee, results_slip, trades, capital):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(*axes)

    ax1 = axes[0]
    ax1.set_title('Gebuehren-Impact (Slippage=0%)', color='white', fontsize=11)
    fee_vals = [r['fee'] for r in results_fee]
    pnl_vals = [r['pnl_pct'] for r in results_fee]
    calmar_vals = [r['calmar'] for r in results_fee]
    ax1.bar([f"{f:.2f}%" for f in fee_vals], pnl_vals,
            color=['#16a34a' if p > 0 else '#ef4444' for p in pnl_vals], alpha=0.8)
    ax1.axhline(y=0, color='#ef4444', linewidth=1, linestyle='--')
    ax1.set_xlabel('Zusaetzliche Gebuehr/Seite')
    ax1.set_ylabel('PnL%')
    for i, (p, c) in enumerate(zip(pnl_vals, calmar_vals)):
        col = 'white' if p > 0 else '#ef4444'
        ax1.text(i, p + (abs(max(pnl_vals, default=1)) * 0.02), f'{p:+.0f}%\nCalmar:{c:.0f}',
                 ha='center', va='bottom', color=col, fontsize=8)

    ax2 = axes[1]
    ax2.set_title('Slippage-Impact (Gebuehr=0.06%/Seite)', color='white', fontsize=11)
    slip_vals = [r['slip'] for r in results_slip]
    pnl_slip  = [r['pnl_pct'] for r in results_slip]
    ax2.bar([f"{s:.2f}%" for s in slip_vals], pnl_slip,
            color=['#16a34a' if p > 0 else '#ef4444' for p in pnl_slip], alpha=0.8)
    ax2.axhline(y=0, color='#ef4444', linewidth=1, linestyle='--')
    ax2.set_xlabel('Zusaetzliche Slippage bei SL')
    ax2.set_ylabel('PnL%')
    for i, p in enumerate(pnl_slip):
        col = 'white' if p > 0 else '#ef4444'
        ax2.text(i, p + (abs(max(pnl_slip, default=1)) * 0.02), f'{p:+.0f}%',
                 ha='center', va='bottom', color=col, fontsize=9)

    n = len(trades)
    wr_base = results_fee[0]['wr']
    fig.suptitle(f'mbot Fee & Slippage Impact | {n} Trades | WR {wr_base:.1f}% | '
                 f'Startkapital: {capital:.0f} USDT', color='white', fontsize=11)
    plt.tight_layout()
    return fig


def main():
    parser = argparse.ArgumentParser(description='mbot Fee & Slippage Impact')
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'=' * 60}\n  mbot — Slippage & Fee Impact Analyse\n{'=' * 60}")
    print(f"  Startkapital: {args.capital} USDT")
    print(f"  Bitget Taker-Gebuehr (bereits im Backtest): 0.06%/Seite\n")

    pair_results = load_trades()
    if not pair_results:
        print(f"  {R}Keine Backtest-Daten. Erst run_backtest.py ausfuehren.{NC}"); sys.exit(1)
    trades = all_trades_flat(pair_results)
    print(f"  {len(trades)} Trades geladen.\n")

    print(f"  {'Gebuehr/Seite':>14}  {'PnL%':>10}  {'MaxDD%':>8}  {'Calmar':>8}  {'WR':>6}")
    print(f"  {'-' * 54}")
    results_fee = []
    for fee in FEE_LEVELS:
        r = simulate_with_fees(trades, args.capital, fee, 0.0)
        results_fee.append({**r, 'fee': fee})
        col = G if r['pnl_pct'] > 0 else R
        marker = ' ← Bitget' if abs(fee - 0.06) < 0.001 else ''
        print(f"  {fee:>12.2f}%  {col}{r['pnl_pct']:>+9.1f}%{NC}  "
              f"{r['max_dd']:>7.1f}%  {r['calmar']:>8.1f}  {r['wr']:>5.1f}%{marker}")

    break_even = None
    for i in range(len(results_fee) - 1):
        if results_fee[i]['pnl_pct'] > 0 and results_fee[i + 1]['pnl_pct'] <= 0:
            break_even = (FEE_LEVELS[i] + FEE_LEVELS[i + 1]) / 2
            break
    print()
    if break_even:
        print(f"  {Y}Break-Even zusaetzliche Gebuehr: ~{break_even:.2f}%/Seite{NC}")
    else:
        print(f"  {G if results_fee[0]['pnl_pct'] > 0 else R}"
              f"{'Profitabel bei allen getesteten Gebuehren.' if results_fee[0]['pnl_pct'] > 0 else 'Nicht profitabel — auch ohne zusaetzliche Gebuehren.'}{NC}")

    print(f"\n  Slippage-Impact (zusaetzliche Gebuehr fix 0.06%/Seite):")
    print(f"  {'Slippage':>10}  {'PnL%':>10}  {'MaxDD%':>8}  {'Calmar':>8}")
    print(f"  {'-' * 44}")
    results_slip = []
    for slip in SLIPPAGE_LEVELS:
        r = simulate_with_fees(trades, args.capital, 0.06, slip)
        results_slip.append({**r, 'slip': slip})
        col = G if r['pnl_pct'] > 0 else R
        print(f"  {slip:>8.2f}%  {col}{r['pnl_pct']:>+9.1f}%{NC}  {r['max_dd']:>7.1f}%  {r['calmar']:>8.1f}")

    fig = create_chart(results_fee, results_slip, trades, args.capital)
    if fig is not None:
        bitget_result = next((r for r in results_fee if abs(r['fee'] - 0.06) < 0.001), results_fee[0])
        caption = (f"mbot Fee & Slippage Impact\n"
                   f"{len(trades)} Trades | WR {results_fee[0]['wr']:.1f}%\n\n"
                   f"Ohne Zusatzgebuehr: {results_fee[0]['pnl_pct']:+.1f}%\n"
                   f"Mit +0.06%/Seite: {bitget_result['pnl_pct']:+.1f}%\n"
                   + (f"Break-Even: ~{break_even:.2f}%/Seite" if break_even else ""))
        save_send(fig, 'fee_impact', caption, args.no_telegram)

    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
