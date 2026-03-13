# src/mbot/analysis/interactive_chart.py
"""
mbot Interaktive Charts (Modus 4)

Generiert Plotly-HTML mit:
  - Candlestick-Chart
  - Entry/Exit Trade-Marker
  - Equity-Curve Subplot
  - Bollinger Bands
"""

import os
import sys
import json
import webbrowser
from datetime import datetime, timezone
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

CONFIGS_DIR  = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs')
CHARTS_DIR   = os.path.join(PROJECT_ROOT, 'artifacts', 'charts')

GREEN  = '\033[0;32m'
BLUE   = '\033[0;34m'
YELLOW = '\033[1;33m'
RED    = '\033[0;31m'
CYAN   = '\033[0;36m'
BOLD   = '\033[1m'
NC     = '\033[0m'

# Farb-Palette fuer ueberlagerte Equity-Kurven
_EQUITY_COLORS = [
    '#ffa726', '#e91e63', '#ab47bc', '#26c6da',
    '#66bb6a', '#ff7043', '#42a5f5', '#d4e157',
]


def _load_all_configs() -> list:
    if not os.path.exists(CONFIGS_DIR):
        return []
    files = sorted(f for f in os.listdir(CONFIGS_DIR)
                   if f.startswith('config_') and f.endswith('_mers.json'))
    configs = []
    for fn in files:
        path = os.path.join(CONFIGS_DIR, fn)
        try:
            with open(path) as f:
                cfg = json.load(f)
            cfg['_filename'] = fn
            configs.append(cfg)
        except Exception:
            pass
    return configs


def _compute_mers_panels(df, signal_config: dict):
    """Berechnet MERS-Indikatoren (Entropy, Energy, ATR, Regime) fuer Chart-Panels."""
    from mbot.strategy.mers_signal import (
        calc_log_returns, calc_rolling_entropy,
        calc_velocity, calc_acceleration, calc_energy, calc_atr,
    )
    price    = df['close']
    returns  = calc_log_returns(price)
    entropy  = calc_rolling_entropy(returns, window=signal_config.get('entropy_window', 18))
    velocity = calc_velocity(price)
    acc      = calc_acceleration(velocity)
    energy   = calc_energy(velocity)
    atr_ser  = calc_atr(df, period=signal_config.get('atr_period', 21))

    regime_window = signal_config.get('regime_window', 14)
    regimes = []
    for i in range(len(df)):
        if i < regime_window:
            regimes.append('neutral')
            continue
        v_win = velocity.iloc[max(0, i - regime_window):i]
        a_win = acc.iloc[max(0, i - regime_window):i]
        std_v = v_win.std()
        if std_v == 0:
            regimes.append('neutral')
            continue
        vel_consistency = abs(v_win.mean()) / std_v
        chaos_ratio     = a_win.std() / std_v
        if chaos_ratio > 1.5:
            regimes.append('chaos')
        elif vel_consistency > 0.3:
            regimes.append('trend')
        else:
            regimes.append('range')

    return entropy, energy, atr_ser, regimes


