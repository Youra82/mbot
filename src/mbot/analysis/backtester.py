# src/mbot/analysis/backtester.py
"""
mbot Backtester (MDEF-MERS)

Simuliert das MERS-Signal auf historischen Kerzen und berechnet:
- Anzahl Trades (Long/Short)
- Win-Rate
- Gesamt-PnL (USDT und %)
- Max Drawdown
- Bestes/Schlechtestes Trade-Ergebnis
- Trade-Liste

SL/TP: ATR-basiert (aus MERS signal['sl_price'] / signal['tp_price'])
Exit:   Intra-candle SL/TP-Pruefung via High/Low (Hard Stop)

Optimierung: Features werden einmal auf dem gesamten DataFrame vorberechnet
(O(N) statt O(N^2)) anstatt get_mers_signal() fuer jede Kerze aufzurufen.
"""

import os
import sys
import json
import logging
import time
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from mbot.strategy.mdef_analysis import (
    calc_log_returns,
    calc_rolling_entropy,
    calc_velocity,
    calc_acceleration,
    calc_energy,
    calc_atr,
    classify_phase_regime,
    calc_multitf_alignment,
)

# Feinere Timeframe je Strategie-Timeframe fuer die SL/TP-Intrabar-Reihenfolgen-
# Aufloesung (oraclebot-Muster).
FINE_TF_MAP = {
    '5m': '1m', '15m': '1m', '30m': '1m',
    '1h': '5m', '2h': '5m',
    '4h': '15m', '6h': '15m',
    '1d': '1h',
}


def _resolve_ambiguous_exit(fine_slice, sl_price, tp_price, side):
    """
    Wenn eine Coarse-Kerze SOWOHL SL als auch TP beruehrt haette, per feineren
    Kerzen die tatsaechliche Reihenfolge aufloesen, statt SL blind zu
    bevorzugen (bisherige, als "konservativ" dokumentierte Konvention).
    """
    if fine_slice is None or fine_slice.empty:
        return None, None
    for _, bar in fine_slice.iterrows():
        if side == 'long':
            if bar['low'] <= sl_price:
                return sl_price, 'loss'
            if bar['high'] >= tp_price:
                return tp_price, 'win'
        else:
            if bar['high'] >= sl_price:
                return sl_price, 'loss'
            if bar['low'] <= tp_price:
                return tp_price, 'win'
    return None, None


secrets_cache = None


