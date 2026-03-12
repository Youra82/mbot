# mbot — MDEF-MERS Hybrid Trading Bot

Ein mathematisch fundierter Trading-Bot basierend auf **Signalverarbeitung**, **Phasenraum-Dynamik** und **Shannon-Entropy**. Der Bot betrachtet den Markt nicht als Preisdiagramm, sondern als dynamisches System — und handelt nur in geordneten, trendenden Regimen.

> **Disclaimer:** Diese Software ist experimentell und dient ausschließlich Forschungszwecken.
> Der Handel mit Kryptowährungen birgt erhebliche finanzielle Risiken. Nutzung auf eigene Gefahr.

---

## Grundidee: Markt als dynamisches System

Klassische Bots handeln Preismuster. Dieser Bot handelt **Systemzustände**:

```
Marktdaten → Signalzerlegung (Entropy, Energie, Beschleunigung)
                    ↓
            MDEF Analyse (Phasenraum-Regime, Multi-Timeframe)
                    ↓
            Regime = 'trend' ? → MERS prüft Entry-Bedingungen
            Regime = 'chaos' ? → Kein Trading
                    ↓
            Alle 3 Entry-Bedingungen erfüllt? → Trade mit ATR-basiertem SL/TP
```

**Nur ein Symbol handelt gleichzeitig.** Wer zuerst ein Signal liefert, tradet — alle anderen warten.

---

## Strategie: MDEF-MERS (4 Layer)

### Layer 1 — MERS: Kern-Entry-Bedingungen

Alle drei müssen gleichzeitig erfüllt sein:

| Bedingung | Formel | Bedeutung |
|---|---|---|
| **Entropy fällt** | `H(t) < H(t−k)` | Markt wird strukturierter → Trend bildet sich |
| **Energie steigt** | `E(t) > E(t−j)` | Momentum baut sich auf |
| **Beschleunigung** | `a(t) > 0` → Long / `a(t) < 0` → Short | Richtung des Momentums |

**Long Entry:**  `H(t) < H(t−entropy_lookback)  ∧  E(t) > E(t−energy_lookback)  ∧  a(t) > 0`
**Short Entry:** `H(t) < H(t−entropy_lookback)  ∧  E(t) > E(t−energy_lookback)  ∧  a(t) < 0`

**State-basierter Exit:**  `H(t) > H(t−1)  ∨  sign(a(t)) ≠ sign(a(t−1))`

### Layer 2 — MDEF: Phasenraum Regime-Check

Klassifiziert den Markt anhand der Phasenraum-Trajektorie `S(t) = (p, v, a)`:

| Regime | Beschreibung | Trading |
|---|---|---|
| `trend` | Spiralförmige Trajektorie, konsistentes Momentum | Entry erlaubt |
| `range` | Cluster/Schleifen, kein klares Momentum | Konfigurierbar |
| `chaos` | Chaotische Streuung, hohe Instabilität | **Kein Trading** |

Berechnung via `vel_consistency = |mean(v)| / std(v)` und `chaos_ratio = std(a) / std(v)`.

### Layer 3 — MDEF: Multi-Timeframe Resonanz

Handel nur wenn Mikro, Meso und Makro-Zeitskala in dieselbe Richtung zeigen:

```
Trend_T = sign(p(t) − p(t−T))

Mikro  (Originalzeitraum)        ─┐
Meso   (meso_tf_mult × Mikro)    ─┼─ alle gleich → Resonanz → Entry
Makro  (macro_tf_mult × Mikro)   ─┘
```

### Layer 4 — Frequenzanalyse (FFT, informativ)

Dominante Marktzyklusperiode via Fourier-Transformation:

```
P(f) = Σ p(t) · e^(−2πift/N)
```

Wird im Signal-Dict und Telegram-Meldung ausgegeben. Kein harter Filter.

---

## Mathematik (vollständige Gleichungen)

| Konzept | Formel | Implementierung |
|---|---|---|
| Log-Rendite | `r(t) = ln(p(t)/p(t−1))` | `calc_log_returns()` |
| Geschwindigkeit | `v(t) = p(t) − p(t−1)` | `calc_velocity()` |
| Beschleunigung | `a(t) = v(t) − v(t−1)` | `calc_acceleration()` |
| Phase-Space | `S(t) = (p(t), v(t), a(t))` | `classify_phase_regime()` |
| Energie | `E(t) = v(t)²` | `calc_energy()` |
| Shannon Entropy | `H = −Σ pᵢ·log(pᵢ)` | `calc_rolling_entropy()` |
| True Range | `TR = max(H−L, \|H−C₋₁\|, \|L−C₋₁\|)` | `calc_atr()` |
| FFT Periode | `1 / argmax(\|FFT(p)\|)` | `calc_dominant_period()` |
| MTF Trend | `sign(p(t) − p(t−T))` | `calc_multitf_alignment()` |