def _generate_chart(exchange, symbol: str, timeframe: str,
                    start_date: str, end_date: str,
                    start_capital: float, signal_config: dict,
                    risk_config: dict) -> str:
    """Generiert HTML-Chart fuer ein Symbol. Gibt Pfad zur HTML-Datei zurueck."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print(f'{RED}Fehler: plotly nicht installiert. Bitte: pip install plotly{NC}')
        return ''

    from mbot.analysis.backtester import load_data, run_backtest

    df = load_data(exchange, symbol, timeframe, start_date, end_date)
    if df is None or df.empty:
        print(f'INFO: {RED}Keine Daten verfuegbar fuer {symbol} ({timeframe}).{NC}')
        return ''

    print(f'INFO: Fuehre Backtest durch...')
    result = run_backtest(df, signal_config, risk_config,
                          start_capital=start_capital, symbol=symbol)
    trades = result.get('trades', [])

    # Equity-Kurve
    cap_curve_times = [df.index[0].isoformat()]
    cap_curve_vals  = [start_capital]
    for t in trades:
        cap_curve_times.append(t.get('exit_time', ''))
        cap_curve_vals.append(t.get('capital_after', start_capital))

    # Statistik
    pnl_pct  = result.get('total_pnl_pct', 0.0)
    win_rate = result.get('win_rate', 0.0)
    max_dd   = result.get('max_drawdown', 0.0)
    n_trades = result.get('total_trades', 0)

    # MERS-Indikatoren berechnen
    entropy, energy, atr_ser, regimes = _compute_mers_panels(df, signal_config)

    # Figur: 5 Panels
    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        specs=[
            [{'secondary_y': True}],
            [{'secondary_y': False}],
            [{'secondary_y': False}],
            [{'secondary_y': False}],
            [{'secondary_y': False}],
        ],
        vertical_spacing=0.025,
        row_heights=[0.42, 0.12, 0.15, 0.15, 0.16],
        subplot_titles=['', 'Volumen', 'Shannon Entropy', 'Energy', 'ATR  |  Regime'],
    )

    # --- Regime-Hintergrund (Candlestick-Panel) ---
    _regime_fill = {'trend': 'rgba(38,166,154,0.35)',
                    'range': 'rgba(255,167,38,0.28)',
                    'chaos': 'rgba(239,83,80,0.35)'}
    prev_reg, blk_start = None, None
    for i, (ts_idx, reg) in enumerate(zip(df.index, regimes)):
        if reg != prev_reg:
            if prev_reg in _regime_fill and blk_start is not None:
                fig.add_vrect(x0=blk_start, x1=ts_idx,
                              fillcolor=_regime_fill[prev_reg],
                              layer='below', line_width=0, row=1, col=1)
            blk_start, prev_reg = ts_idx, reg
    if prev_reg in _regime_fill and blk_start is not None:
        fig.add_vrect(x0=blk_start, x1=df.index[-1],
                      fillcolor=_regime_fill[prev_reg],
                      layer='below', line_width=0, row=1, col=1)

    # --- Panel 1: Candlestick ---
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'], high=df['high'],
        low=df['low'],   close=df['close'],
        name='OHLC',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
        showlegend=True,
    ), row=1, col=1, secondary_y=False)

    # Trade-Marker
    long_entries  = [t for t in trades if t.get('side') == 'long']
    short_entries = [t for t in trades if t.get('side') == 'short']
    tp_exits      = [t for t in trades if t.get('result') == 'win']
    sl_exits      = [t for t in trades if t.get('result') == 'loss']

    if long_entries:
        fig.add_trace(go.Scatter(
            x=[t['entry_time'] for t in long_entries],
            y=[t['entry_price'] for t in long_entries],
            mode='markers',
            marker=dict(symbol='triangle-up', size=16, color='#26a69a',
                        line=dict(color='#ffffff', width=1)),
            name='Entry Long',
            hovertemplate='Entry Long<br>%{x}<br>Preis: %{y:.4f}<extra></extra>',
        ), row=1, col=1, secondary_y=False)

    if short_entries:
        fig.add_trace(go.Scatter(
            x=[t['entry_time'] for t in short_entries],
            y=[t['entry_price'] for t in short_entries],
            mode='markers',
            marker=dict(symbol='triangle-down', size=16, color='#ffa726',
                        line=dict(color='#ffffff', width=1)),
            name='Entry Short',
            hovertemplate='Entry Short<br>%{x}<br>Preis: %{y:.4f}<extra></extra>',
        ), row=1, col=1, secondary_y=False)

    if tp_exits:
        fig.add_trace(go.Scatter(
            x=[t['exit_time'] for t in tp_exits],
            y=[t['exit_price'] for t in tp_exits],
            mode='markers',
            marker=dict(symbol='circle', size=13, color='#00bcd4',
                        line=dict(color='#ffffff', width=1)),
            name='Exit TP ✓',
            hovertemplate='Exit TP<br>%{x}<br>Preis: %{y:.4f}<br>PnL: %{customdata:.2f}%<extra></extra>',
            customdata=[t.get('pnl_pct', 0) for t in tp_exits],
        ), row=1, col=1, secondary_y=False)

    if sl_exits:
        fig.add_trace(go.Scatter(
            x=[t['exit_time'] for t in sl_exits],
            y=[t['exit_price'] for t in sl_exits],
            mode='markers',
            marker=dict(symbol='x', size=14, color='#ef5350',
                        line=dict(color='#ef5350', width=3)),
            name='Exit SL ✗',
            hovertemplate='Exit SL<br>%{x}<br>Preis: %{y:.4f}<br>PnL: %{customdata:.2f}%<extra></extra>',
            customdata=[t.get('pnl_pct', 0) for t in sl_exits],
        ), row=1, col=1, secondary_y=False)

    # Equity-Kurve auf rechter Y-Achse
    fig.add_trace(go.Scatter(
        x=cap_curve_times,
        y=cap_curve_vals,
        mode='lines',
        line=dict(color='#5c9bd6', width=1.5),
        name='Equity',
        hovertemplate='Equity: %{y:.2f} USDT<extra></extra>',
    ), row=1, col=1, secondary_y=True)

    # --- Panel 2: Volumen ---
    if 'volume' in df.columns:
        vol_colors = ['#26a69a' if c >= o else '#ef5350'
                      for c, o in zip(df['close'], df['open'])]
        fig.add_trace(go.Bar(
            x=df.index, y=df['volume'],
            marker_color=vol_colors,
            name='Volumen', showlegend=False, opacity=0.6,
        ), row=2, col=1)

    # --- Panel 3: Shannon Entropy ---
    fig.add_trace(go.Scatter(
        x=df.index, y=entropy,
        mode='lines', line=dict(color='#ab47bc', width=1.5),
        fill='tozeroy', fillcolor='rgba(171,71,188,0.10)',
        name='Entropy', showlegend=False,
        hovertemplate='Entropy: %{y:.4f}<extra></extra>',
    ), row=3, col=1)
    # Mittellinie als Referenz
    fig.add_hline(y=float(entropy.mean()), line_dash='dot',
                  line_color='rgba(171,71,188,0.45)', row=3, col=1)
    # Signal-Punkte auf Entropy markieren
    if trades:
        sig_times = [t['entry_time'] for t in trades]
        sig_ent   = [float(entropy.asof(pd.Timestamp(t))) if hasattr(entropy, 'asof')
                     else float(entropy.mean()) for t in sig_times]
        fig.add_trace(go.Scatter(
            x=sig_times, y=sig_ent, mode='markers',
            marker=dict(symbol='circle-open', size=9, color='#ab47bc',
                        line=dict(width=2)),
            name='Signal', showlegend=False,
            hovertemplate='Signal<br>%{x}<extra></extra>',
        ), row=3, col=1)

    # --- Panel 4: Energy ---
    fig.add_trace(go.Scatter(
        x=df.index, y=energy,
        mode='lines', line=dict(color='#ffa726', width=1.5),
        fill='tozeroy', fillcolor='rgba(255,167,38,0.10)',
        name='Energy', showlegend=False,
        hovertemplate='Energy: %{y:.6f}<extra></extra>',
    ), row=4, col=1)
    if trades:
        sig_en = []
        for t in trades:
            ts_key = pd.Timestamp(t['entry_time'])
            try:
                sig_en.append(float(energy.asof(ts_key)))
            except Exception:
                sig_en.append(float(energy.mean()))
        fig.add_trace(go.Scatter(
            x=[t['entry_time'] for t in trades], y=sig_en, mode='markers',
            marker=dict(symbol='circle-open', size=9, color='#ffa726',
                        line=dict(width=2)),
            showlegend=False,
            hovertemplate='Signal<br>%{x}<extra></extra>',
        ), row=4, col=1)

    # --- Panel 5: ATR + Regime-Balken ---
    fig.add_trace(go.Scatter(
        x=df.index, y=atr_ser,
        mode='lines', line=dict(color='#42a5f5', width=1.3),
        fill='tozeroy', fillcolor='rgba(66,165,245,0.10)',
        name='ATR', showlegend=False,
        hovertemplate='ATR: %{y:.2f}<extra></extra>',
    ), row=5, col=1)
    # Regime als farbige Balken (normiert auf ATR-Skala)
    atr_max = float(atr_ser.max()) if not atr_ser.empty else 1.0
    _reg_col = {'trend': '#26a69a', 'range': '#ffa726',
                'chaos': '#ef5350', 'neutral': 'rgba(0,0,0,0)'}
    reg_heights = [atr_max * 0.18 for _ in regimes]
    fig.add_trace(go.Bar(
        x=df.index, y=reg_heights,
        marker_color=[_reg_col.get(r, 'rgba(0,0,0,0)') for r in regimes],
        opacity=0.55, name='Regime', showlegend=False,
        hovertext=regimes,
        hovertemplate='%{hovertext}<extra></extra>',
    ), row=5, col=1)

    # Dummy-Traces fuer Regime-Legende
    for label, color in [('Trend', '#26a69a'), ('Range', '#ffa726'), ('Chaos', '#ef5350')]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode='markers',
            marker=dict(symbol='square', size=10, color=color),
            name=label, showlegend=True,
        ), row=5, col=1)

    title_text = (
        f'{symbol} {timeframe} — MDEF-MERS | '
        f'Trades: {n_trades} | WR: {win_rate:.1f}% | '
        f'PnL: {pnl_pct:+.1f}% | MaxDD: {max_dd:.1f}%'
    )

    fig.update_layout(
        title=dict(text=title_text, font=dict(size=13), x=0.5, xanchor='center'),
        template='plotly_dark',
        xaxis_rangeslider_visible=False,
        legend=dict(orientation='h', yanchor='bottom', y=1.01,
                    xanchor='center', x=0.5, font=dict(size=11)),
        height=1050,
        margin=dict(l=60, r=70, t=80, b=40),
        barmode='overlay',
        yaxis2=dict(title='Equity (USDT)', showgrid=False,
                    tickfont=dict(color='#5c9bd6'), title_font=dict(color='#5c9bd6')),
    )
    fig.update_yaxes(title_text='Preis', row=1, col=1)
    fig.update_yaxes(title_text='Vol',   row=2, col=1)
    fig.update_yaxes(title_text='H',     row=3, col=1, tickformat='.3f')
    fig.update_yaxes(title_text='E',     row=4, col=1, type='log')
    fig.update_yaxes(title_text='ATR',   row=5, col=1)

    # Speichern
    os.makedirs(CHARTS_DIR, exist_ok=True)
    safe_name  = symbol.replace('/', '').replace(':', '')
    ts         = datetime.now().strftime('%Y%m%d_%H%M%S')
    chart_path = os.path.join(CHARTS_DIR, f'chart_{safe_name}_{timeframe}_{ts}.html')
    fig.write_html(chart_path)
    return chart_path


def generate_portfolio_chart(selected_results: dict, portfolio: dict,
                             start_capital: float,
                             start_date: str, end_date: str) -> str:
    """
    Generiert einen kombinierten Portfolio-Chart:
    - Ueberlagerte Einzel-Equity-Kurven (linke Y-Achse)
    - Portfolio-Equity (rechte Y-Achse, blau)
    - Entry/TP/SL Marker auf Portfolio-Equity-Linie
    - Unteres Panel: Trade-Timeline als Marker-Leiste
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print(f'{RED}Fehler: plotly nicht installiert.{NC}')
        return ''

    port_trades  = portfolio.get('trades', [])
    pnl_pct      = portfolio.get('total_pnl_pct', 0.0)
    win_rate     = portfolio.get('win_rate', 0.0)
    max_dd       = portfolio.get('max_drawdown', 0.0)
    n_trades     = portfolio.get('total_trades', 0)
    end_cap      = portfolio.get('end_capital', start_capital)

    # Portfolio-Equity-Kurve aufbauen
    port_times  = [start_date + 'T00:00:00'] + [t.get('exit_time', '') for t in port_trades]
    port_equity = [start_capital] + [t.get('portfolio_capital_after', start_capital) for t in port_trades]

    # Kapital-vor-Trade fuer Entry-Marker (linker Vorgaenger)
    entry_equity = [start_capital] + [t.get('portfolio_capital_after', start_capital) for t in port_trades[:-1]]

    # Titel
    labels = []
    for r in selected_results.values():
        sym = r.get('symbol', '?').replace('/USDT:USDT', '').replace('/', '')
        tf  = r.get('timeframe', '?')
        labels.append(f'{sym}/{tf}')
    title_text = (
        f'mbot Portfolio — {len(selected_results)} Strategien ({", ".join(labels)}) | '
        f'Trades: {n_trades} | WR: {win_rate:.1f}% | '
        f'PnL: {pnl_pct:+.1f}% | Final Equity: {end_cap:.2f} USDT | MaxDD: {max_dd:.1f}%'
    )

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        specs=[[{'secondary_y': True}], [{'secondary_y': False}]],
        vertical_spacing=0.03,
        row_heights=[0.85, 0.15],
    )

    # Einzel-Equity-Kurven (linke Y-Achse)
    for i, (fn, r) in enumerate(selected_results.items()):
        sym    = r.get('symbol', '?').replace('/USDT:USDT', '')
        tf     = r.get('timeframe', '?')
        trades = r.get('trades', [])
        color  = _EQUITY_COLORS[i % len(_EQUITY_COLORS)]
        eq_t   = [start_date + 'T00:00:00'] + [t.get('exit_time', '') for t in trades]
        eq_v   = [start_capital] + [t.get('capital_after', start_capital) for t in trades]
        fig.add_trace(go.Scatter(
            x=eq_t, y=eq_v,
            mode='lines',
            line=dict(color=color, width=1.2),
            name=f'{sym} {tf}',
            hovertemplate=f'{sym} {tf}<br>Equity: %{{y:.2f}} USDT<extra></extra>',
        ), row=1, col=1, secondary_y=False)

    # Portfolio-Equity (rechte Y-Achse)
    fig.add_trace(go.Scatter(
        x=port_times, y=port_equity,
        mode='lines',
        line=dict(color='#5c9bd6', width=2),
        name='Portfolio Equity',
        hovertemplate='Portfolio: %{y:.2f} USDT<extra></extra>',
    ), row=1, col=1, secondary_y=True)

    # Entry-Marker
    if port_trades:
        fig.add_trace(go.Scatter(
            x=[t.get('entry_time', '') for t in port_trades],
            y=entry_equity,
            mode='markers',
            marker=dict(symbol='triangle-up', size=12, color='#26a69a',
                        line=dict(color='#ffffff', width=0.5)),
            name='Entry ▲',
            hovertemplate='Entry<br>%{x}<extra></extra>',
        ), row=1, col=1, secondary_y=True)

    # Exit TP
    tp_exits = [t for t in port_trades if t.get('result') == 'win']
    if tp_exits:
        fig.add_trace(go.Scatter(
            x=[t.get('exit_time', '') for t in tp_exits],
            y=[t.get('portfolio_capital_after', start_capital) for t in tp_exits],
            mode='markers',
            marker=dict(symbol='circle', size=11, color='#00bcd4',
                        line=dict(color='#ffffff', width=0.5)),
            name='Exit TP ✓',
            hovertemplate='Exit TP<br>%{x}<br>PnL: %{customdata:.1f}%<extra></extra>',
            customdata=[t.get('pnl_pct', 0) for t in tp_exits],
        ), row=1, col=1, secondary_y=True)

    # Exit SL
    sl_exits = [t for t in port_trades if t.get('result') == 'loss']
    if sl_exits:
        fig.add_trace(go.Scatter(
            x=[t.get('exit_time', '') for t in sl_exits],
            y=[t.get('portfolio_capital_after', start_capital) for t in sl_exits],
            mode='markers',
            marker=dict(symbol='x', size=13, color='#ef5350',
                        line=dict(color='#ef5350', width=2.5)),
            name='Exit SL ✗',
            hovertemplate='Exit SL<br>%{x}<br>PnL: %{customdata:.1f}%<extra></extra>',
            customdata=[t.get('pnl_pct', 0) for t in sl_exits],
        ), row=1, col=1, secondary_y=True)

    # Unteres Panel: Trade-Timeline (alle Trades als Marker bei y=1)
    for t in port_trades:
        pass  # wird unten als Batch hinzugefuegt

    if port_trades:
        tp_t = [t.get('exit_time', '') for t in tp_exits]
        sl_t = [t.get('exit_time', '') for t in sl_exits]
        en_t = [t.get('entry_time', '') for t in port_trades]
        if en_t:
            fig.add_trace(go.Scatter(
                x=en_t, y=[1] * len(en_t), mode='markers',
                marker=dict(symbol='triangle-up', size=9, color='#26a69a'),
                showlegend=False,
                hovertemplate='Entry<br>%{x}<extra></extra>',
            ), row=2, col=1)
        if tp_t:
            fig.add_trace(go.Scatter(
                x=tp_t, y=[1] * len(tp_t), mode='markers',
                marker=dict(symbol='circle', size=8, color='#00bcd4'),
                showlegend=False,
                hovertemplate='Exit TP<br>%{x}<extra></extra>',
            ), row=2, col=1)
        if sl_t:
            fig.add_trace(go.Scatter(
                x=sl_t, y=[1] * len(sl_t), mode='markers',
                marker=dict(symbol='x', size=9, color='#ef5350',
                            line=dict(width=2)),
                showlegend=False,
                hovertemplate='Exit SL<br>%{x}<extra></extra>',
            ), row=2, col=1)

    # Start-Annotation
    fig.add_annotation(
        x=start_date + 'T00:00:00', y=start_capital,
        text=f'Start {start_capital:.0f} USDT',
        showarrow=False, font=dict(size=10, color='#aaa'),
        xref='x', yref='y', xanchor='left', yanchor='bottom',
    )

    fig.update_layout(
        title=dict(text=title_text, font=dict(size=11), x=0.5, xanchor='center'),
        template='plotly_dark',
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=False,
        legend=dict(orientation='h', yanchor='bottom', y=1.01,
                    xanchor='center', x=0.5, font=dict(size=10)),
        height=720,
        margin=dict(l=70, r=80, t=80, b=40),
        yaxis=dict(title='Einzel-Equity (USDT)'),
        yaxis2=dict(title='Portfolio-Equity (USDT)', showgrid=False,
                    tickfont=dict(color='#5c9bd6'),
                    title_font=dict(color='#5c9bd6')),
        yaxis3=dict(visible=False),  # unteres Panel ohne Y-Achse
    )
    fig.update_yaxes(visible=False, row=2, col=1)

    os.makedirs(CHARTS_DIR, exist_ok=True)
    ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(CHARTS_DIR, f'mbot_portfolio_{ts}.html')
    fig.write_html(path)
    return path


