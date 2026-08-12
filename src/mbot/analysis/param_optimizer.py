#!/usr/bin/env python3
"""
analysis/param_optimizer.py — Parameter Walk-Forward Optimierung (Port von dnabot, verbessert)

dnabot filtert bestehende Trades post-hoc (Re-Optimierung der Genome-DB waere
teuer). mbots Backtester ist billig direkt aufrufbar -- daher wird hier ECHT
neu gebacktestet: die Historie jedes aktiven Pairs wird in rollierende Fenster
gesplittet, jedes Fenster wird mit dem kompletten bis dahin verfuegbaren
Verlauf (kein Lookahead) neu gebacktestet, nur Trades deren entry_time im
jeweiligen Testfenster liegt zaehlen als Out-of-Sample. Alle Pairs werden pro
Kandidatenwert gepoolt (portfolioweite Bewertung, wie bei dnabots Ansatz).

Modi:
  --param rr               : atr_sl_mult bleibt fix, atr_tp_mult = wert * atr_sl_mult
  --param entry_threshold  : min_entropy_drop_pct wird direkt gesweept (0.01..0.35)

'callback' aus dnabot entfaellt -- mbot hat fixe ATR-SL/TP-Exits, keinen
Trailing-Callback-Mechanismus zum Sweepen.

Ausfuehrung:
  python3 -m mbot.analysis.param_optimizer --param rr
  python3 -m mbot.analysis.param_optimizer --param entry_threshold --pairs 1 --no-telegram
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from mbot.analysis.utils import *
from mbot.utils.exchange import Exchange
from mbot.analysis.backtester import load_data, run_backtest, FINE_TF_MAP, LazyFineData

N_WINDOWS = 5  # 1 Warmup-Fenster (Historie) + 4 OOS-Testfenster

PARAM_MODES = {
    'rr': {
        'label':  'R:R-Ratio (atr_tp_mult / atr_sl_mult)',
        'values': [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        'apply':  lambda sig_cfg, v: {**sig_cfg, 'atr_tp_mult': round(sig_cfg.get('atr_sl_mult', 1.5) * v, 4)},
    },
    'entry_threshold': {
        'label':  'min_entropy_drop_pct (Entry-Strenge)',
        'values': [round(0.01 + i * 0.02, 2) for i in range(18)],
        'apply':  lambda sig_cfg, v: {**sig_cfg, 'min_entropy_drop_pct': v},
    },
}


def _parse_dt(ts):
    dt = datetime.fromisoformat(str(ts))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _make_exchange():
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    return Exchange(secrets['mbot'][0])


def _fold_bounds(df, n_windows):
    n = len(df)
    n_windows = max(2, min(n_windows, n // 50)) if n >= 100 else 2
    fold_size = n // n_windows
    idxs = [i * fold_size for i in range(n_windows)]
    bounds = [df.index[i] for i in idxs] + [df.index[-1]]
    return bounds


def _walkforward_oos_trades(df, trial_cfg, risk_config, symbol, bounds, fine_data):
    """Rollierender Walk-Forward: jedes Testfenster wird mit vollem bisherigem
    Verlauf neu gebacktestet, nur Trades im Fenster selbst zaehlen als OOS."""
    oos_trades = []
    for i in range(1, len(bounds) - 1):
        test_start, test_end = bounds[i], bounds[i + 1]
        df_slice = df.loc[:test_end]
        if len(df_slice) < 120:
            continue
        result = run_backtest(df_slice, trial_cfg, risk_config, 100.0, symbol, fine_data=fine_data)
        for t in result.get('trades', []):
            edt = _parse_dt(t['entry_time'])
            if test_start <= edt < test_end:
                t['entry_dt'] = edt  # noetig fuer simulate()'s chronologische Sortierung
                oos_trades.append(t)
    return oos_trades


def create_chart(results, param, capital):
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax)

    vals    = list(results.keys())
    calmars = [results[v]['calmar'] for v in vals]
    pnls    = [results[v]['pnl_pct'] for v in vals]
    bars = ax.bar([str(v) for v in vals], calmars, color=COLORS[0], alpha=0.8)
    for bar, c, p in zip(bars, calmars, pnls):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f'{c:.1f}\n({p:+.0f}%)',
                ha='center', va='bottom', color='white', fontsize=9)
    if calmars:
        best_idx = calmars.index(max(calmars))
        bars[best_idx].set_edgecolor('#fbbf24')
        bars[best_idx].set_linewidth(3)
    ax.set_xlabel(PARAM_MODES[param]['label'])
    ax.set_ylabel('Calmar (OOS, gepoolt ueber alle Pairs)')
    ax.set_title(f'mbot {PARAM_MODES[param]["label"]} Walk-Forward Optimierung\n'
                 f'Startkapital: {capital:.0f} USDT', color='white')
    plt.tight_layout()
    return fig


def main():
    parser = argparse.ArgumentParser(description='mbot Parameter Walk-Forward Optimizer')
    parser.add_argument('--param',       type=str, choices=list(PARAM_MODES.keys()), required=True)
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--pairs',       type=int,   default=None,
                        help='Nur die ersten N aktiven Pairs testen (Schnelltest)')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    mode = PARAM_MODES[args.param]
    label, values = mode['label'], mode['values']

    print(f"\n{'=' * 62}\n  mbot — {label} Walk-Forward Optimierung\n{'=' * 62}")
    print(f"  Testwerte: {values}")
    print(f"  Kapital: {args.capital} USDT | Fenster: {N_WINDOWS} (1 Warmup + {N_WINDOWS-1} OOS)\n")

    active = sorted(load_active_pairs())
    if args.pairs:
        active = active[:args.pairs]
    if not active:
        print(f"  {R}Keine aktiven Strategien in settings.json.{NC}"); sys.exit(1)

    exchange = _make_exchange()
    pooled = {v: [] for v in values}

    for symbol, tf in active:
        try:
            cfg = load_config(symbol, tf)
        except Exception as e:
            print(f"  {Y}Config fehlt fuer {symbol} {tf}: {e}{NC}"); continue

        meta, sig_cfg = cfg.get('_meta', {}), cfg.get('signal', {})
        start_date = meta.get('start_date', '2024-01-01')
        end_date   = meta.get('end_date', '2099-01-01')
        risk_config = {'risk_per_trade_pct': sig_cfg.get('risk_per_trade_pct', 1.0)}

        print(f"  {C}{symbol} ({tf}){NC} | {start_date} -> {end_date}")
        df = load_data(exchange, symbol, tf, start_date, end_date)
        if df is None or df.empty:
            print(f"  {Y}Keine Daten, skip.{NC}"); continue

        bounds  = _fold_bounds(df, N_WINDOWS)
        fine_tf = FINE_TF_MAP.get(tf)
        fine_data = LazyFineData(symbol, fine_tf) if fine_tf else None

        for v in values:
            trial_cfg  = mode['apply'](sig_cfg, v)
            oos_trades = _walkforward_oos_trades(df, trial_cfg, risk_config, symbol, bounds, fine_data)
            pooled[v].extend(oos_trades)
        print(f"    {len(bounds)-2} OOS-Fenster verarbeitet fuer {len(values)} Kandidatenwerte.")

    results = {}
    for v in values:
        r = simulate(pooled[v], args.capital)
        results[v] = r
        col = G if r['pnl_pct'] > 0 else R
        print(f"  {label} = {v:<6} {col}PnL={r['pnl_pct']:+7.1f}%{NC} | "
              f"MaxDD={r['max_dd']:5.1f}% | Calmar={r['calmar']:7.1f} | Trades={r['n']}")

    if not any(r['n'] > 0 for r in results.values()):
        print(f"\n  {R}Keine OOS-Trades erzeugt.{NC}"); sys.exit(1)

    best_val = max(results, key=lambda v: results[v]['calmar'])
    bc, bp, bd = results[best_val]['calmar'], results[best_val]['pnl_pct'], results[best_val]['max_dd']
    print(f"\n  {'─' * 50}")
    print(f"  {G}★ Optimaler Wert: {label} = {best_val}{NC}")
    print(f"  Calmar: {bc:.1f} | PnL: {bp:+.1f}% | MaxDD: {bd:.1f}%")
    print(f"  {'─' * 50}")

    fig = create_chart(results, args.param, args.capital)
    if fig is not None:
        lines = [f"mbot {label} Walk-Forward Optimierung", f"{len(active)} Pair(s) gepoolt\n"]
        for v in sorted(results, key=lambda x: results[x]['calmar'], reverse=True):
            r = results[v]
            m = "★ " if v == best_val else "  "
            lines.append(f"{m}{v}: Calmar {r['calmar']:.1f} | {r['pnl_pct']:+.0f}% | "
                          f"DD {r['max_dd']:.1f}% | {r['n']} Trades")
        save_send(fig, f'param_{args.param}', "\n".join(lines), args.no_telegram)

    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