---

## SL/TP (ATR-basiert)

```
ATR(t) = EMA(TR(t), atr_period)

Long:  SL = entry − atr_sl_mult × ATR    TP = entry + atr_tp_mult × ATR
Short: SL = entry + atr_sl_mult × ATR    TP = entry − atr_tp_mult × ATR

Standard: atr_sl_mult = 1.5  →  atr_tp_mult = 3.0  →  R:R = 1:2
```

---

## Architektur

```
mbot/
├── master_runner.py                  # Cronjob-Orchestrator (Global State)
├── auto_optimizer_scheduler.py       # Auto-Optimizer Zeitplan-Prüfer
├── run_pipeline.sh                   # Optuna-Optimierung + Config-Training
├── show_results.sh                   # Analyse-Dashboard (4 Modi)
├── push_configs.sh                   # Trainierte Configs auf GitHub pushen
├── run_tests.sh                      # Pytest-Sicherheitscheck
├── update.sh                         # Git-Update (sichert secret.json)
├── install.sh                        # Erstinstallation
├── settings.json                     # Konfiguration (Symbole, Risiko, Signal)
├── secret.json                       # API-Keys & Telegram (nicht in Git)
├── artifacts/
│   ├── tracker/global_state.json     # Aktiver Trade-Status (nicht in Git)
│   ├── results/last_optimizer_run.json
│   ├── charts/                       # Generierte HTML-Charts
│   └── db/optuna_studies_mbot.db     # Optuna SQLite Datenbank
│
└── src/mbot/
    ├── strategy/
    │   ├── mdef_analysis.py          # MDEF: Entropy, Velocity, Acc, Energy, ATR, FFT, MTF, Regime
    │   ├── mers_signal.py            # MERS: 4-Layer Signal-Logik + State-Exit
    │   ├── run.py                    # Pro-Symbol-Runner (signal | check Modus)
    │   └── configs/
    │       └── config_*_mers.json    # Optimierte MERS-Configs pro Symbol/TF
    │
    ├── analysis/
    │   ├── backtester.py             # Historische Simulation (ATR-basierte SL/TP)
    │   ├── optimizer.py              # Optuna — 15 MERS-Parameter optimieren
    │   ├── portfolio_simulator.py    # Portfolio-Simulation
    │   ├── interactive_chart.py      # Plotly Charts
    │   └── show_results.py           # 4-Modus Analyse-Dashboard
    │
    └── utils/
        ├── exchange.py               # Bitget CCXT Wrapper
        ├── trade_manager.py          # Entry/SL/TP (ATR) + Global State
        ├── telegram.py               # Benachrichtigungen
        └── guardian.py               # Crash-Schutz Decorator
```

---

## Optimizer — 15 Parameter (Optuna)

Der Optimizer findet automatisch die besten Werte für alle 15 Parameter:

| Parameter | Bereich | Beschreibung |
|---|---|---|
| `entropy_window` | 10–60 | Rollierendes Fenster für Shannon Entropy |
| `entropy_lookback` | 3–25 | Kerzen zurück für Entropy-Vergleich |
| `energy_lookback` | 3–25 | Kerzen zurück für Energie-Vergleich |
| `min_entropy_drop_pct` | 0.01–0.35 | Minimaler relativer Entropy-Abfall |
| `min_energy_rise_pct` | 0.05–1.50 | Minimaler relativer Energie-Anstieg |
| `atr_period` | 7–28 | ATR-Berechnungsperiode |
| `atr_sl_mult` | 0.5–3.0 | SL-Abstand = Mult × ATR |
| `atr_tp_mult` | > atr_sl_mult, max 6.0 | TP-Abstand = Mult × ATR |
| `use_regime_filter` | 0 / 1 | Phasenraum Regime-Check aktivieren |
| `regime_window` | 10–40 | Fenster für Regime-Klassifikation |
| `allow_range_trade` | 0 / 1 | Trading auch im Range-Regime erlauben |
| `use_multitf_filter` | 0 / 1 | Multi-Timeframe Filter aktivieren |
| `meso_tf_mult` | 2–8 | Meso-Zeitskala (z.B. 4 → 15m×4 = 1h) |
| `macro_tf_mult` | 8–32 | Makro-Zeitskala (z.B. 16 → 15m×16 = 4h) |

---

## Konfiguration (`settings.json`)

