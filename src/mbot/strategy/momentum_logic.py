# src/mbot/strategy/momentum_logic.py
"""
Momentum Signal Logic fuer mbot.

Ziel: Erkennt den Moment, kurz bevor ein Kurs eine saubere ~1% Bewegung
      ohne grosse Ruecklaeufer durchzieht.

Strategie:
  1. Bollinger Band Breakout:
     - Preis schliesst AUSSERHALB der Bollinger Bands (Breakout)
     - Signalisiert, dass die Volatilitaet explodiert

  2. Volumen-Bestaetigung:
     - Aktuelle Kerzen-Volumen > min_volume_multiplier * Volumen-Durchschnitt
     - Starkes Volumen = echter Schub, kein Fake-Breakout

  3. Saubere Kerze (Momentum-Koerper):
     - Kerzenkoerper >= min_body_ratio * Gesamtrange
     - Wenig Dochte = Richtungsueberzeugung, kein Hin-und-Her

  4. RSI-Filter:
     - Bei Long: RSI nicht ueberverkauft (< rsi_max_long), kein Chasing
     - Bei Short: RSI nicht ueberkauft (> rsi_min_short)

  5. Vorheriger Squeeze (optional, Bonus-Guete-Check):
     - BBW war zuletzt unter dem 50-Perioden-Durchschnitt
     - Enge Bands -> Ausbruch hat mehr Power
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

MIN_CANDLES_REQUIRED = 60  # Mindestanzahl Kerzen fuer zuverlaessige Berechnung


def get_momentum_signal(df: pd.DataFrame, signal_config: dict) -> dict:
    """
    Analysiert den Dataframe und gibt ein Momentum-Signal zurueck.

    Args:
        df:            OHLCV DataFrame (index=timestamp)
        signal_config: dict aus settings.json['signal']

    Returns:
        {
            'side':               'long' | 'short' | None,
            'entry_price':        float,
            'body_ratio':         float,
            'volume_multiplier':  float,
            'bbw':                float,
            'rsi':                float,
            'reason':             str,  # Warum Signal / kein Signal
        }
    """
    result = {
        'side': None,
        'entry_price': 0.0,
        'body_ratio': 0.0,
        'volume_multiplier': 0.0,
        'bbw': 0.0,
        'rsi': 50.0,
        'reason': 'kein Signal',
    }

    if df is None or len(df) < MIN_CANDLES_REQUIRED:
        result['reason'] = f'Nicht genug Kerzen ({len(df) if df is not None else 0} < {MIN_CANDLES_REQUIRED})'
        logger.warning(result['reason'])
        return result

    # --- Parameter ---
    bb_period          = int(signal_config.get('bb_period', 20))
    bb_std             = float(signal_config.get('bb_std', 2.0))
    vol_ma_period      = int(signal_config.get('volume_ma_period', 20))
    min_body_ratio     = float(signal_config.get('min_body_ratio', 0.55))
    min_vol_mult       = float(signal_config.get('min_volume_multiplier', 1.4))
    rsi_period         = int(signal_config.get('rsi_period', 14))
    rsi_max_long       = float(signal_config.get('rsi_max_long', 75))
    rsi_min_short      = float(signal_config.get('rsi_min_short', 25))

    df = df.copy()

    # --- Bollinger Bands ---
    df['bb_mid']   = df['close'].rolling(bb_period).mean()
    df['bb_std']   = df['close'].rolling(bb_period).std()
    df['bb_upper'] = df['bb_mid'] + bb_std * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - bb_std * df['bb_std']
    df['bbw']      = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

    # --- Volumen MA ---
    df['vol_ma'] = df['volume'].rolling(vol_ma_period).mean()

    # --- RSI ---
    delta  = df['close'].diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_gain = gain.rolling(rsi_period).mean()
    avg_loss = loss.rolling(rsi_period).mean()
    rs   = avg_gain / avg_loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # --- Aktuelle Kerze ---
    current = df.iloc[-1]

    # Sicherstellen, dass alle Werte vorhanden sind
    if pd.isna(current['bb_upper']) or pd.isna(current['vol_ma']) or pd.isna(current['rsi']):
        result['reason'] = 'Indikatorwerte noch nicht berechnet (zu wenig Kerzen)'
        return result

    # --- Kerzen-Analyse ---
    body       = abs(current['close'] - current['open'])
    total_range = current['high'] - current['low']
    body_ratio = (body / total_range) if total_range > 1e-10 else 0.0

    # --- Volumen-Multiplikator ---
    vol_mult = (current['volume'] / current['vol_ma']) if current['vol_ma'] > 1e-10 else 0.0

    result['entry_price']       = float(current['close'])
    result['body_ratio']        = round(body_ratio, 3)
    result['volume_multiplier'] = round(vol_mult, 2)
    result['bbw']               = round(float(current['bbw']), 4)
    result['rsi']               = round(float(current['rsi']), 1)

    # --- Squeeze-Check (Bonus-Guete) ---
    bbw_avg_50 = df['bbw'].iloc[-51:-1].mean()
    was_in_squeeze = (current['bbw'] <= bbw_avg_50 * 1.5) if not pd.isna(bbw_avg_50) else True

    # --- Signal-Logik ---
    is_bullish_candle = current['close'] > current['open']
    is_bearish_candle = current['close'] < current['open']

    # LONG: Bullisher Breakout ueber BB-Upper
    if (is_bullish_candle
            and current['close'] > current['bb_upper']
            and body_ratio >= min_body_ratio
            and vol_mult >= min_vol_mult
            and current['rsi'] <= rsi_max_long):

        result['side'] = 'long'
        squeeze_note = ' (nach Squeeze)' if was_in_squeeze else ''
        result['reason'] = (
            f"LONG Breakout{squeeze_note}: "
            f"Koerper={body_ratio:.0%} Vol={vol_mult:.1f}x RSI={current['rsi']:.0f}"
        )
        logger.info(f"LONG Signal: {result['reason']}")

    # SHORT: Bearisher Breakout unter BB-Lower
    elif (is_bearish_candle
            and current['close'] < current['bb_lower']
            and body_ratio >= min_body_ratio
            and vol_mult >= min_vol_mult
            and current['rsi'] >= rsi_min_short):

        result['side'] = 'short'
        squeeze_note = ' (nach Squeeze)' if was_in_squeeze else ''
        result['reason'] = (
            f"SHORT Breakout{squeeze_note}: "
            f"Koerper={body_ratio:.0%} Vol={vol_mult:.1f}x RSI={current['rsi']:.0f}"
        )
        logger.info(f"SHORT Signal: {result['reason']}")

    else:
        # Detaillierte Erklaerung warum kein Signal
        reasons = []
        if not (is_bullish_candle and current['close'] > current['bb_upper']) and \
           not (is_bearish_candle and current['close'] < current['bb_lower']):
            reasons.append(f"kein BB-Breakout (close={current['close']:.2f}, upper={current['bb_upper']:.2f}, lower={current['bb_lower']:.2f})")
        if body_ratio < min_body_ratio:
            reasons.append(f"Koerper zu klein ({body_ratio:.0%} < {min_body_ratio:.0%})")
        if vol_mult < min_vol_mult:
            reasons.append(f"Volumen zu schwach ({vol_mult:.1f}x < {min_vol_mult:.1f}x)")
        result['reason'] = 'kein Signal: ' + ', '.join(reasons) if reasons else 'kein Signal'
        logger.debug(result['reason'])

    return result
