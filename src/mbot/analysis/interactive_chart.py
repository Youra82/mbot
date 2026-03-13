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

    # Bollinger Bands berechnen
    bb_period = signal_config.get('bb_period', 20)
    bb_std    = signal_config.get('bb_std', 2.0)
    df['bb_mid']   = df['close'].rolling(bb_period).mean()
    df['bb_upper'] = df['bb_mid'] + bb_std * df['close'].rolling(bb_period).std()
    df['bb_lower'] = df['bb_mid'] - bb_std * df['close'].rolling(bb_period).std()

    # Equity-Kurve
    cap_curve_times = [df.index[0].isoformat()]
    cap_curve_vals  = [start_capital]
    for t in trades:
        cap_curve_times.append(t.get('exit_time', ''))
        cap_curve_vals.append(t.get('capital_after', start_capital))

    # Figur erstellen
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=(f'{symbol} ({timeframe}) — Candlestick + Trades',
                        'Equity-Kurve (USDT)'),
        row_heights=[0.7, 0.3],
    )

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'], high=df['high'],
        low=df['low'],   close=df['close'],
        name='Preis',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
    ), row=1, col=1)

    # Bollinger Bands
    fig.add_trace(go.Scatter(
        x=df.index, y=df['bb_upper'],
        line=dict(color='rgba(100,149,237,0.6)', width=1, dash='dot'),
        name='BB Upper',
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['bb_lower'],
        line=dict(color='rgba(100,149,237,0.6)', width=1, dash='dot'),
        name='BB Lower',
        fill='tonexty',
        fillcolor='rgba(100,149,237,0.05)',
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['bb_mid'],
        line=dict(color='rgba(100,149,237,0.4)', width=1),
        name='BB Mid',
    ), row=1, col=1)

    # Trade-Marker
    long_entries  = [t for t in trades if t.get('side') == 'long']
    short_entries = [t for t in trades if t.get('side') == 'short']
    wins   = [t for t in trades if t.get('result') == 'win']
    losses = [t for t in trades if t.get('result') == 'loss']

    if long_entries:
        fig.add_trace(go.Scatter(
            x=[t['entry_time'] for t in long_entries],
            y=[t['entry_price'] for t in long_entries],
            mode='markers',
            marker=dict(symbol='triangle-up', size=10, color='#26a69a'),
            name='Long Entry',
            hovertemplate='Long Entry<br>Preis: %{y:.4f}<extra></extra>',
        ), row=1, col=1)

    if short_entries:
        fig.add_trace(go.Scatter(
            x=[t['entry_time'] for t in short_entries],
            y=[t['entry_price'] for t in short_entries],
            mode='markers',
            marker=dict(symbol='triangle-down', size=10, color='#ef5350'),
            name='Short Entry',
            hovertemplate='Short Entry<br>Preis: %{y:.4f}<extra></extra>',
        ), row=1, col=1)

    if wins:
        fig.add_trace(go.Scatter(
            x=[t['exit_time'] for t in wins],
            y=[t['exit_price'] for t in wins],
            mode='markers',
            marker=dict(symbol='star', size=10, color='#ffd700'),
            name='Exit Win',
            hovertemplate='Win Exit<br>Preis: %{y:.4f}<extra></extra>',
        ), row=1, col=1)

    if losses:
        fig.add_trace(go.Scatter(
            x=[t['exit_time'] for t in losses],
            y=[t['exit_price'] for t in losses],
            mode='markers',
            marker=dict(symbol='x', size=10, color='#ff6b6b'),
            name='Exit Loss',
            hovertemplate='Loss Exit<br>Preis: %{y:.4f}<extra></extra>',
        ), row=1, col=1)

    # Equity-Kurve
    fig.add_trace(go.Scatter(
        x=cap_curve_times,
        y=cap_curve_vals,
        mode='lines+markers',
        line=dict(color='#64b5f6', width=2),
        marker=dict(size=4),
        name='Kapital',
        fill='tozeroy',
        fillcolor='rgba(100,181,246,0.1)',
    ), row=2, col=1)

    # Statistik-Annotation
    pnl_pct  = result.get('total_pnl_pct', 0.0)
    win_rate = result.get('win_rate', 0.0)
    max_dd   = result.get('max_drawdown', 0.0)
    n_trades = result.get('total_trades', 0)
    end_cap  = result.get('end_capital', start_capital)

    annotation_text = (
        f'Trades: {n_trades} | WR: {win_rate:.1f}% | '
        f'PnL: {pnl_pct:+.2f}% | MaxDD: {max_dd:.1f}% | '
        f'End: {end_cap:.2f} USDT'
    )

    fig.update_layout(
        title=dict(
            text=f'mbot Chart — {symbol} ({timeframe}) | {start_date} bis {end_date}',
            font=dict(size=16),
        ),
        annotations=[dict(
            text=annotation_text,
            xref='paper', yref='paper',
            x=0.0, y=1.02,
            showarrow=False,
            font=dict(size=12, color='#aaa'),
        )],
        template='plotly_dark',
        xaxis_rangeslider_visible=False,
        legend=dict(orientation='h', yanchor='bottom', y=1.04, xanchor='right', x=1),
        height=900,
        margin=dict(l=60, r=20, t=120, b=40),
    )

    # Speichern
    os.makedirs(CHARTS_DIR, exist_ok=True)
    safe_name  = symbol.replace('/', '').replace(':', '')
    ts         = datetime.now().strftime('%Y%m%d_%H%M%S')
    chart_path = os.path.join(CHARTS_DIR, f'chart_{safe_name}_{timeframe}_{ts}.html')
    fig.write_html(chart_path)
    return chart_path


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
