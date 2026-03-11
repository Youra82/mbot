# mbot — Momentum Breakout Trading Bot

Ein regelbasierter Trading-Bot, der auf saubere Momentum-Ausbrüche wartet und diese mit 20x Hebel mitreitet.
Die Signal-Parameter (BB, Volumen, RSI) werden via **Optuna** auf historischen Daten optimiert und als Config-Dateien gespeichert — bevor der Bot live geht.

> **Disclaimer:** Diese Software ist experimentell und dient ausschließlich Forschungszwecken.
> Der Handel mit Kryptowährungen birgt erhebliche finanzielle Risiken. Nutzung auf eigene Gefahr.

---

## Grundidee

Der Bot lauert auf einen spezifischen Marktmoment:

```
Konsolidierung (enge Bollinger Bands)
    ↓
Preisausbruch außerhalb der Bands
    ↓
Großer Kerzenkörper (≥ 55% der Gesamtrange) → Richtungsüberzeugung
Hohes Volumen (≥ 1.4× Durchschnitt)          → Echter Schub, kein Fake-Out
RSI-Filter                                    → Kein Chasing bei Extremwerten
    ↓
Entry mit 20x Hebel + vollem verfügbaren Kapital
SL = 0.1% Preisbewegung  → −2% Kontoverlust
TP = 1.0% Preisbewegung  → +20% Kontogewinn
```

**Nur ein Symbol handelt gleichzeitig.** Wer zuerst ein Signal liefert, tradet — alle anderen warten.
Nach TP oder SL beginnt die Lauerjagd von vorne.

---

## Architektur

```
mbot/
├── master_runner.py                  # Cronjob-Orchestrator (Global State Management)
├── auto_optimizer_scheduler.py       # Auto-Optimizer Zeitplan-Prüfer
├── run_pipeline.sh                   # Optuna-Optimierung + Config-Training
├── show_results.sh                   # Analyse-Dashboard (4 Modi)
├── push_configs.sh                   # Trainierte Configs auf GitHub pushen
├── run_tests.sh                      # Pytest-Sicherheitscheck
├── update.sh                         # Git-Update (sichert secret.json)
├── install.sh                        # Erstinstallation
├── settings.json                     # Konfiguration (Symbole, Risiko, Optimierung)
├── secret.json                       # API-Keys & Telegram (nicht in Git)
├── artifacts/
│   ├── tracker/
│   │   └── global_state.json         # Aktiver Trade-Status (nicht in Git)
│   ├── results/
│   │   └── last_optimizer_run.json   # Letzter Optimizer-Lauf (nicht in Git)
│   ├── charts/                       # Generierte HTML-Charts
│   ├── db/
│   │   └── optuna_studies_mbot.db    # Optuna SQLite Datenbank
│   └── cache/
│       └── .last_optimization_run    # Timestamp letzter Auto-Optimizer-Lauf
│
└── src/mbot/
    ├── strategy/
    │   ├── momentum_logic.py         # Bollinger-Breakout Signal-Erkennung
    │   ├── run.py                    # Pro-Symbol-Runner (signal | check Modus)
    │   └── configs/
    │       └── config_*_momentum.json  # Optimierte Signal-Configs pro Symbol/TF
    │
    ├── analysis/
    │   ├── backtester.py             # Historische Simulation
    │   ├── optimizer.py              # Optuna Signal-Parameter Optimizer
    │   ├── portfolio_simulator.py    # Portfolio-Simulation (gemeinsamer Kapitalpool)
    │   ├── interactive_chart.py      # Plotly Candlestick + Trade-Marker Charts
    │   └── show_results.py           # 4-Modus Analyse-Dashboard
    │
    └── utils/
        ├── exchange.py               # Bitget CCXT Wrapper
        ├── trade_manager.py          # Entry/SL/TP + Global State
        ├── telegram.py               # Telegram-Benachrichtigungen
        └── guardian.py               # Crash-Schutz Decorator
```

---

## Signal-Logik

### Bollinger Band Breakout

```
BB-Upper = SMA(20) + 2.0 × StdDev(20)
BB-Lower = SMA(20) − 2.0 × StdDev(20)
BB-Width = (BB-Upper − BB-Lower) / BB-Mid
```

