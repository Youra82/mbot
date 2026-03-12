# src/mbot/strategy/mdef_analysis.py
"""
MDEF - Market Dynamics & Entropy Framework (vollstaendige Implementierung)

Kern-Berechnungen fuer die MDEF-MERS Strategie:

LAYER 1 - Signalverarbeitung:
  calc_log_returns()        - Logarithmische Renditen
  calc_rolling_entropy()    - Rolling Shannon Entropy (Ordnung vs. Chaos)
  calc_velocity()           - Erste Ableitung (Momentum)
  calc_acceleration()       - Zweite Ableitung (Momentumwechsel)
  calc_energy()             - Kinetische Energie (v^2)
  calc_atr()                - Average True Range (fuer SL/TP)

LAYER 2 - Phasenraum-Analyse (MDEF-Kern):
  classify_phase_regime()   - Klassifiziert Markt als 'trend' / 'range' / 'chaos'
                              Basiert auf Phasenraum-Trajektorie (v, a)
                              Chaos-Filter: kein Trading wenn Regime = 'chaos'

LAYER 3 - Multi-Timeframe Resonanz:
  calc_multitf_alignment()  - Prueft ob Mikro/Meso/Makro-Trends ausgerichtet sind
                              Handelssignal nur bei vollstaendiger Resonanz

LAYER 4 - Frequenzanalyse (informativ):
  calc_dominant_period()    - Dominante Zyklusperiode via FFT
                              Wird im Signal-Dict zurueckgegeben (Logging)

Mathematik:
  r(t) = ln(p(t) / p(t-1))                              [Log-Rendite]
  v(t) = p(t) - p(t-1)                                  [Geschwindigkeit]
  a(t) = v(t) - v(t-1) = p(t) - 2p(t-1) + p(t-2)      [Beschleunigung]
  E(t) = v(t)^2                                          [Energie]
  H = -sum(p_i * log(p_i))                              [Shannon Entropy]
  P(f) = sum(p(t) * e^(-2*pi*i*f*t/N))                 [Fourier/FFT]
"""

import numpy as np
import pandas as pd


# ============================================================
# LAYER 1: Signalverarbeitung
# ============================================================

def calc_log_returns(price: pd.Series) -> pd.Series:
    """Logarithmische Renditen ln(p_t / p_{t-1})."""
    return np.log(price / price.shift(1)).fillna(0.0)


def calc_rolling_entropy(returns: pd.Series, window: int = 20, bins: int = 10) -> pd.Series:
    """
    Rolling Shannon Entropy der Rendite-Verteilung.

    H = -sum(p_i * log(p_i))

    Hohe Entropy  = chaotisch / unstrukturiert
    Niedrige Entropy = geordnet / trendend (Signal zum Handeln)
    """
    def _entropy(x: np.ndarray) -> float:
        counts, _ = np.histogram(x, bins=bins)
        counts = counts[counts > 0]
        if len(counts) == 0:
            return 0.0
        p = counts / counts.sum()
        return float(-np.sum(p * np.log(p + 1e-12)))

    result = returns.rolling(window).apply(_entropy, raw=True)
    return result.bfill()


def calc_velocity(price: pd.Series) -> pd.Series:
    """Erste Differenz des Preises (Geschwindigkeit / Momentum-Proxy)."""
    return price.diff().fillna(0.0)


def calc_acceleration(velocity: pd.Series) -> pd.Series:
    """
    Zweite Differenz des Preises (Beschleunigung).
    Positiv  -> Momentum nimmt zu (Long-Signal)
    Negativ  -> Momentum nimmt ab / dreht (Short-Signal)
    """
    return velocity.diff().fillna(0.0)