class LazyFineData:
    """
    On-Demand-Fetcher fuer Fein-Daten (Intrabar-Aufloesung). Laedt Fein-Kerzen
    nur fuer die Tage, an denen im Backtest tatsaechlich eine same-candle
    SL/TP-Ambiguitaet auftritt, statt den kompletten Backtest-Zeitraum vorab
    herunterzuladen -- diese Ambiguitaet ist selten (die weit ueberwiegende
    Mehrheit der Kerzen braucht nie eine Intrabar-Aufloesung).
    Ergebnis ist identisch zum eagerly geladenen DataFrame, nur Zeitpunkt und
    Groesse der Netzwerk-Fetches aendern sich. Pro (symbol, fine_tf)-Instanz
    wiederverwendbar -- z.B. ueber alle Optuna-Trials eines Optimizer-Laufs
    hinweg, damit einmal geladene Tage nicht mehrfach abgerufen werden.

    WICHTIG: Fetcht bewusst NICHT ueber load_data() -- wiederholte schmale
    Lazy-Anfragen ueber load_data() wuerden pro Aufruf den kompletten
    Netzwerk-Overhead eines frischen Abrufs erzeugen, statt bereits geladene
    Tage wiederzuverwenden (in einem Schwesterprojekt fuehrte ein analoges
    Caching-Problem zu abweichenden Backtest-Ergebnissen zwischen eager und
    lazy). Daher direkter Exchange-Zugriff mit eigenem Tages-Cache.
    """
    def __init__(self, symbol, fine_tf):
        self.symbol = symbol
        self.fine_tf = fine_tf
        self._days = {}
        self._exchange = None

    def _get_exchange(self):
        global secrets_cache
        if self._exchange is not None:
            return self._exchange
        try:
            from mbot.utils.exchange import Exchange
            if secrets_cache is None:
                with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
                    secrets_cache = json.load(f)
            accounts = secrets_cache.get('mbot', [])
            if accounts:
                self._exchange = Exchange(accounts[0])
        except Exception:
            self._exchange = None
        return self._exchange

    def _ensure_day(self, day):
        if day in self._days:
            return
        exchange = self._get_exchange()
        if exchange is None or not exchange.markets:
            self._days[day] = None
            return
        try:
            day_start_ms = int(day.value // 10**6)
            day_end_ms   = day_start_ms + 24 * 3600 * 1000
            tf_ms        = exchange.exchange.parse_timeframe(self.fine_tf) * 1000
            all_ohlcv    = []
            current      = day_start_ms
            while current < day_end_ms:
                chunk = exchange.exchange.fetch_ohlcv(self.symbol, self.fine_tf, current, 200)
                if not chunk:
                    break
                chunk = [c for c in chunk if c[0] < day_end_ms]
                if not chunk:
                    break
                all_ohlcv.extend(chunk)
                new_current = chunk[-1][0] + tf_ms
                if new_current <= current:
                    break
                current = new_current
                time.sleep(exchange.exchange.rateLimit / 1000)
            if not all_ohlcv:
                self._days[day] = None
                return
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df.sort_index(inplace=True)
            df = df[~df.index.duplicated(keep='last')]
            self._days[day] = df if not df.empty else None
        except Exception:
            self._days[day] = None

    def get_slice(self, start_ts, end_ts):
        if self.fine_tf is None:
            return None
        start_ts = pd.Timestamp(start_ts)
        end_ts   = pd.Timestamp(end_ts)
        first_day = start_ts.floor('D')
        last_day  = (end_ts - pd.Timedelta(microseconds=1)).floor('D')
        parts = []
        day = first_day
        while day <= last_day:
            self._ensure_day(day)
            if self._days[day] is not None:
                parts.append(self._days[day])
            day += pd.Timedelta(days=1)
        if not parts:
            return None
        combined = pd.concat(parts).sort_index()
        combined = combined[~combined.index.duplicated(keep='first')]
        return combined.loc[(combined.index >= start_ts) & (combined.index < end_ts)]


def _precompute_regime_series(velocity: pd.Series, acc: pd.Series, window: int) -> pd.Series:
    """
    Vektorisierte Vorberechnung von classify_phase_regime() fuer jede Kerze
    (rolling mean/std statt Python-Loop mit wiederholten iloc-Slices+Funktions-
    aufrufen -- vermeidet O(N^2), analog zu entropy/velocity/acc/atr_ser oben).
    Mathematik identisch zu classify_phase_regime() in mdef_analysis.py.
    """
    eps = 1e-10
    vel_mean = velocity.rolling(window, min_periods=1).mean()
    vel_std  = velocity.rolling(window, min_periods=1).std()
    acc_std  = acc.rolling(window, min_periods=1).std()

    chaos_ratio     = acc_std / (vel_std + eps)
    vel_consistency = vel_mean.abs() / (vel_std + eps)

    regime = pd.Series('range', index=velocity.index, dtype=object)
    regime[vel_consistency > 0.35] = 'trend'
    regime[chaos_ratio > 1.8] = 'chaos'  # Chaos hat Prioritaet (wie im Original)
    return regime


def _get_fine_slice(fine_data, start_ts, end_ts):
    """Liest ein Fein-Daten-Fenster aus -- akzeptiert sowohl einen bereits
    komplett geladenen DataFrame (alte, eager Nutzung) als auch ein
    LazyFineData-Objekt (neue, on-demand Nutzung), per Duck-Typing."""
    if fine_data is None:
        return None
    if hasattr(fine_data, 'get_slice'):
        return fine_data.get_slice(start_ts, end_ts)
    return fine_data.loc[(fine_data.index >= start_ts) & (fine_data.index < end_ts)]


logger = logging.getLogger(__name__)

# Mindest-Kerzen bevor erstes Signal berechnet werden kann
# entropy_window (max 60) + entropy_lookback (max 20) + atr_period (max 21) + buffer
MIN_CANDLES = 110


def load_data(exchange_instance, symbol: str, timeframe: str,
              start_date: str, end_date: str) -> pd.DataFrame:
    """
    Laedt historische OHLCV-Daten von Bitget.
    Benoetigt eine Exchange-Instanz.
    """
    logger.info(f"Lade Daten: {symbol} ({timeframe}) | {start_date} -> {end_date}")
    if not hasattr(exchange_instance, 'exchange'):
        logger.error("Ungueltige Exchange-Instanz uebergeben.")
        return pd.DataFrame()

    start_ts = int(exchange_instance.exchange.parse8601(start_date + 'T00:00:00Z'))
    end_ts   = int(exchange_instance.exchange.parse8601(end_date   + 'T23:59:59Z'))
    tf_ms    = exchange_instance.exchange.parse_timeframe(timeframe) * 1000

    all_ohlcv = []
    current   = start_ts

    while current < end_ts:
        try:
            chunk = exchange_instance.exchange.fetch_ohlcv(symbol, timeframe, current, 200)
            if not chunk:
                break
            chunk = [c for c in chunk if c[0] <= end_ts]
            if not chunk:
                break
            all_ohlcv.extend(chunk)
            current = chunk[-1][0] + tf_ms
            time.sleep(exchange_instance.exchange.rateLimit / 1000)
        except Exception as e:
            logger.error(f"Fehler beim Laden der Daten: {e}")
            break

    if not all_ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep='last')]
    logger.info(f"  -> {len(df)} Kerzen geladen.")
    return df


