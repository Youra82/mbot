# src/mbot/strategy/mers_signal.py
"""
MERS - Market Entropy Resonance System (vollstaendige MDEF-MERS Implementierung)

Vollstaendiger MDEF-MERS Hybrid Bot - Alle 4 Layer:

  LAYER 1 - MERS Entry-Bedingungen (Kern):
    1. Entropy faellt  -> Markt wird strukturierter (Trend bildet sich)
    2. Energie steigt  -> Momentum baut sich auf
    3. Beschleunigung  -> positiv = Long | negativ = Short

  LAYER 2 - Phasenraum Regime-Check (MDEF):
    4. [optional] Regime != 'chaos'  -> Trajektorie stabil (kein chaotischer Markt)
       'trend'  -> Spiralfoermige Bewegung -> Trading erlaubt
       'range'  -> Cluster/Schleifen      -> kein Trading (per Default off)
       'chaos'  -> chaotische Streuung    -> kein Trading

  LAYER 3 - Multi-Timeframe Resonanz (MDEF):
    5. [optional] MTF-Ausrichtung: Mikro/Meso/Makro zeigen alle in dieselbe Richtung
       Nur wenn alle Zeitskalen ausgerichtet sind wird gehandelt

  LAYER 4 - Frequenzanalyse (FFT):
    Dominant Period wird berechnet und im Signal-Dict zurueckgegeben (Logging)
    Kein harter Filter, da FFT-Stabilitaet schwer zu threshholden

  SL/TP (ATR-basiert):
    SL = entry_price +/- atr_sl_mult * ATR
    TP = entry_price +/- atr_tp_mult * ATR

  State-basierter Exit (check_mers_exit):
    Exit wenn: Entropy steigt ODER Beschleunigung wechselt Vorzeichen

Signal-Config Parameter (alle optimierbar via Optuna):
  Kern-MERS:
    entropy_window, entropy_lookback, energy_lookback
    min_entropy_drop_pct, min_energy_rise_pct
    atr_period, atr_sl_mult, atr_tp_mult

  MDEF Regime-Check:
    use_regime_filter (0/1), regime_window

  MDEF Multi-Timeframe:
    use_multitf_filter (0/1), meso_tf_mult, macro_tf_mult
    allow_range_trade (0/1): ob Range-Regime erlaubt ist
"""

import numpy as np
import pandas as pd

from mbot.strategy.mdef_analysis import (
    calc_log_returns,
    calc_rolling_entropy,
    calc_velocity,
    calc_acceleration,
    calc_energy,
    calc_atr,
    classify_phase_regime,
    calc_multitf_alignment,
    calc_dominant_period,
)