```json
{
    "risk": {
        "leverage": 20,
        "margin_mode": "isolated"
    },
    "signal": {
        "entropy_window": 20,
        "entropy_lookback": 10,
        "energy_lookback": 5,
        "min_entropy_drop_pct": 0.05,
        "min_energy_rise_pct": 0.20,
        "atr_period": 14,
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 3.0,
        "use_regime_filter": 1,
        "regime_window": 20,
        "allow_range_trade": 0,
        "use_multitf_filter": 0,
        "meso_tf_mult": 4,
        "macro_tf_mult": 16
    }
}
```

---

## Installation

```bash
git clone https://github.com/Youra82/mbot.git
cd mbot
chmod +x install.sh && bash ./install.sh
nano secret.json
```

```json
{
    "mbot": [{"name": "Account-1", "apiKey": "...", "secret": "...", "password": "..."}],
    "telegram": {"bot_token": "...", "chat_id": "..."}
}
```

---

## Workflow

#### 1. MERS-Parameter optimieren

```bash
./run_pipeline.sh
```

Optuna optimiert alle 15 MERS-Parameter auf historischen Daten. Ergebnis: `config_*_mers.json` in `src/mbot/strategy/configs/`.

#### 2. Ergebnisse analysieren

```bash
./show_results.sh
```

| Modus | Funktion |
|---|---|
| **1) Einzel-Analyse** | Jede Strategie isoliert backtesten |
| **2) Manuelle Portfolio-Simulation** | Eigene Strategieauswahl |
| **3) Auto Portfolio-Optimierung** | Bot findet bestes Strategie-Team |
| **4) Interaktive Charts** | Plotly Candlestick + Entry/Exit + Equity-Kurve |

#### 3. Tests

```bash
./run_tests.sh
```

#### 4. Configs pushen (für VPS)

```bash
./push_configs.sh
```

#### 5. Cronjob einrichten

```bash
crontab -e
```

```cron
# 15m-Strategie → alle 5 Minuten
*/5 * * * * /usr/bin/flock -n /root/mbot/mbot.lock /bin/sh -c "cd /root/mbot && .venv/bin/python3 master_runner.py >> /root/mbot/logs/cron.log 2>&1"
```

---

## Global State — Nur ein Trade gleichzeitig

```
master_runner.py (Cronjob)
    │
    ├── global_state.active_symbol gesetzt?
    │       ├── JA  → run.py --mode check
    │       │           MERS State-Exit prüfen (Entropy/Acc gedreht?)
    │       │           Position noch offen? → warten
    │       │           Position geschlossen? → State löschen, Telegram
    │       │
    │       └── NEIN → run.py --mode signal (jedes Symbol sequenziell)
    │                   MDEF-MERS Signal? → Entry + SL/TP → State setzen
```

---

## Telegram-Nachrichten

**Neuer Trade:**
```
mbot MERS - NEUER TRADE

Symbol:    BTC/USDT:USDT (15m)
Richtung:  LONG
Entry:     43250.0000 USDT
SL:        43158.5000 (0.21% Preis)
TP:        43433.0000 (0.42% Preis)
R:R:       1:2.0
Hebel:     20x | Kapital: 100.00 USDT
Kontrakte: 0.0462

Signal: MERS: Entropy-12.3% Energie+34.5% AccUP0.001234 ATR=91.5 | Regime=trend | FFT-Periode=20K
```

**State-Exit (vor SL/TP):**
```
mbot MERS - STATE EXIT

Symbol:  BTC/USDT:USDT (15m)
Grund:   Entropy steigt / Beschleunigung dreht
(MERS state-basierter Exit vor SL/TP)
```

---

## Tägliche Verwaltung

```bash
# Logs
tail -f logs/cron.log
tail -f logs/master_runner.log

# Aktiven Trade
cat artifacts/tracker/global_state.json

# Global State manuell zurücksetzen
echo '{"active_symbol":null,"active_timeframe":null,"active_since":null,"entry_price":null,"side":null,"sl_price":null,"tp_price":null,"contracts":null}' > artifacts/tracker/global_state.json

# Auto-Optimizer erzwingen
.venv/bin/python3 auto_optimizer_scheduler.py --force

# Bot aktualisieren
./update.sh
```

---

## Abhängigkeiten

```
ccxt==4.3.5      # Exchange-Verbindung (Bitget)
pandas==2.1.3    # Datenverarbeitung
numpy            # Array-Operationen, FFT
optuna==4.5.0    # MERS-Parameter Optimierung (15 Parameter)
requests==2.31.0 # Telegram
plotly           # Interaktive Charts
pytest           # Tests
```