def run_backtest(df: pd.DataFrame, signal_config: dict, risk_config: dict,
                  start_capital: float = 1000.0, symbol: str = '',
                  trial=None, fine_data: pd.DataFrame = None) -> dict:
    """
    Fuehrt den MERS-Backtest durch.

    Optimierung: Alle Features (Entropy, ATR, Velocity, Acc, Energy) werden
    einmal auf dem gesamten DataFrame vorberechnet (O(N)). Danach werden im
    Loop nur Werte per Index nachgeschlagen, statt get_mers_signal() fuer
    jede Kerze neu aufzurufen (was O(N^2) waere).

    Returns dict mit allen Ergebnissen.
    """
    risk_per_trade_pct = float(signal_config.get('risk_per_trade_pct',
                               risk_config.get('risk_per_trade_pct', 1.0)))
    leverage           = int(signal_config.get('leverage', risk_config.get('leverage', 1)))
    fee_rate           = float(risk_config.get('fee_rate_pct',       0.06)) / 100.0
    slippage_pct       = float(risk_config.get('entry_slippage_pct', 0.15))
    min_notional_usdt  = float(risk_config.get('min_notional_usdt',  5.0))

    # --- Signal-Parameter auslesen ---
    entropy_window    = int(signal_config.get('entropy_window',       20))
    entropy_lookback  = int(signal_config.get('entropy_lookback',     10))
    energy_lookback   = int(signal_config.get('energy_lookback',       5))
    min_entropy_drop  = float(signal_config.get('min_entropy_drop_pct', 0.05))
    min_energy_rise   = float(signal_config.get('min_energy_rise_pct',  0.20))
    atr_period        = int(signal_config.get('atr_period',            14))
    atr_sl_mult       = float(signal_config.get('atr_sl_mult',          1.5))
    atr_tp_mult       = float(signal_config.get('atr_tp_mult',          3.0))
    use_regime_filter = bool(int(signal_config.get('use_regime_filter',   1)))
    regime_window     = int(signal_config.get('regime_window',          20))
    allow_range_trade = bool(int(signal_config.get('allow_range_trade',   0)))
    use_multitf_filter = bool(int(signal_config.get('use_multitf_filter', 0)))
    meso_tf_mult      = int(signal_config.get('meso_tf_mult',            4))
    macro_tf_mult     = int(signal_config.get('macro_tf_mult',           16))
    use_volume_filter = bool(int(signal_config.get('use_volume_filter',   0)))
    min_vol_ratio     = float(signal_config.get('min_vol_ratio',          1.0))

    if len(df) < MIN_CANDLES + 1:
        return _empty_result(symbol, start_capital)

    # -------------------------------------------------------
    # FEATURE-VORBERECHNUNG (einmalig auf vollem DataFrame)
    # -------------------------------------------------------
    price    = df['close']
    returns  = calc_log_returns(price)
    entropy  = calc_rolling_entropy(returns, window=entropy_window)
    velocity = calc_velocity(price)
    acc      = calc_acceleration(velocity)
    energy   = calc_energy(velocity)
    atr_ser  = calc_atr(df, period=atr_period)
    regime_ser = _precompute_regime_series(velocity, acc, regime_window)
    vol_ratio_ser = df['volume'] / df['volume'].rolling(20).mean()

    capital  = start_capital
    trades   = []
    in_trade = False
    trade    = {}
    coarse_duration = df.index[1] - df.index[0] if len(df.index) >= 2 else None

    total_steps      = len(df) - MIN_CANDLES
    checkpoint_every = max(1, total_steps // 8)  # 8 Checkpoints: 12.5/25/.../100%

    for i in range(MIN_CANDLES, len(df)):
        # --- Optuna Pruning: Intermediate Value alle 25% der Kerzen ---
        if trial is not None:
            step = (i - MIN_CANDLES) // checkpoint_every
            if step > 0 and (i - MIN_CANDLES) % checkpoint_every == 0:
                pnl_so_far = (capital - start_capital) / start_capital * 100 if start_capital > 0 else 0.0
                trial.report(pnl_so_far, step)
                if trial.should_prune():
                    import optuna
                    raise optuna.exceptions.TrialPruned()

        current = df.iloc[i]

        # --- Trade-Aufloesung (SL/TP hit check) ---
        if in_trade:
            entry = trade['entry_price']
            side  = trade['side']
            sl_p  = trade['sl_price']
            tp_p  = trade['tp_price']
            hi    = current['high']
            lo    = current['low']

            hit_sl = (side == 'long'  and lo  <= sl_p) or (side == 'short' and hi >= sl_p)
            hit_tp = (side == 'long'  and hi  >= tp_p) or (side == 'short' and lo <= tp_p)

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:
                    # Beide Level in derselben Kerze moeglich -- Reihenfolge unklar
                    # ohne feinere Daten. Per fine_data (falls vorhanden) real
                    # aufloesen statt SL blind zu bevorzugen (oraclebot-Muster).
                    exit_p, result = None, None
                    if fine_data is not None and coarse_duration is not None:
                        idx_ts = df.index[i]
                        fine_slice = _get_fine_slice(fine_data, idx_ts, idx_ts + coarse_duration)
                        exit_p, result = _resolve_ambiguous_exit(fine_slice, sl_p, tp_p, side)
                    if exit_p is None:
                        result, exit_p = 'loss', sl_p  # Fallback: alte SL-first-Konvention
                elif hit_tp:
                    result = 'win'
                    exit_p = tp_p
                else:
                    result = 'loss'
                    exit_p = sl_p

                # Risiko-basierte Positionsgroesse (wie dnabot)
                sl_distance = abs(entry - trade['sl_price'])
                risk_amount = capital * risk_per_trade_pct / 100.0
                if sl_distance > 0:
                    pos_contracts = risk_amount / sl_distance
                else:
                    pos_contracts = 0.0

                if side == 'long':
                    pnl_usdt = pos_contracts * (exit_p - entry)
                else:
                    pnl_usdt = pos_contracts * (entry - exit_p)

                # Handelsgebühren (Entry + Exit Notional × fee_rate pro Leg)
                fee_usdt = pos_contracts * (entry + exit_p) * fee_rate
                pnl_usdt -= fee_usdt

                pnl_pct  = pnl_usdt / capital * 100 if capital > 0 else 0.0
                capital  = max(capital + pnl_usdt, 0.0)

                idx = df.index[i]
                exit_time = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
                trade.update({
                    'exit_price':         exit_p,
                    'exit_time':          exit_time,
                    'result':             result,
                    'pnl_pct':            round(pnl_pct, 2),
                    'pnl_usdt':           round(pnl_usdt, 2),
                    'capital_after':      round(capital, 2),
                    'risk_per_trade_pct': risk_per_trade_pct,
                    'leverage':           leverage,
                })
                trades.append(trade)
                in_trade = False
                trade = {}
            continue  # Wenn in Trade: keine neue Signal-Pruefung

        # -------------------------------------------------------
        # MERS-SIGNAL-PRUEFUNG (vorberechnete Features nutzen)
        # -------------------------------------------------------
        cur_entropy  = entropy.iloc[i]
        cur_energy   = energy.iloc[i]
        cur_acc      = acc.iloc[i]
        cur_atr      = atr_ser.iloc[i]
        entry_price  = float(price.iloc[i])

        # Lookback-Schutz
        if i < entropy_lookback or i < energy_lookback:
            continue
        prev_entropy = entropy.iloc[i - entropy_lookback]
        prev_energy  = energy.iloc[i - energy_lookback]

        # NaN / ungueltige Werte ueberspringen
        if any(np.isnan(v) for v in [cur_entropy, prev_entropy, cur_energy,
                                      prev_energy, cur_acc, cur_atr]):
            continue
        if cur_atr <= 0 or entry_price <= 0:
            continue

        # --- Layer 2: Regime-Check (optional) ---
        # regime wird immer nachgeschlagen (fuer Trade-Logging), Filter nur wenn aktiv.
        regime = regime_ser.iloc[i]
        if use_regime_filter:
            if regime == 'chaos':
                continue
            if regime == 'range' and not allow_range_trade:
                continue

        # --- Layer 1: Entropy-Bedingung ---
        if prev_entropy <= 0:
            continue
        entropy_drop = (prev_entropy - cur_entropy) / prev_entropy
        if entropy_drop < min_entropy_drop:
            continue

        # --- Layer 1: Energie-Bedingung ---
        if prev_energy <= 0:
            continue
        energy_rise = (cur_energy - prev_energy) / prev_energy
        if energy_rise < min_energy_rise:
            continue

        # --- Layer 1: Richtung via Beschleunigung ---
        if cur_acc > 0:
            side = 'long'
        elif cur_acc < 0:
            side = 'short'
        else:
            continue

        # --- Layer 3: Multi-Timeframe (optional) ---
        if use_multitf_filter:
            window_df = df.iloc[max(0, i - MIN_CANDLES):i + 1]
            mtf = calc_multitf_alignment(window_df,
                                          meso_mult=meso_tf_mult,
                                          macro_mult=macro_tf_mult)
            if not mtf['aligned']:
                continue
            if side == 'long'  and mtf['direction'] < 0:
                continue
            if side == 'short' and mtf['direction'] > 0:
                continue

        # --- Layer 5: Volumen-Bestaetigung (optional) ---
        if use_volume_filter:
            vol_ratio = vol_ratio_ser.iloc[i]
            if np.isnan(vol_ratio) or vol_ratio < min_vol_ratio:
                continue

        # --- Slippage auf Entry-Preis (Market Order Realismus) ---
        if side == 'long':
            entry_price = entry_price * (1.0 + slippage_pct / 100.0)
        else:
            entry_price = entry_price * (1.0 - slippage_pct / 100.0)

        # --- SL/TP berechnen (ATR-basiert, vom geslippten Entry) ---
        if side == 'long':
            sl_price_abs = entry_price - atr_sl_mult * cur_atr
            tp_price_abs = entry_price + atr_tp_mult * cur_atr
        else:
            sl_price_abs = entry_price + atr_sl_mult * cur_atr
            tp_price_abs = entry_price - atr_tp_mult * cur_atr

        # --- Min-Notional Check (wie Live Bot) ---
        sl_dist = abs(entry_price - sl_price_abs)
        risk_amount_check = capital * risk_per_trade_pct / 100.0
        contracts_check = risk_amount_check / sl_dist if sl_dist > 0 else 0.0
        if contracts_check * entry_price < min_notional_usdt:
            continue

        idx = df.index[i]
        entry_time = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
        in_trade = True
        trade = {
            'symbol':      symbol,
            'side':        side,
            'entry_time':  entry_time,
            'entry_price': entry_price,
            'sl_price':    sl_price_abs,
            'tp_price':    tp_price_abs,
            'atr':         round(cur_atr, 6),
            'regime':      regime,
        }

    # --- Statistiken ---
    if not trades:
        return _empty_result(symbol, start_capital)

    wins   = sum(1 for t in trades if t['result'] == 'win')
    losses = len(trades) - wins
    pnls   = [t['pnl_pct'] for t in trades]

    # Drawdown
    cap_curve = [start_capital] + [t['capital_after'] for t in trades]
    peak  = cap_curve[0]
    max_dd = 0.0
    for c in cap_curve:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    total_pnl_usdt = capital - start_capital
    total_pnl_pct  = total_pnl_usdt / start_capital * 100 if start_capital > 0 else 0.0

    return {
        'symbol':         symbol,
        'trades':         trades,
        'total_trades':   len(trades),
        'wins':           wins,
        'losses':         losses,
        'win_rate':       round(wins / len(trades) * 100, 1),
        'total_pnl_pct':  round(total_pnl_pct, 2),
        'total_pnl_usdt': round(total_pnl_usdt, 2),
        'max_drawdown':   round(max_dd, 2),
        'best_trade':     round(max(pnls), 2),
        'worst_trade':    round(min(pnls), 2),
        'start_capital':  start_capital,
        'end_capital':    round(capital, 2),
    }


def _empty_result(symbol: str, start_capital: float) -> dict:
    return {
        'symbol':         symbol,
        'trades':         [],
        'total_trades':   0,
        'wins':           0,
        'losses':         0,
        'win_rate':       0.0,
        'total_pnl_pct':  0.0,
        'total_pnl_usdt': 0.0,
        'max_drawdown':   0.0,
        'best_trade':     0.0,
        'worst_trade':    0.0,
        'start_capital':  start_capital,
        'end_capital':    start_capital,
    }