def get_mers_signal(df: pd.DataFrame, signal_config: dict) -> dict:
    """
    Vollstaendige MDEF-MERS Signal-Funktion.

    Workflow:
      Daten -> MDEF Analyse -> Regime-Check -> MTF-Check -> MERS Entry -> Signal

    Returns dict:
      side:             'long' | 'short' | None
      entry_price:      float
      sl_price:         float  (ATR-basiert)
      tp_price:         float  (ATR-basiert)
      atr:              float
      atr_sl_mult:      float
      atr_tp_mult:      float
      entropy_drop:     float
      energy_rise:      float
      acceleration:     float
      entropy:          float
      regime:           str ('trend'|'range'|'chaos'|'n/a')
      dominant_period:  float (dominante FFT-Periode in Kerzen)
      mtf_micro:        float (+1|-1|0)
      mtf_meso:         float (+1|-1|0)
      mtf_macro:        float (+1|-1|0)
      mtf_aligned:      bool
      reason:           str
    """
    # --- Kern-MERS Parameter ---
    entropy_window   = int(signal_config.get('entropy_window',       20))
    entropy_lookback = int(signal_config.get('entropy_lookback',     10))
    energy_lookback  = int(signal_config.get('energy_lookback',       5))
    min_entropy_drop = float(signal_config.get('min_entropy_drop_pct', 0.05))
    min_energy_rise  = float(signal_config.get('min_energy_rise_pct',  0.20))
    atr_period       = int(signal_config.get('atr_period',            14))
    atr_sl_mult      = float(signal_config.get('atr_sl_mult',          1.5))
    atr_tp_mult      = float(signal_config.get('atr_tp_mult',          3.0))

    # --- MDEF Regime-Check Parameter ---
    use_regime_filter = bool(int(signal_config.get('use_regime_filter', 1)))
    regime_window     = int(signal_config.get('regime_window',         20))
    allow_range_trade = bool(int(signal_config.get('allow_range_trade',  0)))

    # --- MDEF Multi-Timeframe Parameter ---
    use_multitf_filter = bool(int(signal_config.get('use_multitf_filter', 0)))
    meso_tf_mult       = int(signal_config.get('meso_tf_mult',           4))
    macro_tf_mult      = int(signal_config.get('macro_tf_mult',          16))

    # --- Mindest-Kerzen ---
    min_required = (entropy_window + max(entropy_lookback, energy_lookback)
                    + atr_period + max(regime_window, 5) + 5)
    if len(df) < min_required:
        return _no_signal('zu wenig Daten', regime='n/a')

    # -------------------------------------------------------
    # LAYER 1: MERS Basis-Berechnungen
    # -------------------------------------------------------
    price    = df['close']
    returns  = calc_log_returns(price)
    entropy  = calc_rolling_entropy(returns, window=entropy_window)
    velocity = calc_velocity(price)
    acc      = calc_acceleration(velocity)
    energy   = calc_energy(velocity)
    atr_ser  = calc_atr(df, period=atr_period)

    cur_entropy  = entropy.iloc[-1]
    prev_entropy = entropy.iloc[-(1 + entropy_lookback)]
    cur_energy   = energy.iloc[-1]
    prev_energy  = energy.iloc[-(1 + energy_lookback)]
    cur_acc      = acc.iloc[-1]
    cur_atr      = atr_ser.iloc[-1]
    entry_price  = float(price.iloc[-1])

    if any(np.isnan(v) for v in [cur_entropy, prev_entropy, cur_energy, prev_energy, cur_acc, cur_atr]):
        return _no_signal('NaN in Berechnungen', regime='n/a')
    if cur_atr <= 0 or entry_price <= 0:
        return _no_signal('ATR oder Preis <= 0', regime='n/a')

    # -------------------------------------------------------
    # LAYER 2: Phasenraum Regime-Check (MDEF)
    # -------------------------------------------------------
    regime = classify_phase_regime(velocity, acc, window=regime_window)

    if use_regime_filter:
        if regime == 'chaos':
            return _no_signal(f'Regime=chaos (Phasenraum instabil)', regime=regime)
        if regime == 'range' and not allow_range_trade:
            return _no_signal(f'Regime=range (kein Momentum)', regime=regime)

    # -------------------------------------------------------
    # LAYER 4: FFT - Dominante Periode (informativ, kein Filter)
    # -------------------------------------------------------
    fft_window     = min(64, len(price))
    dominant_period = calc_dominant_period(price, window=fft_window)

    # -------------------------------------------------------
    # LAYER 1 (Fortsetzung): MERS Entry-Bedingungen
    # -------------------------------------------------------

    # --- Bedingung 1: Entropy faellt ---
    if prev_entropy <= 0:
        return _no_signal('prev_entropy <= 0', regime=regime)
    entropy_drop = (prev_entropy - cur_entropy) / prev_entropy
    if entropy_drop < min_entropy_drop:
        return _no_signal(
            f'Entropy stagniert/steigt (drop={entropy_drop:.3f} < {min_entropy_drop})',
            regime=regime
        )

    # --- Bedingung 2: Energie steigt ---
    if prev_energy <= 0:
        return _no_signal('prev_energy <= 0 (kein Momentum)', regime=regime)
    energy_rise = (cur_energy - prev_energy) / prev_energy
    if energy_rise < min_energy_rise:
        return _no_signal(
            f'Energie faellt/stagniert (rise={energy_rise:.3f} < {min_energy_rise})',
            regime=regime
        )

    # --- Bedingung 3: Beschleunigung -> Richtung ---
    if cur_acc > 0:
        side = 'long'
    elif cur_acc < 0:
        side = 'short'
    else:
        return _no_signal('Beschleunigung = 0', regime=regime)

    # -------------------------------------------------------
    # LAYER 3: Multi-Timeframe Resonanz (MDEF)
    # -------------------------------------------------------
    mtf = calc_multitf_alignment(df, meso_mult=meso_tf_mult, macro_mult=macro_tf_mult)

    if use_multitf_filter:
        if not mtf['aligned']:
            return _no_signal(
                f'MTF nicht ausgerichtet: '
                f'micro={int(mtf["micro_trend"])} '
                f'meso={int(mtf["meso_trend"])} '
                f'macro={int(mtf["macro_trend"])}',
                regime=regime, mtf=mtf
            )
        # MTF-Richtung muss Signal-Richtung bestaetigen
        if side == 'long'  and mtf['direction'] < 0:
            return _no_signal('MTF zeigt Short, Signal Long: verworfen', regime=regime, mtf=mtf)
        if side == 'short' and mtf['direction'] > 0:
            return _no_signal('MTF zeigt Long, Signal Short: verworfen', regime=regime, mtf=mtf)

    # -------------------------------------------------------
    # SL/TP berechnen (ATR-basiert)
    # -------------------------------------------------------
    if side == 'long':
        sl_price = entry_price - atr_sl_mult * cur_atr
        tp_price = entry_price + atr_tp_mult * cur_atr
    else:
        sl_price = entry_price + atr_sl_mult * cur_atr
        tp_price = entry_price - atr_tp_mult * cur_atr

    # -------------------------------------------------------
    # Signal zusammenbauen
    # -------------------------------------------------------
    reason_parts = [
        f"MERS: Entropy-{entropy_drop:.1%} Energie+{energy_rise:.1%}",
        f"Acc={'UP' if cur_acc > 0 else 'DN'}{abs(cur_acc):.6f}",
        f"ATR={cur_atr:.4f} | SL={sl_price:.4f} TP={tp_price:.4f}",
        f"Regime={regime}",
    ]
    if dominant_period > 0:
        reason_parts.append(f"FFT-Periode={dominant_period:.0f}K")
    if use_multitf_filter:
        reason_parts.append(
            f"MTF={int(mtf['micro_trend'])}/{int(mtf['meso_trend'])}/{int(mtf['macro_trend'])}"
        )

    return {
        'side':             side,
        'entry_price':      entry_price,
        'sl_price':         round(sl_price, 6),
        'tp_price':         round(tp_price, 6),
        'atr':              round(cur_atr, 6),
        'atr_sl_mult':      atr_sl_mult,
        'atr_tp_mult':      atr_tp_mult,
        'entropy_drop':     round(entropy_drop, 4),
        'energy_rise':      round(energy_rise, 4),
        'acceleration':     round(float(cur_acc), 8),
        'entropy':          round(float(cur_entropy), 4),
        'regime':           regime,
        'dominant_period':  dominant_period,
        'mtf_micro':        mtf['micro_trend'],
        'mtf_meso':         mtf['meso_trend'],
        'mtf_macro':        mtf['macro_trend'],
        'mtf_aligned':      mtf['aligned'],
        'reason':           ' | '.join(reason_parts),
    }