def run_interactive_chart():
    """Interaktiver Chart-Generator (Modus 4)."""
    print('\n========== INTERAKTIVE CHARTS ===========\n')

    configs = _load_all_configs()
    if not configs:
        print(f'{RED}Keine Config-Dateien gefunden in {CONFIGS_DIR}{NC}')
        print(f'{YELLOW}Bitte zuerst run_pipeline.sh ausfuehren.{NC}')
        return

    # Verfuegbare Configs auflisten
    print(f'{BOLD}{"="*70}{NC}')
    print('Verfuegbare Konfigurationen:')
    print(f'{BOLD}{"="*70}{NC}')
    for idx, cfg in enumerate(configs, 1):
        pnl     = cfg.get('_meta', {}).get('pnl_pct')
        pnl_str = f'  [+{pnl:.1f}%]' if pnl and pnl > 0 else (f'  [{pnl:.1f}%]' if pnl is not None else '')
        clean   = cfg['_filename'].replace('config_', '').replace('_mers.json', '')
        print(f'{idx:>3}) {clean}{CYAN}{pnl_str}{NC}')
    print(f'{BOLD}{"="*70}{NC}')

    print('\nWaehle Konfiguration(en) zum Anzeigen:')
    print("  Einzeln: z.B. '1' oder '5'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")
    raw = input('\nAuswahl: ').strip().lower()
    if raw in ('alle', 'all'):
        selected = configs
    else:
        indices = []
        for part in raw.replace(',', ' ').split():
            try:
                indices.append(int(part) - 1)
            except ValueError:
                pass
        selected = [configs[i] for i in indices if 0 <= i < len(configs)]

    if not selected:
        print(f'{RED}Keine gueltigen Strategien ausgewaehlt.{NC}')
        return

    # Chart-Optionen
    print(f'\n{"="*60}')
    print('Chart-Optionen:')
    print(f'{"="*60}')

    raw = input('Startdatum (JJJJ-MM-TT) [leer=beliebig]: ').strip()
    start_date = raw if raw else None

    raw = input('Enddatum (JJJJ-MM-TT) [leer=heute]: ').strip()
    end_date = raw if raw else datetime.now(timezone.utc).strftime('%Y-%m-%d')

    raw = input('Letzten N Tage anzeigen [leer=alle]: ').strip()
    if raw:
        try:
            n_days = int(raw)
            from datetime import timedelta
            end_dt   = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(days=n_days)
            start_date = start_dt.strftime('%Y-%m-%d')
            end_date   = end_dt.strftime('%Y-%m-%d')
        except ValueError:
            pass
    if not start_date:
        start_date = '2020-01-01'

    raw = input('Startkapital in USDT [Standard: 1000]: ').strip()
    try:
        start_capital = float(raw) if raw else 1000.0
    except ValueError:
        start_capital = 1000.0

    # Exchange laden
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    with open(os.path.join(PROJECT_ROOT, 'settings.json')) as f:
        settings = json.load(f)

    accounts = secrets.get('mbot', [])
    if not accounts:
        print(f'{RED}Keine mbot-Accounts in secret.json.{NC}')
        return

    telegram  = secrets.get('telegram', {})
    bot_token = telegram.get('bot_token', '')
    chat_id   = telegram.get('chat_id', '')
    send_tg   = False
    if bot_token and chat_id:
        raw = input('Telegram versenden? (j/n) [Standard: n]: ').strip().lower()
        send_tg = raw in ('j', 'y', 'ja', 'yes')

    from mbot.utils.exchange import Exchange
    exchange    = Exchange(accounts[0])
    risk_config = settings.get('risk', {})

    # Charts generieren
    generated = []
    for cfg in selected:
        market        = cfg.get('market', {})
        symbol        = market.get('symbol', '?')
        tf            = market.get('timeframe', '?')
        signal_config = cfg.get('signal', settings.get('signal', {}))

        print(f'INFO: Verarbeite {cfg["_filename"]}...')
        print(f'INFO: Lade OHLCV-Daten fuer {symbol} {tf}...')
        path = _generate_chart(
            exchange, symbol, tf,
            start_date, end_date,
            start_capital, signal_config, risk_config,
        )
        if path:
            generated.append(path)
            print(f'INFO: Erstelle Chart...')
            print(f'INFO: {GREEN}✅ Chart gespeichert: {path}{NC}')
            if send_tg:
                print(f'INFO: Sende Chart via Telegram...')
                _send_charts_via_telegram([path], bot_token, chat_id)

    if not generated:
        print(f'\n{RED}Keine Charts generiert.{NC}')
        return

    print(f'\nINFO:')
    print(f'INFO: {GREEN}✅ Alle Charts generiert!{NC}')
    print(f'{GREEN}✅ Charts wurden generiert!{NC}')


def _send_charts_via_telegram(chart_paths: list, bot_token: str, chat_id: str):
    """Sendet HTML-Charts als Datei-Download per Telegram (sendDocument)."""
    import requests
    for path in chart_paths:
        filename = os.path.basename(path)
        caption  = f'📊 Chart: {filename.replace("chart_", "").replace(".html", "")}'
        try:
            with open(path, 'rb') as f:
                requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendDocument',
                    data={'chat_id': chat_id, 'caption': caption},
                    files={'document': (filename, f, 'text/html')},
                    timeout=60,
                )
        except Exception as e:
            print(f'  Telegram-Fehler: {e}')