**LONG-Signal** — alle Bedingungen müssen erfüllt sein:
```
close > BB-Upper          ← Ausbruch über Oberband
close > open              ← Bullische Kerze
Kerzenkörper ≥ 55%        ← Sauber, keine großen Dochte
Volumen ≥ 1.4× MA(20)     ← Echter Schub
RSI ≤ 75                  ← Kein Chasing bei Überkauf
```

**SHORT-Signal** — gespiegelt:
```
close < BB-Lower          ← Ausbruch unter Unterband
close < open              ← Bearische Kerze
Kerzenkörper ≥ 55%        ← Sauber, keine großen Dochte
Volumen ≥ 1.4× MA(20)     ← Echter Druck
RSI ≥ 25                  ← Kein Chasing bei Überverkauf
```

### Beispiel-Signal

```
[Momentum Signal erkannt]
  Symbol:    BTC/USDT:USDT (15m)
  Richtung:  LONG (Breakout nach Squeeze)
  Körper:    78%  (sauber, wenig Dochte)
  Volumen:   2.1× Durchschnitt
  RSI:       61

  Entry:     43.250 USDT (Market Order)
  SL:        43.207 USDT (−0.1% Preis = −2.0% Konto)
  TP:        43.683 USDT (+1.0% Preis = +20.0% Konto)
  Hebel:     20×  |  Kapital: 100 USDT  |  Position: 2.000 USDT
```

---

## Risiko-Kalkulation

Mit 20× Hebel und vollem Kapital:

| | Preis-Bewegung | Konto-Auswirkung |
|---|---|---|
| **Stop Loss** | −0.10% | −2.0% (fest) |
| **Take Profit** | +1.00% | +20.0% |
| **R:R Ratio** | — | **1 : 10** |

```
Konto = 100 USDT
Position = 100 × 20 = 2.000 USDT

SL-Abstand = sl_account_pct / leverage = 2.0% / 20 = 0.1% Preis
TP-Abstand = tp_price_pct = 1.0% Preis

Verlust bei SL:  2.000 × 0.1%  =  2 USDT  = 2% des Kontos
Gewinn bei TP:   2.000 × 1.0%  = 20 USDT  = 20% des Kontos
```

---

## Global State — Nur ein Trade gleichzeitig

```
artifacts/tracker/global_state.json
```

```
master_runner.py startet (Cronjob)
    │
    ├── [Auto-Optimizer] Scheduler im Hintergrund prüfen
    │
    ├── use_auto_optimizer_results = true?
    │       ├── JA  → Symbole aus src/mbot/strategy/configs/ laden
    │       └── NEIN → Symbole aus active_strategies in settings.json
    │
    ├── global_state.active_symbol gesetzt?
    │       │
    │       ├── JA  → run.py --mode check
    │       │           Position noch offen? → warten
    │       │           Position geschlossen? → State löschen, Telegram, neu starten
    │       │
    │       └── NEIN → run.py --mode signal (für jedes Symbol, sequenziell)
    │                   Signal? → Entry + SL/TP platzieren → State setzen → fertig
    │                   Kein Signal? → nächstes Symbol prüfen
```

---

## Konfiguration (`settings.json`)

```json
{
    "live_trading_settings": {
        "max_open_positions": 1,
        "use_auto_optimizer_results": false,
        "active_strategies": [
            {"symbol": "BTC/USDT:USDT", "timeframe": "15m", "active": true},
            {"symbol": "ETH/USDT:USDT", "timeframe": "15m", "active": true},
            {"symbol": "SOL/USDT:USDT", "timeframe": "15m", "active": true}
        ]
    },
    "optimization_settings": {
        "enabled": true,
        "schedule": {
            "day_of_week": 6,
            "hour": 15,
            "minute": 0,
            "interval": {"value": 7, "unit": "days"}
        },
        "symbols_to_optimize": "auto",
        "timeframes_to_optimize": "auto",
        "lookback_days": "auto",
        "start_capital": 1000,
        "cpu_cores": -1,
        "num_trials": 200,
        "mode": "strict",
        "constraints": {
            "max_drawdown_pct": 30,
            "min_win_rate_pct": 50,
            "min_pnl_pct": 0
        },
        "send_telegram_on_completion": true
    },
    "risk": {
        "leverage":       20,
        "margin_mode":    "isolated",
        "sl_account_pct": 2.0,
        "tp_price_pct":   1.0
    },
    "signal": {
        "bb_period":             20,
        "bb_std":                2.0,
        "volume_ma_period":      20,
        "min_body_ratio":        0.55,
        "min_volume_multiplier": 1.4,
        "rsi_period":            14,
        "rsi_max_long":          75,
        "rsi_min_short":         25
    }
}
```