def check_mers_exit(df: pd.DataFrame, signal_config: dict, entry_side: str) -> bool:
    """
    State-basierter Exit (zusaetzlich zu hartem SL/TP via Bitget Trigger).

    Gibt True zurueck wenn:
      - Entropy beginnt wieder zu steigen (Markt wird ungeordneter) ODER
      - Beschleunigung wechselt Vorzeichen (Trend-Momentum dreht)

    Wird im 'check'-Modus von run.py aufgerufen.
    """
    entropy_window = int(signal_config.get('entropy_window', 20))

    if len(df) < entropy_window + 3:
        return False

    price   = df['close']
    returns = calc_log_returns(price)
    entropy = calc_rolling_entropy(returns, window=entropy_window)
    vel     = calc_velocity(price)
    acc     = calc_acceleration(vel)

    cur_entropy  = entropy.iloc[-1]
    prev_entropy = entropy.iloc[-2]
    cur_acc      = acc.iloc[-1]

    if any(np.isnan(v) for v in [cur_entropy, prev_entropy, cur_acc]):
        return False

    entropy_rising = cur_entropy > prev_entropy
    acc_flipped    = (cur_acc < 0) if entry_side == 'long' else (cur_acc > 0)

    return entropy_rising or acc_flipped


def _no_signal(reason: str, regime: str = 'n/a', mtf: dict = None) -> dict:
    if mtf is None:
        mtf = {'micro_trend': 0.0, 'meso_trend': 0.0, 'macro_trend': 0.0,
               'aligned': False, 'direction': 0.0}
    return {
        'side':             None,
        'entry_price':      None,
        'sl_price':         None,
        'tp_price':         None,
        'atr':              None,
        'atr_sl_mult':      None,
        'atr_tp_mult':      None,
        'entropy_drop':     None,
        'energy_rise':      None,
        'acceleration':     None,
        'entropy':          None,
        'regime':           regime,
        'dominant_period':  None,
        'mtf_micro':        mtf['micro_trend'],
        'mtf_meso':         mtf['meso_trend'],
        'mtf_macro':        mtf['macro_trend'],
        'mtf_aligned':      mtf['aligned'],
        'reason':           reason,
    }
