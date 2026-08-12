#!/usr/bin/env python3
"""
walk_forward_test.py — Walk-Forward Analyse: Optimaler Lookback-Zeitraum (Port von dnabot)

Untersucht welcher Lookback-Zeitraum (Wochen zurueck) fuer die woechentliche
Portfolio-Optimierung (run_portfolio_optimizer.py) am besten Out-of-Sample
performt. Nutzt dieselbe Greedy-Calmar-Portfolioauswahl wie
run_portfolio_optimizer.py (find_best_portfolio aus portfolio_simulator.py)
statt einer eigenen Auswahl-Logik.

Methode: Rolling Walk-Forward (kein Lookahead)
  Fuer jeden Lookback (1, 2, 4, 8, 12, 26 Wochen):
    Pro Test-Woche:
      In-Sample     → letzte N Wochen: Portfolio via find_best_portfolio waehlen
      Out-of-Sample → naechste Woche: Portfolio anwenden (run_portfolio_simulation),
                       Equity akkumuliert rollend weiter

  Alle Lookbacks laufen auf demselben OOS-Zeitraum (fairer Vergleich).

Ausfuehrung:
    python3 walk_forward_test.py
    python3 walk_forward_test.py --capital 100 --min-trades 2 --no-telegram
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from mbot.analysis.utils import simulate, get_telegram, send_photo, load_settings, G, Y, R, C, NC, RESULTS_DIR
from mbot.analysis.portfolio_simulator import find_best_portfolio, run_portfolio_simulation

OUTPUT_PATH      = '/tmp/mbot_walkforward.png'
OUTPUT_PATH_DOCS = os.path.join(PROJECT_ROOT, 'docs', 'walkforward_latest.png')

LOOKBACK_WINDOWS = [1, 2, 4, 8, 12, 26]  # Wochen


def _parse_dt(ts):
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def next_monday(dt):
    """Rundet auf den naechsten Montag (oder bleibt beim Montag)."""
    days = (7 - dt.weekday()) % 7
    return (dt + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def load_all_results():
    """Laedt ALLE Backtest-Ergebnisse -- kein active_strategies Filter (der
    Walk-Forward bewertet den Optimizer der alle Pairs scannt, nicht nur
    die aktuell aktiven)."""
    results = {}
    if not os.path.isdir(RESULTS_DIR):
        return results
    for fname in sorted(os.listdir(RESULTS_DIR)):
        if not fname.startswith('backtest_') or not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(RESULTS_DIR, fname)) as f:
                data = json.load(f)
        except Exception:
            continue
        parsed = []
        for t in data.get('trades', []):
            dt = _parse_dt(t.get('entry_time', ''))
            if dt:
                t['entry_dt'] = dt
                parsed.append(t)
        if parsed:
            results[fname] = {'symbol': data['market'], 'timeframe': data['timeframe'], 'trades': parsed}
    return results


def _filter_window(results, start_dt, end_dt):
    out = {}
    for fn, r in results.items():
        trades = [t for t in r['trades'] if start_dt <= t['entry_dt'] < end_dt]
        if trades:
            out[fn] = {**r, 'trades': trades}
    return out


def _pseudo_backtest_result(r):
    """Reichert {'symbol','timeframe','trades'} mit den Feldern an, die
    find_best_portfolio() braucht (total_pnl_pct, max_drawdown, total_trades)."""
    sim = simulate(r['trades'], 100.0)
    return {**r, 'total_pnl_pct': sim['pnl_pct'], 'max_drawdown': sim['max_dd'], 'total_trades': sim['n']}


def run_walk_forward(all_results, lookback_weeks, min_trades, week_starts, capital, max_dd_limit):
    """Walk-Forward fuer einen Lookback-Zeitraum. Equity akkumuliert rollend."""
    equity = capital
    curve  = []
    total_n, total_wins, empty_weeks = 0, 0, 0

    for week_start in week_starts:
        is_start = week_start - timedelta(weeks=lookback_weeks)
        oos_end  = week_start + timedelta(weeks=1)

        train_window = _filter_window(all_results, is_start, week_start)
        train_window = {fn: r for fn, r in train_window.items() if len(r['trades']) >= min_trades}
        pseudo = {fn: _pseudo_backtest_result(r) for fn, r in train_window.items()}

        selected_keys = []
        if pseudo:
            portfolio = find_best_portfolio(pseudo, 100.0, max_dd_limit, verbose=False)
            if portfolio and portfolio.get('selected'):
                selected_keys = portfolio['selected']

        test_window = _filter_window(all_results, week_start, oos_end)
        test_subset = {fn: test_window[fn] for fn in selected_keys if fn in test_window}

        if not test_subset:
            empty_weeks += 1
            curve.append((oos_end, equity, len(selected_keys), 0))
            continue

        port = run_portfolio_simulation(test_subset, equity)
        equity = port['end_capital']
        total_n    += port['total_trades']
        total_wins += port['wins']
        curve.append((oos_end, equity, len(selected_keys), port['total_trades']))

    return curve, total_n, total_wins, empty_weeks


def compute_stats(curve, capital):
    """Berechnet PnL%, MaxDD% und Calmar aus einer Equity-Kurve."""
    if not curve:
        return 0.0, 0.0, 0.0
    final_eq = curve[-1][1]
    pnl_pct  = (final_eq - capital) / capital * 100.0
    eq_vals  = [capital] + [e[1] for e in curve]
    peak, max_dd = eq_vals[0], 0.0
    for e in eq_vals:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    calmar = pnl_pct / max_dd if max_dd > 0 else pnl_pct
    return calmar, pnl_pct, max_dd


def create_chart(results, week_starts, capital):
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print(f"  {R}matplotlib nicht installiert.{NC}")
        return None

    COLORS = ['#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed', '#0891b2']
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 11))
    fig.patch.set_facecolor('#0f172a')
    for ax in (ax1, ax2):
        ax.set_facecolor('#1e293b')
        ax.tick_params(colors='#94a3b8')
        ax.xaxis.label.set_color('#94a3b8')
        ax.yaxis.label.set_color('#94a3b8')
        ax.spines[:].set_color('#334155')
        ax.grid(True, alpha=0.15, color='#475569')

    ax1.axhline(y=capital, color='#475569', linestyle='--', alpha=0.5, linewidth=0.8)
    ax1.text(week_starts[0], capital * 1.02, f'Start {capital:.0f} USDT', color='#475569', fontsize=8)

    summary_data = []
    bar_labels, bar_calmar, bar_colors = [], [], []

    for i, (weeks, (curve, n_total, n_wins, empty_w)) in enumerate(sorted(results.items())):
        calmar, pnl_pct, max_dd = compute_stats(curve, capital)
        wr = n_wins / n_total * 100 if n_total > 0 else 0.0

        dates    = [week_starts[0]] + [e[0] for e in curve]
        equities = [capital]        + [e[1] for e in curve]

        color = COLORS[i % len(COLORS)]
        label = f"{weeks:2}W Lookback: {pnl_pct:+.0f}% | DD {max_dd:.1f}% | Calmar {calmar:.1f}"
        ax1.plot(dates, equities, color=color, linewidth=2, label=label, zorder=3)

        bar_labels.append(f'{weeks}W')
        bar_calmar.append(calmar)
        bar_colors.append(color)
        summary_data.append({'weeks': weeks, 'calmar': calmar, 'pnl_pct': pnl_pct,
                              'max_dd': max_dd, 'n_total': n_total, 'wr': wr, 'empty_w': empty_w})

    ax1.set_title(f'mbot Walk-Forward — Lookback-Vergleich (Out-of-Sample)\n'
                  f'Startkapital: {capital:.0f} USDT | Test-Wochen: {len(week_starts)}',
                  color='white', fontsize=12, pad=10)
    ax1.set_ylabel('Equity (USDT)', color='#94a3b8')
    ax1.legend(fontsize=9, loc='upper left', framealpha=0.3, facecolor='#1e293b', labelcolor='white')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax1.xaxis.set_major_locator(mdates.MonthLocator())
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha='right', color='#94a3b8')

    try:
        all_eq = [capital] + [e[1] for c, _ in results.items() for e in _[0]]
        min_eq = min(e for e in all_eq if e > 0)
        ax1.set_yscale('log')
        ax1.set_ylim(bottom=max(1, min_eq * 0.5))
    except Exception:
        pass

    ax2.set_title('Calmar Score pro Lookback (Out-of-Sample, hoeher = besser)', color='white', fontsize=11, pad=8)
    bars = ax2.bar(bar_labels, bar_calmar, color=bar_colors, alpha=0.8, edgecolor='#1e293b', linewidth=1.2)
    for bar, score in zip(bars, bar_calmar):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + abs(max(bar_calmar, default=1)) * 0.01,
                 f'{score:.1f}', ha='center', va='bottom', color='white', fontsize=11, fontweight='bold')
    if bar_calmar:
        best_idx = bar_calmar.index(max(bar_calmar))
        bars[best_idx].set_edgecolor('#fbbf24')
        bars[best_idx].set_linewidth(3)
        ax2.text(bars[best_idx].get_x() + bars[best_idx].get_width() / 2,
                 -abs(max(bar_calmar, default=1)) * 0.08, '★ BEST', ha='center', va='top',
                 color='#fbbf24', fontsize=9, fontweight='bold')
    ax2.set_xlabel('Lookback-Zeitraum', color='#94a3b8')
    ax2.set_ylabel('Calmar Score (OOS)', color='#94a3b8')
    ax2.axhline(y=0, color='#475569', linewidth=0.8)

    plt.tight_layout(pad=2.5)
    plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight', facecolor='#0f172a')
    os.makedirs(os.path.dirname(OUTPUT_PATH_DOCS), exist_ok=True)
    plt.savefig(OUTPUT_PATH_DOCS, dpi=150, bbox_inches='tight', facecolor='#0f172a')
    plt.close()
    return OUTPUT_PATH, summary_data


def main():
    parser = argparse.ArgumentParser(description='mbot Walk-Forward Lookback Analyse')
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--min-trades',  type=int,   default=2,
                        help='Min. Trades pro Pair im Lookback-Fenster')
    parser.add_argument('--max-dd',      type=float, default=30.0,
                        help='Max. Drawdown-Limit fuer die In-Sample Portfolio-Auswahl (%%)')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    capital = args.capital

    print(f"\n{'=' * 62}")
    print(f"  mbot — Walk-Forward Lookback Analyse")
    print(f"{'=' * 62}")
    print(f"  Startkapital: {capital} USDT")
    print(f"  Min. Trades:  {args.min_trades} (pro Pair im Lookback-Fenster)")
    print(f"  MaxDD-Limit:  {args.max_dd}% (In-Sample Portfolio-Auswahl)")
    print(f"  Lookbacks:    {LOOKBACK_WINDOWS} Wochen")
    print()

    print("  Lade Backtest-Daten...", end='', flush=True)
    all_results = load_all_results()
    if not all_results:
        print(f"\n  {R}Keine Backtest-Daten. Erst run_backtest.py ausfuehren!{NC}\n")
        sys.exit(1)

    all_dts  = [t['entry_dt'] for r in all_results.values() for t in r['trades']]
    min_date, max_date = min(all_dts), max(all_dts)
    n_weeks  = (max_date - min_date).days // 7
    print(f" {len(all_results)} Pairs | {min_date.strftime('%Y-%m-%d')} → "
          f"{max_date.strftime('%Y-%m-%d')} ({n_weeks} Wochen)")

    active_lookbacks = [w for w in LOOKBACK_WINDOWS if w + 1 <= n_weeks]
    skipped = [w for w in LOOKBACK_WINDOWS if w not in active_lookbacks]
    if skipped:
        print(f"  {Y}Uebersprungen (zu wenig Daten): {skipped}W{NC}")
    if not active_lookbacks:
        print(f"  {R}Nicht genug Daten.{NC}\n")
        sys.exit(1)

    max_lookback = max(active_lookbacks)
    oos_start = next_monday(min_date + timedelta(weeks=max_lookback))
    oos_end   = next_monday(max_date)

    week_starts = []
    w = oos_start
    while w < oos_end:
        week_starts.append(w)
        w += timedelta(weeks=1)

    if len(week_starts) < 2:
        print(f"  {R}Zu wenig OOS-Wochen ({len(week_starts)}).{NC}\n")
        sys.exit(1)

    print(f"  OOS-Zeitraum: {oos_start.strftime('%Y-%m-%d')} → "
          f"{oos_end.strftime('%Y-%m-%d')} ({len(week_starts)} Test-Wochen)")
    print()

    results = {}
    for weeks in active_lookbacks:
        print(f"  {C}Lookback {weeks:2d}W ...{NC}", end='', flush=True)
        curve, n_total, n_wins, empty_w = run_walk_forward(
            all_results, weeks, args.min_trades, week_starts, capital, args.max_dd
        )
        calmar, pnl_pct, max_dd = compute_stats(curve, capital)
        wr = n_wins / n_total * 100 if n_total > 0 else 0.0
        results[weeks] = (curve, n_total, n_wins, empty_w)
        col = G if pnl_pct > 0 else R
        print(f"  {col}PnL={pnl_pct:+.1f}% | DD={max_dd:.1f}% | Calmar={calmar:.1f} | "
              f"Trades={n_total} | WR={wr:.1f}% | Leerwochen={empty_w}{NC}")

    best_weeks = max(results, key=lambda w: compute_stats(results[w][0], capital)[0])
    bc, bp, bd = compute_stats(results[best_weeks][0], capital)

    print()
    print(f"  {'─' * 50}")
    print(f"  {G}★ Bester Lookback: {best_weeks} Wochen{NC}")
    print(f"  Calmar: {bc:.1f} | PnL: {bp:+.1f}% | MaxDD: {bd:.1f}%")
    print(f"  {'─' * 50}")

    rec_date = (datetime.now(timezone.utc) - timedelta(weeks=best_weeks)).strftime('%Y-%m-%d')
    print()
    print(f"  {Y}Empfehlung fuer den woechentlichen Optimizer-Lookback:{NC}")
    print(f"  optimization_settings.start_date ≈ \"{rec_date}\" (= {best_weeks} Wochen rollierender Lookback)")

    print()
    chart_result = create_chart(results, week_starts, capital)
    if chart_result is None:
        sys.exit(0)

    chart_path, summary_data = chart_result
    print(f"  {G}✓ Chart gespeichert: {chart_path}{NC}")

    print()
    print(f"{'=' * 62}")
    print(f"  {'Lookback':<10} {'PnL%':>8} {'MaxDD%':>8} {'Calmar':>8} {'Trades':>7} {'WR':>7} {'LeerW':>6}")
    print(f"  {'─' * 56}")
    for d in sorted(summary_data, key=lambda x: x['calmar'], reverse=True):
        marker = '★' if d['weeks'] == best_weeks else ' '
        col = G if d['pnl_pct'] > 0 else R
        print(f"  {marker}{d['weeks']:2d}W       {col}{d['pnl_pct']:>+8.1f}%{NC} "
              f"{d['max_dd']:>7.1f}% {d['calmar']:>8.1f} {d['n_total']:>7} {d['wr']:>6.1f}% {d['empty_w']:>6}")
    print(f"{'=' * 62}")

    if not args.no_telegram:
        token, chat_id = get_telegram()
        if token and chat_id:
            caption_lines = [
                "mbot Walk-Forward — Lookback-Analyse (Out-of-Sample)",
                f"Zeitraum: {oos_start.strftime('%Y-%m-%d')} → {oos_end.strftime('%Y-%m-%d')} "
                f"({len(week_starts)} Wochen)",
                f"Startkapital: {capital:.0f} USDT", "",
            ]
            for d in sorted(summary_data, key=lambda x: x['calmar'], reverse=True):
                marker = "★ " if d['weeks'] == best_weeks else "  "
                caption_lines.append(f"{marker}{d['weeks']:2d}W: {d['pnl_pct']:+.1f}% | "
                                      f"DD {d['max_dd']:.1f}% | Calmar {d['calmar']:.1f} | "
                                      f"{d['n_total']} Trades | WR {d['wr']:.1f}%")
            caption_lines.append("")
            caption_lines.append(f"★ Bester Lookback: {best_weeks} Wochen (Calmar {bc:.1f})")
            print()
            print("  Sende Chart via Telegram...", end='', flush=True)
            send_photo(token, chat_id, chart_path, "\n".join(caption_lines))
            print(f" {G}✓{NC}")
        else:
            print(f"  {Y}Telegram nicht konfiguriert — nur lokaler Chart.{NC}")

    print(f"\n  {G}Walk-Forward Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