| Parameter | Erklärung |
|---|---|
| `max_open_positions` | Immer 1 — mbot handelt nur einen Trade gleichzeitig. |
| `use_auto_optimizer_results` | `true` → Symbole aus trainierten Config-Dateien; `false` → manuelle Liste. |
| `active_strategies` | Liste der Symbole + Timeframes. Wer zuerst signalisiert, tradet. |
| `optimization_settings.enabled` | Auto-Optimizer aktivieren/deaktivieren. |
| `optimization_settings.schedule` | Wann der Auto-Optimizer läuft (Tag, Uhrzeit, Intervall). |
| `optimization_settings.mode` | `strict` = profitabel & sicher; `best_profit` = maximaler PnL. |
| `leverage` | Hebel (Standard: 20). |
| `sl_account_pct` | Maximaler Kontoverlust pro Trade in % (Standard: 2.0). |
| `tp_price_pct` | Take-Profit als Preisbewegung in % (Standard: 1.0). |
| `bb_period` | Bollinger Bands Periode (Fallback wenn keine Config vorhanden). |
| `min_body_ratio` | Mindest-Kerzenkörper als Anteil der Gesamtrange (Standard: 0.55 = 55%). |
| `min_volume_multiplier` | Volumen muss X-faches des MA sein (Standard: 1.4). |

---

## Installation 🚀

#### 1. Projekt klonen

```bash
git clone https://github.com/Youra82/mbot.git
cd mbot
```

#### 2. Installations-Skript ausführen

```bash
chmod +x install.sh
bash ./install.sh
```

Das Skript erstellt die virtuelle Python-Umgebung, installiert alle Abhängigkeiten und legt die Verzeichnisstruktur an.

#### 3. API-Keys eintragen

```bash
nano secret.json
```

```json
{
    "mbot": [
        {
            "name": "Account-1",
            "apiKey": "DEIN_BITGET_API_KEY",
            "secret": "DEIN_BITGET_SECRET",
            "password": "DEIN_BITGET_PASSPHRASE"
        }
    ],
    "telegram": {
        "bot_token": "DEIN_TELEGRAM_BOT_TOKEN",
        "chat_id":   "DEINE_CHAT_ID"
    }
}
```

---

## Workflow

#### 1. Signal-Parameter trainieren (Optimizer)

```bash
./run_pipeline.sh
```

Interaktive Eingabe: Symbole, Timeframes, Zeitraum, Startkapital, Anzahl Trials.
Optuna optimiert BB/Volumen/RSI-Parameter für jedes Symbol/TF-Paar und speichert die besten Configs in `src/mbot/strategy/configs/`.
Optional: Optimierte Strategien direkt in `settings.json` eintragen.

#### 2. Ergebnisse analysieren

```bash
./show_results.sh
```

| Modus | Funktion |
|---|---|
| **1) Einzel-Analyse** | Jede Strategie isoliert backtesten — Trades, Win-Rate, PnL, MaxDD |
| **2) Manuelle Portfolio-Simulation** | Eigene Strategieauswahl, gemeinsamer Kapitalpool |
| **3) Auto Portfolio-Optimierung** | Bot findet das beste Strategie-Team unter Drawdown-Constraint |
| **4) Interaktive Charts** | Plotly Candlestick + BB-Bands + Entry/Exit-Marker + Equity-Kurve als HTML |

#### 3. Configs auf GitHub pushen (optional, für VPS-Deployment)

```bash
./push_configs.sh
```

Staged nur die generierten Config-Dateien, erstellt einen Commit und pusht auf `origin/main`.

#### 4. Tests laufen lassen

```bash
./run_tests.sh
```

Führt Unit-Tests (SL/TP-Kalkulation, Global State) und einen Live-Workflow-Test auf Bitget durch.

