"""
Demo: MDEF-MERS Indikatoren als Subplots unter dem Candlestick-Chart
Zeigt wie Shannon Entropy, Energy, Velocity und Regime-Klassifikation aussehen würden.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import json
import pandas as pd
import numpy as np
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(__file__)


def load_data():
    from mbot.utils.exchange import Exchange
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    accounts = secrets.get('mbot', [])
    exch = Exchange(accounts[0])
    exchange = exch.get_exchange()
    since = exchange.parse8601('2025-01-01T00:00:00Z')
    ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '6h', since=since, limit=500)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    return df


def compute_indicators(df, cfg):
    from mbot.strategy.mers_signal import (
        calc_log_returns, calc_rolling_entropy, calc_velocity,
        calc_acceleration, calc_energy, calc_atr, classify_phase_regime,
    )
    price    = df['close']
    returns  = calc_log_returns(price)
    entropy  = calc_rolling_entropy(returns, window=cfg.get('entropy_window', 18))
    velocity = calc_velocity(price)
    acc      = calc_acceleration(velocity)
    energy   = calc_energy(velocity)
    atr_ser  = calc_atr(df, period=cfg.get('atr_period', 21))

    # Regime pro Kerze
    regime_window = cfg.get('regime_window', 14)
    regimes = []
    for i in range(len(df)):
        if i < regime_window:
            regimes.append('neutral')
            continue
        v_win = velocity.iloc[max(0, i-regime_window):i]
        a_win = acc.iloc[max(0, i-regime_window):i]
        std_v = v_win.std()
        std_a = a_win.std()
        if std_v == 0:
            regimes.append('neutral')
            continue
        vel_consistency = abs(v_win.mean()) / std_v
        chaos_ratio     = std_a / std_v if std_v > 0 else 0
        if chaos_ratio > 1.5:
            regimes.append('chaos')
        elif vel_consistency > 0.3:
            regimes.append('trend')
        else:
            regimes.append('range')

    # Signal-Punkte ermitteln (wo alle Bedingungen erfüllt)
    min_entropy_drop = cfg.get('min_entropy_drop_pct', 0.18)
    min_energy_rise  = cfg.get('min_energy_rise_pct', 1.15)
    el_lb = cfg.get('entropy_lookback', 6)
    en_lb = cfg.get('energy_lookback', 6)

    signals = []
    for i in range(max(el_lb, en_lb) + 5, len(df)):
        if entropy.iloc[i] <= 0 or entropy.iloc[i - el_lb] <= 0:
            continue
        entropy_drop = (entropy.iloc[i - el_lb] - entropy.iloc[i]) / entropy.iloc[i - el_lb]
        if energy.iloc[i - en_lb] <= 0:
            continue
        energy_rise = (energy.iloc[i] - energy.iloc[i - en_lb]) / energy.iloc[i - en_lb]
        if entropy_drop >= min_entropy_drop and energy_rise >= min_energy_rise and regimes[i] == 'trend':
            direction = 'long' if velocity.iloc[i] > 0 else 'short'
            signals.append((df.index[i], direction, df['close'].iloc[i]))

    return entropy, energy, velocity, atr_ser, regimes, signals


def build_chart(df, entropy, energy, velocity, atr_ser, regimes, signals, cfg):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    # Regime-Farben für Candlestick-Hintergrund
    regime_colors = {'trend': 'rgba(38,166,154,0.08)', 'range': 'rgba(255,167,38,0.08)',
                     'chaos': 'rgba(239,83,80,0.08)', 'neutral': 'rgba(0,0,0,0)'}

    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=[0.42, 0.15, 0.15, 0.13, 0.15],
        subplot_titles=[
            'BTC/USDT:USDT 6h — MDEF-MERS (Demo)',
            'Shannon Entropy  H = -Σ pᵢ·log(pᵢ)',
            'Markt-Energy  (Bewegungsintensität)',
            'ATR  (Volatilität → SL/TP Basis)',
            'Regime-Klassifikation',
        ],
        specs=[[{}], [{}], [{}], [{}], [{}]],
    )

    # --- Panel 1: Candlestick ---
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'],
        increasing_line_color='#26a69a', decreasing_line_color='#ef5350',
        increasing_fillcolor='#26a69a', decreasing_fillcolor='#ef5350',
        line_width=1, name='OHLC', showlegend=False,
    ), row=1, col=1)

    # Regime-Hintergrund als vrect (shapige Blöcke)
    prev_regime = None
    block_start = None
    for i, (ts, reg) in enumerate(zip(df.index, regimes)):
        if reg != prev_regime:
            if prev_regime is not None and block_start is not None:
                color = regime_colors.get(prev_regime, 'rgba(0,0,0,0)')
                if color != 'rgba(0,0,0,0)':
                    fig.add_vrect(x0=block_start, x1=ts, fillcolor=color,
                                  layer='below', line_width=0, row=1, col=1)
            block_start = ts
            prev_regime = reg
    # letzten Block schließen
    if prev_regime and block_start:
        color = regime_colors.get(prev_regime, 'rgba(0,0,0,0)')
        if color != 'rgba(0,0,0,0)':
            fig.add_vrect(x0=block_start, x1=df.index[-1], fillcolor=color,
                          layer='below', line_width=0, row=1, col=1)

    # Signal-Marker
    long_signals  = [(t, p) for t, d, p in signals if d == 'long']
    short_signals = [(t, p) for t, d, p in signals if d == 'short']
    if long_signals:
        lt, lp = zip(*long_signals)
        fig.add_trace(go.Scatter(
            x=lt, y=[p * 0.995 for p in lp], mode='markers',
            marker=dict(symbol='triangle-up', size=14, color='#26a69a',
                        line=dict(color='#fff', width=0.8)),
            name='Entry Long', showlegend=True,
        ), row=1, col=1)
    if short_signals:
        st, sp = zip(*short_signals)
        fig.add_trace(go.Scatter(
            x=st, y=[p * 1.005 for p in sp], mode='markers',
            marker=dict(symbol='triangle-down', size=14, color='#ffa726',
                        line=dict(color='#fff', width=0.8)),
            name='Entry Short', showlegend=True,
        ), row=1, col=1)

    # --- Panel 2: Shannon Entropy ---
    fig.add_trace(go.Scatter(
        x=df.index, y=entropy, mode='lines',
        line=dict(color='#ab47bc', width=1.5), name='Entropy', showlegend=False,
        fill='tozeroy', fillcolor='rgba(171,71,188,0.12)',
        hovertemplate='Entropy: %{y:.4f}<extra></extra>',
    ), row=2, col=1)
    # Threshold-Linie (mittlerer Entropy-Wert als Referenz)
    ent_mean = float(entropy.mean())
    fig.add_hline(y=ent_mean, line_dash='dot', line_color='rgba(171,71,188,0.5)',
                  row=2, col=1)
    # Signal-Punkte auf Entropy-Panel markieren
    if signals:
        sig_ts = [t for t, _, _ in signals]
        sig_ent = [float(entropy.get(t, entropy.mean())) if t in entropy.index else ent_mean for t in sig_ts]
        fig.add_trace(go.Scatter(
            x=sig_ts, y=sig_ent, mode='markers',
            marker=dict(symbol='circle-open', size=10, color='#ab47bc',
                        line=dict(width=2)),
            showlegend=False,
            hovertemplate='Signal hier<extra></extra>',
        ), row=2, col=1)

    # --- Panel 3: Energy ---
    fig.add_trace(go.Scatter(
        x=df.index, y=energy, mode='lines',
        line=dict(color='#ffa726', width=1.5), name='Energy', showlegend=False,
        fill='tozeroy', fillcolor='rgba(255,167,38,0.10)',
        hovertemplate='Energy: %{y:.6f}<extra></extra>',
    ), row=3, col=1)
    if signals:
        sig_en = [float(energy.iloc[df.index.get_loc(t)]) if t in df.index else 0 for t, _, _ in signals]
        fig.add_trace(go.Scatter(
            x=[t for t, _, _ in signals], y=sig_en, mode='markers',
            marker=dict(symbol='circle-open', size=10, color='#ffa726',
                        line=dict(width=2)),
            showlegend=False,
        ), row=3, col=1)

    # --- Panel 4: ATR ---
    fig.add_trace(go.Scatter(
        x=df.index, y=atr_ser, mode='lines',
        line=dict(color='#42a5f5', width=1.3), name='ATR', showlegend=False,
        fill='tozeroy', fillcolor='rgba(66,165,245,0.10)',
        hovertemplate='ATR: %{y:.2f}<extra></extra>',
    ), row=4, col=1)

    # --- Panel 5: Regime als farbige Balken ---
    regime_num  = {'trend': 1, 'range': 0.5, 'chaos': 0.2, 'neutral': 0}
    regime_col  = {'trend': '#26a69a', 'range': '#ffa726', 'chaos': '#ef5350', 'neutral': '#444'}
    r_colors    = [regime_col.get(r, '#444') for r in regimes]
    r_vals      = [regime_num.get(r, 0) for r in regimes]
    fig.add_trace(go.Bar(
        x=df.index, y=r_vals,
        marker_color=r_colors, name='Regime',
        showlegend=False,
        hovertext=regimes,
        hovertemplate='%{hovertext}<extra></extra>',
    ), row=5, col=1)

    # Dummy-Traces für Regime-Legende
    for label, color in [('Trend', '#26a69a'), ('Range', '#ffa726'), ('Chaos', '#ef5350')]:
        fig.add_trace(go.Bar(x=[None], y=[None], marker_color=color,
                             name=label, showlegend=True), row=5, col=1)

    # --- Layout ---
    n_signals = len(signals)
    fig.update_layout(
        template='plotly_dark',
        height=920,
        margin=dict(l=70, r=50, t=60, b=40),
        title=dict(
            text=(f'MDEF-MERS Indikatoren — BTC/USDT:USDT 6h  |  '
                  f'{n_signals} Signale gefunden  |  '
                  f'Hintergrund: <span style="color:#26a69a">■ Trend</span>  '
                  f'<span style="color:#ffa726">■ Range</span>  '
                  f'<span style="color:#ef5350">■ Chaos</span>'),
            x=0.5, xanchor='center', font=dict(size=12),
        ),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation='h', yanchor='bottom', y=1.01,
                    xanchor='center', x=0.5, font=dict(size=10)),
        barmode='overlay',
    )

    # Y-Achsen beschriften
    fig.update_yaxes(title_text='Preis (USDT)', row=1, col=1)
    fig.update_yaxes(title_text='Entropy', row=2, col=1, tickformat='.3f')
    fig.update_yaxes(title_text='Energy', row=3, col=1, tickformat='.5f')
    fig.update_yaxes(title_text='ATR', row=4, col=1)
    fig.update_yaxes(title_text='Regime', row=5, col=1, showticklabels=False)

    out_path = os.path.join(PROJECT_ROOT, 'artifacts', 'charts', 'demo_mers_indicators.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.write_html(out_path)
    print(f'\n✓ Demo-Chart gespeichert: {out_path}\n')
    return out_path


if __name__ == '__main__':
    print('Lade BTC/USDT:USDT 6h Daten...')
    df = load_data()
    print(f'  {len(df)} Kerzen geladen.')

    # Config von BTC 6h laden
    cfg_path = os.path.join(PROJECT_ROOT, 'src', 'mbot', 'strategy', 'configs',
                            'config_BTCUSDTUSDT_6h_mers.json')
    with open(cfg_path) as f:
        cfg_data = json.load(f)
    cfg = cfg_data.get('signal', {})

    print('Berechne MERS-Indikatoren...')
    entropy, energy, velocity, atr_ser, regimes, signals = compute_indicators(df, cfg)
    print(f'  {len(signals)} Signale gefunden.')

    print('Erstelle Demo-Chart...')
    build_chart(df, entropy, energy, velocity, atr_ser, regimes, signals, cfg)