def calc_energy(velocity: pd.Series) -> pd.Series:
    """
    Kinetische Marktenergie = Geschwindigkeit^2.
    Steigt wenn Momentum (in beliebige Richtung) zunimmt.
    """
    return velocity ** 2


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (ATR) via EMA.
    True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    Wird fuer ATR-basierte SL/TP-Berechnung verwendet.
    """
    high       = df['high']
    low        = df['low']
    close      = df['close']
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(span=period, adjust=False).mean()


# ============================================================
# LAYER 2: Phasenraum-Regime-Klassifikation (MDEF-Kern)
# ============================================================

def classify_phase_regime(velocity: pd.Series, acceleration: pd.Series,
                           window: int = 20) -> str:
    """
    Klassifiziert das aktuelle Markt-Regime anhand der Phasenraum-Trajektorie.

    Methode:
      - vel_consistency = |mean(v)| / std(v)
            Hoch = konsistente Richtung = Trend
            Niedrig = kein klare Richtung = Range
      - chaos_ratio = std(a) / std(v)
            Hoch = Beschleunigung springt stark = Chaos
            Niedrig = ruhige, gleichmaessige Bewegung

    Returns: 'trend' | 'range' | 'chaos'

    Interpretation fuer Trading:
      'trend'  -> Spiralfoermige Trajektorie -> MERS prueft Entry
      'range'  -> Cluster/Schleifen          -> Mean-Reversion (MERS schlaeft)
      'chaos'  -> Chaotische Streuung        -> KEIN TRADING
    """
    vel_win = velocity.iloc[-window:]
    acc_win = acceleration.iloc[-window:]

    vel_std     = float(vel_win.std())
    acc_std     = float(acc_win.std())
    vel_mean_abs = float(vel_win.abs().mean())

    eps = 1e-10

    # Chaos-Indikator: Beschleunigungsvarianz relativ zu Geschwindigkeitsvarianz
    chaos_ratio = acc_std / (vel_std + eps)

    # Trend-Indikator: konsistente Richtung der Geschwindigkeit
    vel_consistency = abs(float(vel_win.mean())) / (vel_std + eps)

    # Regime-Regeln (aus Phasenraum-Theorie)
    if chaos_ratio > 1.8:
        return 'chaos'
    if vel_consistency > 0.35:
        return 'trend'
    return 'range'


# ============================================================
# LAYER 3: Multi-Timeframe Resonanz
# ============================================================

def calc_multitf_alignment(df: pd.DataFrame,
                            meso_mult: int = 4,
                            macro_mult: int = 16) -> dict:
    """
    Prueft ob Mikro-, Meso- und Makro-Zeitskala in dieselbe Richtung zeigen.

    Methode: Resampling durch Gruppierung der Mikro-Kerzen.
      - Mikro:  Originalzeitraum (z.B. 15m)
      - Meso:   Jede meso_mult-te Kerze (z.B. 4 -> 1h wenn Basis=15m)
      - Makro:  Jede macro_mult-te Kerze (z.B. 16 -> 4h wenn Basis=15m)

    Trend-Berechnung: sign(close[-1] - close[-lookback])

    Returns dict:
      micro_trend:  +1 | -1 | 0
      meso_trend:   +1 | -1 | 0
      macro_trend:  +1 | -1 | 0
      aligned:      bool (alle gleiche Richtung, nicht 0)
      direction:    +1 | -1 | 0 (gemeinsame Richtung)
    """
    close = df['close']

    # Mikro-Trend (letzte 5 Kerzen)
    micro_lb = 5
    if len(close) < micro_lb + 1:
        return _no_alignment()
    micro_trend = float(np.sign(close.iloc[-1] - close.iloc[-micro_lb - 1]))

    # Meso-Trend: jede meso_mult-te Kerze
    meso_series = close.iloc[::meso_mult]
    meso_lb = 4
    if len(meso_series) < meso_lb + 1:
        return _no_alignment()
    meso_trend = float(np.sign(meso_series.iloc[-1] - meso_series.iloc[-meso_lb - 1]))

    # Makro-Trend: jede macro_mult-te Kerze
    macro_series = close.iloc[::macro_mult]
    macro_lb = 3
    if len(macro_series) < macro_lb + 1:
        return _no_alignment()
    macro_trend = float(np.sign(macro_series.iloc[-1] - macro_series.iloc[-macro_lb - 1]))

    # Ausrichtung: alle gleich UND nicht 0
    aligned = (micro_trend == meso_trend == macro_trend) and (micro_trend != 0)
    direction = micro_trend if aligned else 0.0

    return {
        'micro_trend': micro_trend,
        'meso_trend':  meso_trend,
        'macro_trend': macro_trend,
        'aligned':     aligned,
        'direction':   direction,
    }


def _no_alignment() -> dict:
    return {
        'micro_trend': 0.0,
        'meso_trend':  0.0,
        'macro_trend': 0.0,
        'aligned':     False,
        'direction':   0.0,
    }


# ============================================================
# LAYER 4: Frequenzanalyse via FFT (informativ)
# ============================================================

def calc_dominant_period(price: pd.Series, window: int = 64) -> float:
    """
    Berechnet die dominante Zyklusperiode des Marktes via FFT.

    P(f) = sum(p(t) * e^(-2*pi*i*f*t/N))  [Fourier-Transformation]

    Returns: dominante Periode in Kerzen (z.B. 20.0 = 20-Kerzen-Zyklus)
             0.0 wenn nicht genug Daten oder keine dominante Frequenz.

    Interpretation:
      Periodenlaenge gibt den dominanten Marktrhythmus an.
      Stabil = Markt hat klare Zyklusstruktur.
      Sprunghaft = kein klarer Zyklus = chaotisch.
    """
    n = min(window, len(price))
    if n < 8:
        return 0.0

    data = price.iloc[-n:].values.astype(float)
    # Linear-Detrend (entfernt Trend-Komponente vor FFT)
    trend = np.linspace(data[0], data[-1], n)
    data  = data - trend

    fft_vals = np.abs(np.fft.rfft(data))
    freqs    = np.fft.rfftfreq(n)

    # DC-Komponente ignorieren (Frequenz = 0)
    fft_vals[0] = 0.0

    dominant_idx = int(np.argmax(fft_vals))
    if dominant_idx == 0 or freqs[dominant_idx] == 0:
        return 0.0

    return round(1.0 / freqs[dominant_idx], 1)