#### 5. Live schalten

```bash
nano settings.json
# active: true setzen für gewünschte Symbole
# use_auto_optimizer_results: true → trainierte Configs verwenden
```

#### 6. Cronjob einrichten

```bash
crontab -e
```

```cron
# Für 15m-Strategien: alle 5 Minuten prüfen
*/5 * * * * cd /root/mbot && .venv/bin/python3 master_runner.py >> /root/mbot/logs/cron.log 2>&1

# Für 1h-Strategien: jede Stunde, 2 Min nach voll
2 * * * * cd /root/mbot && .venv/bin/python3 master_runner.py >> /root/mbot/logs/cron.log 2>&1
```

> Mit `flock` parallele Starts verhindern:
> ```
> */5 * * * * /usr/bin/flock -n /root/mbot/mbot.lock /bin/sh -c "cd /root/mbot && .venv/bin/python3 master_runner.py >> /root/mbot/logs/cron.log 2>&1"
> ```

---

## Auto-Optimizer

Der `auto_optimizer_scheduler.py` wird von `master_runner.py` bei jedem Cronjob-Lauf im Hintergrund aufgerufen. Er prüft ob eine Optimierung fällig ist und startet sie bei Bedarf automatisch.

```bash
# Manuell erzwingen (ignoriert Zeitplan):
.venv/bin/python3 auto_optimizer_scheduler.py --force

# Protokoll ansehen:
tail -f logs/auto_optimizer_trigger.log
```

**Ablauf:**
```
Cronjob → master_runner.py
    └── auto_optimizer_scheduler.py (Hintergrund)
            ├── Zeitplan fällig? (z.B. samstags 15:00 oder alle 7 Tage)
            │       NEIN → sofort beenden
            │       JA  → .optimization_in_progress Lock setzen
            │               Telegram: "Optimizer gestartet"
            │               optimizer.py für alle aktiven Symbole/TF starten
            │               Telegram: Ergebniszusammenfassung senden
            │               Lock löschen, Timestamp aktualisieren
```

---

## Tägliche Verwaltung ⚙️

#### Logs ansehen

```bash
# Cronjob-Log live verfolgen
tail -f logs/cron.log

# Master Runner
tail -f logs/master_runner.log

# Auto-Optimizer
tail -f logs/auto_optimizer_trigger.log

# Nach Fehlern suchen
grep -i "ERROR" logs/master_runner.log
```

#### Aktiven Trade ansehen

```bash
cat artifacts/tracker/global_state.json
```

#### Master Runner manuell starten

```bash
cd /root/mbot && .venv/bin/python3 master_runner.py
```

#### Global State manuell zurücksetzen

```bash
# Falls ein Trade hängt und keine Position mehr offen ist:
echo '{"active_symbol":null,"active_timeframe":null,"active_since":null,"entry_price":null,"side":null,"sl_price":null,"tp_price":null,"contracts":null}' > artifacts/tracker/global_state.json
```

#### Bot aktualisieren

```bash
./update.sh
```

Sichert automatisch `secret.json` vor dem `git reset --hard origin/main`.

---

## Wichtige Regeln

- `secret.json` ist **nicht in Git** — wird von `update.sh` automatisch gesichert
- `artifacts/tracker/global_state.json` ist **nicht in Git** — enthält den aktiven Trade-Status
- Immer erst `./run_pipeline.sh` (Optimizer) bevor Live-Trading aktiviert wird
- Den Cronjob-Intervall dem Timeframe anpassen (15m-Strategie → 5min Cronjob)
- `use_auto_optimizer_results: true` erst aktivieren nachdem `run_pipeline.sh` erfolgreich war
- Mit sehr kleinem Kapital (< 50 USDT): `min_volume_multiplier` ggf. reduzieren — Bitget erfordert mind. 5 USDT Notional

---

## Abhängigkeiten

```
ccxt==4.3.5      # Exchange-Verbindung (Bitget)
pandas==2.1.3    # Datenverarbeitung
ta==0.11.0       # RSI (via ta-lib Wrapper)
numpy            # Array-Operationen
optuna==4.5.0    # Signal-Parameter Optimierung
requests==2.31.0 # Telegram
plotly           # Interaktive Charts (Modus 4)
pytest           # Tests
```
