#!/usr/bin/env python3
"""
analysis/strategy_comparison.py — WF Re-Opt vs. Alle Configs (Port von dnabot)

Vergleicht zwei Ansaetze auf demselben Zeitraum (kein Lookahead):

  A) Woechentliche Re-Optimierung (Walk-Forward):
     - Trainings-Fenster (lookback Wochen): Portfolio via mbots eigener
       Greedy-Calmar-Logik (find_best_portfolio aus portfolio_simulator.py,
       dieselbe die run_portfolio_optimizer.py nutzt) auswaehlen
     - Test-Fenster (naechste lookback Wochen): Mit dem gewaehlten Portfolio
       traden -- via run_portfolio_simulation() (Pro-Strategie-Lock, kein
       gemeinsamer ungebremster Kapitalpool wie bei dnabots Modell)
     - Equity akkumuliert rollend ueber die gesamte Periode

  B) Alle Configs dauerhaft (Baseline):
     - Alle vorhandenen Backtest-Ergebnisse (kein active_strategies Filter),
       gemeinsam simuliert via run_portfolio_simulation()

Ausgabe: PNG-Chart + interaktiver HTML-Chart (Plotly) + Telegram-Zusammenfassung.

Ausfuehrung:
  python3 -m mbot.analysis.strategy_comparison
  python3 -m mbot.analysis.strategy_comparison --capital 100 --max-dd 30 --lookback 2 --no-telegram
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *
from mbot.analysis.portfolio_simulator import find_best_portfolio, run_portfolio_simulation

DOCS_DIR = os.path.join(PROJECT_ROOT, 'docs')


def _parse_dt(ts):
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def load_all_results():
    """Laedt ALLE Backtest-Ergebnisse -- kein active_strategies Filter (dnabot-Analogon)."""
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
    """Reichert einen {'symbol','timeframe','trades'} Eintrag mit den Feldern
    an, die find_best_portfolio()/_best_tf_per_coin() erwarten (total_pnl_pct,
    max_drawdown, total_trades) -- via utils.simulate() auf 100 USDT normiert."""
    sim = simulate(r['trades'], 100.0)
    return {**r, 'total_pnl_pct': sim['pnl_pct'], 'max_drawdown': sim['max_dd'], 'total_trades': sim['n']}


def _calmar(pnl_pct, max_dd):
    return pnl_pct / max_dd if max_dd > 0 else pnl_pct


def simulate_walkforward(all_results, capital, max_dd, lookback_weeks, min_trades, start_dt, end_dt):
    """Rolling Walk-Forward mit mbots eigener Greedy-Portfolio-Auswahl."""
    window_delta = timedelta(weeks=lookback_weeks)
    windows = []
    t = start_dt
    while t + window_delta * 2 <= end_dt:
        windows.append((t, t + window_delta, t + window_delta * 2))
        t += window_delta

    if not windows:
        return [(start_dt, capital)], []

    curve  = [(start_dt, capital)]
    wstats = []
    equity = capital

    for train_start, train_end, test_end in windows:
        train_window = _filter_window(all_results, train_start, train_end)
        train_window = {fn: r for fn, r in train_window.items() if len(r['trades']) >= min_trades}
        pseudo = {fn: _pseudo_backtest_result(r) for fn, r in train_window.items()}

        selected_keys = []
        if pseudo:
            portfolio = find_best_portfolio(pseudo, 100.0, max_dd, verbose=False)
            if portfolio and portfolio.get('selected'):
                selected_keys = portfolio['selected']

        test_window = _filter_window(all_results, train_end, test_end)
        test_subset = {fn: test_window[fn] for fn in selected_keys if fn in test_window}

        equity_before = equity
        if test_subset:
            port = run_portfolio_simulation(test_subset, equity)
            equity = port['end_capital']
            for tt in port['trades']:
                curve.append((tt.get('exit_dt') or _parse_dt(tt.get('exit_time', tt.get('entry_time'))),
                              tt['portfolio_capital_after']))
            n_trades, wins, window_dd = port['total_trades'], port['wins'], port['max_drawdown']
        else:
            n_trades, wins, window_dd = 0, 0, 0.0
            curve.append((test_end, equity))

        pnl_pct = (equity - equity_before) / equity_before * 100.0 if equity_before > 0 else 0.0
        wstats.append({
            'train_start': train_start, 'train_end': train_end, 'test_end': test_end,
            'selected': [f"{all_results[fn]['symbol'].split('/')[0]}/{all_results[fn]['timeframe']}"
                         for fn in selected_keys],
            'n_trades': n_trades, 'pnl_pct': pnl_pct, 'max_dd': window_dd, 'wins': wins,
        })

    return curve, wstats


def simulate_all_configs(all_results, capital, start_dt, end_dt):
    window_results = _filter_window(all_results, start_dt, end_dt)
    curve = [(start_dt, capital)]
    if not window_results:
        return curve, {'pnl_pct': 0.0, 'max_dd': 0.0, 'wr': 0.0, 'n': 0, 'equity': capital}
    port = run_portfolio_simulation(window_results, capital)
    for tt in port['trades']:
        curve.append((_parse_dt(tt.get('exit_time', tt.get('entry_time'))), tt['portfolio_capital_after']))
    return curve, {
        'pnl_pct': port['total_pnl_pct'], 'max_dd': port['max_drawdown'],
        'wr': port['win_rate'] / 100.0, 'n': port['total_trades'], 'equity': port['end_capital'],
    }


def _drawdown_series(curve):
    peak = curve[0][1]
    dds = []
    for dt, eq in curve:
        if eq > peak:
            peak = eq
        dds.append((dt, (peak - eq) / peak * 100.0 if peak > 0 else 0.0))
    return dds


def generate_png_chart(curve_wf, curve_all, wstats, stats_all, capital, lookback_weeks, no_telegram):
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.patches import Patch
    except ImportError:
        print(f"{R}  matplotlib nicht installiert — PNG uebersprungen.{NC}")
        return None

    dd_wf, dd_all = _drawdown_series(curve_wf), _drawdown_series(curve_all)
    times_wf, eq_wf   = [p[0] for p in curve_wf],  [p[1] for p in curve_wf]
    times_all, eq_all = [p[0] for p in curve_all], [p[1] for p in curve_all]

    pnl_wf    = (eq_wf[-1] - capital) / capital * 100.0 if capital > 0 else 0.0
    max_dd_wf = max((p[1] for p in dd_wf), default=0.0)
    n_wf      = sum(w['n_trades'] for w in wstats)
    wins_wf   = sum(w['wins'] for w in wstats)
    wr_wf     = wins_wf / n_wf if n_wf > 0 else 0.0

    DARK_BG, PANEL_BG = '#0f172a', '#1e293b'
    COLOR_WF, COLOR_ALL = '#2563eb', '#f97316'
    GRID_COL, TEXT_COL, WHITE = '#334155', '#94a3b8', '#f1f5f9'

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True,
                                    gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.06})
    fig.patch.set_facecolor(DARK_BG)
    for ax in (ax1, ax2):
        ax.set_facecolor(PANEL_BG)
        ax.tick_params(colors=TEXT_COL, labelsize=9)
        ax.spines[:].set_color(GRID_COL)
        ax.grid(True, alpha=0.2, color=GRID_COL, linewidth=0.7)
        ax.xaxis.label.set_color(TEXT_COL)
        ax.yaxis.label.set_color(TEXT_COL)

    ax1.plot(times_all, eq_all, color=COLOR_ALL, linewidth=1.6, alpha=0.85,
             label=f'Alle Configs  |  PnL: {stats_all["pnl_pct"]:+.1f}%  |  MaxDD: {stats_all["max_dd"]:.1f}%  '
                   f'|  WR: {stats_all["wr"]:.1%}  |  n={stats_all["n"]}')
    ax1.plot(times_wf, eq_wf, color=COLOR_WF, linewidth=2.2,
             label=f'WF Re-Opt ({lookback_weeks}w)  |  PnL: {pnl_wf:+.1f}%  |  MaxDD: {max_dd_wf:.1f}%  '
                   f'|  WR: {wr_wf:.1%}  |  n={n_wf}')
    ax1.axhline(y=capital, color='#475569', linewidth=0.9, linestyle='--', alpha=0.6)
    for w in wstats:
        ax1.axvline(x=w['train_end'], color='#1e40af', linewidth=0.6, alpha=0.35, linestyle=':')
    ax1.set_ylabel('Equity (USDT)', color=TEXT_COL, fontsize=10)
    ax1.legend(facecolor='#1e293b', edgecolor=GRID_COL, labelcolor=WHITE, fontsize=8.5, loc='upper left')

    winner     = 'WF Re-Opt' if pnl_wf > stats_all['pnl_pct'] else 'Alle Configs'
    winner_col = COLOR_WF if pnl_wf > stats_all['pnl_pct'] else COLOR_ALL
    fig.suptitle(f'mbot Strategie-Vergleich  —  WF Re-Opt ({lookback_weeks}w) vs. Alle Configs  —  Gewinner: ',
                 fontsize=12, color=TEXT_COL, y=0.98)
    fig.text(0.72, 0.978, winner, fontsize=12, color=winner_col, fontweight='bold')

    ax2.fill_between([p[0] for p in dd_all], [p[1] for p in dd_all], alpha=0.35, color=COLOR_ALL)
    ax2.fill_between([p[0] for p in dd_wf],  [p[1] for p in dd_wf],  alpha=0.45, color=COLOR_WF)
    ax2.plot([p[0] for p in dd_all], [p[1] for p in dd_all], color=COLOR_ALL, linewidth=1.2, alpha=0.7)
    ax2.plot([p[0] for p in dd_wf],  [p[1] for p in dd_wf],  color=COLOR_WF,  linewidth=1.5)
    ax2.invert_yaxis()
    ax2.set_ylabel('Drawdown %', color=TEXT_COL, fontsize=9)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha='right', fontsize=8)
    ax2.legend(handles=[Patch(color=COLOR_WF, label=f'WF Re-Opt ({lookback_weeks}w)'),
                        Patch(color=COLOR_ALL, label='Alle Configs')],
               facecolor='#1e293b', edgecolor=GRID_COL, labelcolor=WHITE, fontsize=8, loc='lower left')

    output_path = '/tmp/mbot_strategy_comparison.png'
    docs_path   = os.path.join(DOCS_DIR, 'strategy_comparison_latest.png')
    os.makedirs(DOCS_DIR, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor=DARK_BG)
    fig.savefig(docs_path, dpi=150, bbox_inches='tight', facecolor=DARK_BG)
    plt.close(fig)
    print(f"  {G}✓ PNG-Chart: {output_path}{NC}")

    if not no_telegram:
        token, chat_id = get_telegram()
        if token:
            caption = (f"mbot Strategie-Vergleich\n"
                       f"WF Re-Opt ({lookback_weeks}w): {pnl_wf:+.1f}% | MaxDD: {max_dd_wf:.1f}% | "
                       f"WR: {wr_wf:.1%} | {n_wf} Trades\n"
                       f"Alle Configs: {stats_all['pnl_pct']:+.1f}% | MaxDD: {stats_all['max_dd']:.1f}% | "
                       f"WR: {stats_all['wr']:.1%} | {stats_all['n']} Trades\n"
                       f"Gewinner: {winner}")
            send_photo(token, chat_id, output_path, caption)
            print(f"  {G}✓ PNG via Telegram gesendet.{NC}")
    return output_path


def generate_html_chart(curve_wf, curve_all, wstats, stats_all, capital, lookback_weeks, no_telegram):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print(f"{R}  plotly nicht installiert — HTML uebersprungen.{NC}")
        return None

    dd_wf, dd_all = _drawdown_series(curve_wf), _drawdown_series(curve_all)
    times_wf, eq_wf   = [p[0] for p in curve_wf],  [p[1] for p in curve_wf]
    times_all, eq_all = [p[0] for p in curve_all], [p[1] for p in curve_all]

    pnl_wf    = (eq_wf[-1] - capital) / capital * 100.0 if capital > 0 else 0.0
    max_dd_wf = max((p[1] for p in dd_wf), default=0.0)
    n_wf      = sum(w['n_trades'] for w in wstats)
    wins_wf   = sum(w['wins'] for w in wstats)
    wr_wf     = wins_wf / n_wf if n_wf > 0 else 0.0
    winner    = 'WF Re-Opt' if pnl_wf > stats_all['pnl_pct'] else 'Alle Configs'

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                        vertical_spacing=0.04, subplot_titles=['Equity-Verlauf', 'Drawdown (%)'])
    fig.add_trace(go.Scatter(x=times_all, y=eq_all,
        name=f'Alle Configs  {stats_all["pnl_pct"]:+.1f}% | MaxDD {stats_all["max_dd"]:.1f}% | WR {stats_all["wr"]:.1%} | n={stats_all["n"]}',
        line=dict(color='#f97316', width=1.8), opacity=0.85), row=1, col=1)
    fig.add_trace(go.Scatter(x=times_wf, y=eq_wf,
        name=f'WF Re-Opt ({lookback_weeks}w)  {pnl_wf:+.1f}% | MaxDD {max_dd_wf:.1f}% | WR {wr_wf:.1%} | n={n_wf}',
        line=dict(color='#2563eb', width=2.5)), row=1, col=1)
    fig.add_hline(y=capital, line=dict(color='rgba(148,163,184,0.35)', width=1, dash='dash'), row=1, col=1)
    for w in wstats:
        fig.add_vline(x=w['train_end'].isoformat(),
                      line=dict(color='rgba(37,99,235,0.25)', width=1, dash='dot'), row=1, col=1)
    fig.add_trace(go.Scatter(x=[p[0] for p in dd_all], y=[-p[1] for p in dd_all], name='DD Alle Configs',
        line=dict(color='#f97316', width=1.5), fill='tozeroy', fillcolor='rgba(249,115,22,0.15)', opacity=0.7),
        row=2, col=1)
    fig.add_trace(go.Scatter(x=[p[0] for p in dd_wf], y=[-p[1] for p in dd_wf], name='DD WF Re-Opt',
        line=dict(color='#2563eb', width=2), fill='tozeroy', fillcolor='rgba(37,99,235,0.2)'), row=2, col=1)

    fig.update_layout(
        title=dict(text=f'mbot Strategie-Vergleich  —  Gewinner: <b>{winner}</b>  |  Kapital: {capital:.0f} USDT',
                   font=dict(size=14), x=0.5, xanchor='center'),
        height=800, hovermode='x unified', template='plotly_dark',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5, font=dict(size=9)),
        xaxis2=dict(rangeslider=dict(visible=True)),
    )
    fig.update_yaxes(title_text='Equity (USDT)', row=1, col=1)
    fig.update_yaxes(title_text='Drawdown %', row=2, col=1)

    output_path = '/tmp/mbot_strategy_comparison.html'
    fig.write_html(output_path)
    print(f"  {G}✓ HTML-Chart: {output_path}{NC}")

    if not no_telegram:
        token, chat_id = get_telegram()
        if token:
            try:
                import requests
                with open(output_path, 'rb') as f:
                    requests.post(f'https://api.telegram.org/bot{token}/sendDocument',
                                  data={'chat_id': chat_id,
                                        'caption': f"mbot Strategie-Vergleich (interaktiv)\nGewinner: {winner}"},
                                  files={'document': (os.path.basename(output_path), f)}, timeout=30)
                print(f"  {G}✓ HTML via Telegram gesendet.{NC}")
            except Exception as e:
                print(f"  Telegram Fehler: {e}")
    return output_path


def print_summary(curve_wf, curve_all, wstats, stats_all, capital, lookback_weeks, no_telegram):
    pnl_wf    = (curve_wf[-1][1] - capital) / capital * 100.0 if capital > 0 else 0.0
    max_dd_wf = max((p[1] for p in _drawdown_series(curve_wf)), default=0.0)
    n_wf      = sum(w['n_trades'] for w in wstats)
    wins_wf   = sum(w['wins'] for w in wstats)
    wr_wf     = wins_wf / n_wf if n_wf > 0 else 0.0
    calmar_wf = _calmar(pnl_wf, max_dd_wf)
    calmar_all = _calmar(stats_all['pnl_pct'], stats_all['max_dd'])

    col_wf  = G if pnl_wf >= 0 else R
    col_all = G if stats_all['pnl_pct'] >= 0 else R

    w = 72
    print(f"\n{'=' * w}\n  mbot Strategie-Vergleich\n"
          f"  WF Re-Opt ({lookback_weeks}w Fenster)  vs.  Alle Configs dauerhaft\n{'=' * w}")
    print(f"\n  {'Kennzahl':<22} {'WF Re-Opt':>18} {'Alle Configs':>18}")
    print(f"  {'-' * (w - 2)}")
    rows = [
        ('PnL %',        f"{col_wf}{pnl_wf:+.1f}%{NC}", f"{col_all}{stats_all['pnl_pct']:+.1f}%{NC}"),
        ('Final Equity', f"{curve_wf[-1][1]:.2f} USDT", f"{stats_all['equity']:.2f} USDT"),
        ('Max Drawdown', f"{max_dd_wf:.1f}%", f"{stats_all['max_dd']:.1f}%"),
        ('Calmar Ratio', f"{calmar_wf:.2f}", f"{calmar_all:.2f}"),
        ('Win-Rate',     f"{wr_wf:.1%}", f"{stats_all['wr']:.1%}"),
        ('Trades',       str(n_wf), str(stats_all['n'])),
        ('WF Fenster',   str(len(wstats)), '—'),
    ]
    for label, vwf, vall in rows:
        print(f"  {label:<22} {vwf:>28} {vall:>28}")

    winner = 'WF Re-Opt' if calmar_wf > calmar_all else 'Alle Configs'
    print(f"\n  {'─' * (w - 2)}\n  Gewinner (nach Calmar-Ratio): {G if winner=='WF Re-Opt' else Y}{winner}{NC}")

    if wstats:
        print(f"\n  Fenster-Uebersicht (Walk-Forward)")
        print(f"  {'Zeitraum':<26} {'Pairs':>5} {'n':>5} {'PnL%':>8} {'MaxDD':>8}")
        print(f"  {'-' * (w - 2)}")
        for ws in wstats:
            date_str = f"{ws['train_end'].strftime('%Y-%m-%d')} → {ws['test_end'].strftime('%Y-%m-%d')}"
            col = G if ws['pnl_pct'] >= 0 else R
            print(f"  {date_str:<26} {len(ws['selected']):>5} {ws['n_trades']:>5} "
                  f"{col}{ws['pnl_pct']:>+6.1f}%{NC} {ws['max_dd']:>7.1f}%")
    print(f"\n{'=' * w}\n")

    if not no_telegram:
        token, chat_id = get_telegram()
        if token:
            try:
                import requests
                msg = (f"mbot Strategie-Vergleich\n\n"
                       f"WF Re-Opt ({lookback_weeks}w Fenster):\n"
                       f"  PnL: {pnl_wf:+.1f}% | MaxDD: {max_dd_wf:.1f}%\n"
                       f"  WR: {wr_wf:.1%} | Calmar: {calmar_wf:.2f} | {n_wf} Trades\n\n"
                       f"Alle Configs:\n"
                       f"  PnL: {stats_all['pnl_pct']:+.1f}% | MaxDD: {stats_all['max_dd']:.1f}%\n"
                       f"  WR: {stats_all['wr']:.1%} | Calmar: {calmar_all:.2f} | {stats_all['n']} Trades\n\n"
                       f"Gewinner (Calmar): {winner}")
                requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                              data={'chat_id': chat_id, 'text': msg}, timeout=10)
                print(f"  {G}✓ Zusammenfassung via Telegram gesendet.{NC}")
            except Exception as e:
                print(f"  Telegram Fehler: {e}")


def main():
    default_capital  = 100.0
    default_lookback = 2
    try:
        s = load_settings()
        default_capital  = float(s.get('optimization_settings', {}).get('start_capital', 100.0))
    except Exception:
        pass

    parser = argparse.ArgumentParser(description='mbot Strategie-Vergleich')
    parser.add_argument('--capital',     type=float, default=default_capital)
    parser.add_argument('--max-dd',      type=float, default=30.0)
    parser.add_argument('--lookback',    type=int,   default=default_lookback,
                        help='Fenster-Groesse in Wochen (Training + Test je lookback Wochen)')
    parser.add_argument('--min-trades',  type=int,   default=2)
    parser.add_argument('--start-date',  type=str,   default=None)
    parser.add_argument('--end-date',    type=str,   default=None)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'─' * 72}\n  mbot Strategie-Vergleich\n"
          f"  WF Re-Opt ({args.lookback}w) vs. Alle Configs  |  Kapital: {args.capital:.0f} USDT  |  "
          f"MaxDD-Limit: {args.max_dd:.0f}%\n{'─' * 72}\n")

    print("  Lade Backtest-Ergebnisse ...", end='', flush=True)
    all_results = load_all_results()
    if not all_results:
        print(f"\n{R}  Keine Backtest-Ergebnisse gefunden. Erst run_backtest.py ausfuehren!{NC}\n")
        sys.exit(1)

    all_trades = [t for r in all_results.values() for t in r['trades']]
    if not all_trades:
        print(f"\n{R}  Keine Trades in den Ergebnissen.{NC}\n"); sys.exit(1)

    all_times  = sorted(t['entry_dt'] for t in all_trades)
    data_start = all_times[0].replace(hour=0, minute=0, second=0, microsecond=0)
    data_end   = all_times[-1].replace(hour=23, minute=59, second=59, microsecond=0)
    start_dt = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc) if args.start_date else data_start
    end_dt   = datetime.fromisoformat(args.end_date).replace(tzinfo=timezone.utc) if args.end_date else data_end

    print(f" {len(all_results)} Configs, {len(all_trades)} Trades. "
          f"Zeitraum: {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}")

    min_period = timedelta(weeks=args.lookback * 2)
    if (end_dt - start_dt) < min_period:
        print(f"\n{R}  Zeitraum zu kurz fuer Walk-Forward (mind. {args.lookback * 2} Wochen).{NC}\n")
        sys.exit(1)

    print(f"\n  Schritt 1/2: Walk-Forward Re-Opt ({args.lookback}w Fenster) ...")
    curve_wf, wstats = simulate_walkforward(all_results, args.capital, args.max_dd,
                                            args.lookback, args.min_trades, start_dt, end_dt)
    n_wf = sum(w['n_trades'] for w in wstats)
    print(f"  {G}✓{NC} {len(wstats)} Fenster, {n_wf} Trades, Final Equity: {curve_wf[-1][1]:.2f} USDT")

    print(f"\n  Schritt 2/2: Alle Configs (keine Filterung) ...")
    curve_all, stats_all = simulate_all_configs(all_results, args.capital, start_dt, end_dt)
    print(f"  {G}✓{NC} {stats_all['n']} Trades, Final Equity: {stats_all['equity']:.2f} USDT")

    print_summary(curve_wf, curve_all, wstats, stats_all, args.capital, args.lookback, args.no_telegram)

    print("  Erstelle Charts ...")
    generate_png_chart(curve_wf, curve_all, wstats, stats_all, args.capital, args.lookback, args.no_telegram)
    generate_html_chart(curve_wf, curve_all, wstats, stats_all, args.capital, args.lookback, args.no_telegram)


if __name__ == '__main__':
    main()
