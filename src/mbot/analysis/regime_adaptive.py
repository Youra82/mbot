#!/usr/bin/env python3
"""
analysis/regime_adaptive.py — Regime-adaptive R:R (Port von dnabot, verbessert)

dnabot testet verschiedene RR-Ratios pro Timeframe-Gruppe indem es bestehende
Trades neu gewichtet. mbots realisiertes pnl_pct haengt aber schon am
TATSAECHLICH verwendeten atr_tp_mult/atr_sl_mult -- eine nachtraegliche
Neugewichtung waere nicht korrekt. Daher wird hier ECHT neu gebacktestet
(Walk-Forward wie in param_optimizer.py): fuer jede Timeframe-Gruppe wird das
beste R:R (atr_tp_mult/atr_sl_mult, atr_sl_mult fix aus der Config) separat
Out-of-Sample ermittelt und mit einem global uniformen R:R verglichen.

Timeframe-Gruppen: {15m,30m,1h}=schnell | {2h,4h}=mittel | {6h,1d}=langsam
(dnabots urspruengliche Fast/Slow-Heatmap ueber 2 Dimensionen wird wegen der
Backtest-Kosten auf einen direkten Gruppen-Vergleich vereinfacht, keine volle
NxNxN-Kombinatorik ueber 3 Gruppen.)
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

N_WINDOWS = 5
RR_TEST_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
TF_GROUPS = {
    'schnell': ['15m', '30m', '1h'],
    'mittel':  ['2h', '4h'],
    'langsam': ['6h', '1d'],
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
    return [df.index[i] for i in idxs] + [df.index[-1]]


def _walkforward_oos_trades(df, trial_cfg, risk_config, symbol, bounds, fine_data):
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


def _tf_group(tf):
    for g, tfs in TF_GROUPS.items():
        if tf in tfs:
            return g
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capital',     type=float, default=100.0)
    parser.add_argument('--pairs',       type=int,   default=None,
                        help='Nur die ersten N aktiven Pairs testen (Schnelltest)')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  mbot — Regime-adaptive R:R (echte Walk-Forward Re-Backtests)\n{'='*60}")

    active = sorted(load_active_pairs())
    if args.pairs:
        active = active[:args.pairs]
    if not active:
        print(f"  {R}Keine aktiven Strategien.{NC}"); sys.exit(1)

    exchange = _make_exchange()
    # pooled[group][rr] = Liste von OOS-Trades
    pooled = {g: {rr: [] for rr in RR_TEST_VALUES} for g in TF_GROUPS}
    groups_seen = set()

    for symbol, tf in active:
        group = _tf_group(tf)
        if group is None:
            print(f"  {Y}{symbol} {tf}: keine TF-Gruppe zugeordnet, skip.{NC}"); continue
        groups_seen.add(group)

        try:
            cfg = load_config(symbol, tf)
        except Exception as e:
            print(f"  {Y}Config fehlt fuer {symbol} {tf}: {e}{NC}"); continue

        meta, sig_cfg = cfg.get('_meta', {}), cfg.get('signal', {})
        start_date = meta.get('start_date', '2024-01-01')
        end_date   = meta.get('end_date', '2099-01-01')
        risk_config = {'risk_per_trade_pct': sig_cfg.get('risk_per_trade_pct', 1.0)}
        atr_sl_mult = sig_cfg.get('atr_sl_mult', 1.5)

        print(f"  {C}{symbol} ({tf}, Gruppe={group}){NC} | {start_date} -> {end_date}")
        df = load_data(exchange, symbol, tf, start_date, end_date)
        if df is None or df.empty:
            print(f"  {Y}Keine Daten, skip.{NC}"); continue

        bounds  = _fold_bounds(df, N_WINDOWS)
        fine_tf = FINE_TF_MAP.get(tf)
        fine_data = LazyFineData(symbol, fine_tf) if fine_tf else None

        for rr in RR_TEST_VALUES:
            trial_cfg = {**sig_cfg, 'atr_tp_mult': round(atr_sl_mult * rr, 4)}
            oos = _walkforward_oos_trades(df, trial_cfg, risk_config, symbol, bounds, fine_data)
            pooled[group][rr].extend(oos)

    if not groups_seen:
        print(f"\n  {R}Keine Trades erzeugt.{NC}"); sys.exit(1)

    # ── Bestes RR pro Gruppe ──────────────────────────────────────────────────
    group_results = {}
    best_rr_per_group = {}
    print()
    for g in sorted(groups_seen):
        rr_results = {rr: simulate(pooled[g][rr], args.capital) for rr in RR_TEST_VALUES}
        group_results[g] = rr_results
        best_rr = max(rr_results, key=lambda rr: rr_results[rr]['calmar'])
        best_rr_per_group[g] = best_rr
        print(f"  Gruppe {g}: bestes RR={best_rr} "
              f"(Calmar={rr_results[best_rr]['calmar']:.1f}, PnL={rr_results[best_rr]['pnl_pct']:+.1f}%, "
              f"n={rr_results[best_rr]['n']})")

    # ── Adaptive Kombination (bestes RR je Gruppe) ───────────────────────────
    adaptive_trades = []
    for g in groups_seen:
        adaptive_trades.extend(pooled[g][best_rr_per_group[g]])
    adaptive_r = simulate(adaptive_trades, args.capital)

    # ── Uniform: bestes global einheitliches RR ──────────────────────────────
    uniform_results = {}
    for rr in RR_TEST_VALUES:
        uniform_trades = []
        for g in groups_seen:
            uniform_trades.extend(pooled[g][rr])
        uniform_results[rr] = simulate(uniform_trades, args.capital)
    best_uniform_rr = max(uniform_results, key=lambda rr: uniform_results[rr]['calmar'])
    best_uniform = uniform_results[best_uniform_rr]

    print(f"\n  {'─'*60}")
    print(f"  Adaptive (bestes RR je Gruppe): Calmar={adaptive_r['calmar']:.1f} | "
          f"PnL={adaptive_r['pnl_pct']:+.1f}% | DD={adaptive_r['max_dd']:.1f}%")
    print(f"  Uniform (bestes globales RR={best_uniform_rr}): Calmar={best_uniform['calmar']:.1f} | "
          f"PnL={best_uniform['pnl_pct']:+.1f}% | DD={best_uniform['max_dd']:.1f}%")
    print(f"  {'─'*60}")

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(*axes)

    for g in sorted(groups_seen):
        rr_lbls = [str(rr) for rr in RR_TEST_VALUES]
        calmars = [group_results[g][rr]['calmar'] for rr in RR_TEST_VALUES]
        axes[0].plot(rr_lbls, calmars, marker='o', linewidth=2, label=f'{g} (bestes={best_rr_per_group[g]})')
    axes[0].axhline(best_uniform['calmar'], color='#f59e0b', linestyle='--',
                     label=f'Bestes Uniform RR={best_uniform_rr}')
    axes[0].set_xlabel('R:R (atr_tp_mult / atr_sl_mult)')
    axes[0].set_ylabel('Calmar (OOS)')
    axes[0].set_title('Calmar pro R:R nach TF-Gruppe')
    axes[0].legend(facecolor='#1e293b', labelcolor='white', fontsize=8)

    cats = ['Uniform\nbestes RR', 'Adaptive\n(pro Gruppe)']
    vals = [best_uniform['calmar'], adaptive_r['calmar']]
    axes[1].bar(cats, vals, color=['#2563eb', '#16a34a'], alpha=0.8, width=0.4)
    for i, v in enumerate(vals):
        axes[1].text(i, v + max(abs(x) for x in vals) * 0.02, f'{v:.1f}',
                     ha='center', va='bottom', color='white', fontsize=12, fontweight='bold')
    axes[1].set_ylabel('Calmar')
    axes[1].set_title('Uniform vs. Adaptive R:R (OOS)')

    fig.suptitle(f'mbot Regime-adaptive R:R | {len(active)} Pair(s) getestet', color='white', fontsize=11)
    plt.tight_layout()

    caption = (f"mbot Regime-adaptive R:R\n\n"
               f"Bestes RR je Gruppe:\n"
               + "\n".join(f"  {g}: RR={best_rr_per_group[g]} (Calmar {group_results[g][best_rr_per_group[g]]['calmar']:.1f})"
                            for g in sorted(groups_seen))
               + f"\n\nAdaptive: Calmar {adaptive_r['calmar']:.1f} | PnL {adaptive_r['pnl_pct']:+.1f}%\n"
               f"Uniform (RR={best_uniform_rr}): Calmar {best_uniform['calmar']:.1f} | PnL {best_uniform['pnl_pct']:+.1f}%\n"
               f"Verbesserung: {adaptive_r['calmar'] - best_uniform['calmar']:+.1f}")
    save_send(fig, 'regime_adaptive', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
