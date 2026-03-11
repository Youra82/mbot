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
├── run_pipeline.sh                   # Interaktiver Backtest (Signal auf historischen Daten)
├── show_results.sh                   # Letzten Backtest anzeigen (Übersicht + Trade-Liste)
├── run_tests.sh                      # Pytest-Sicherheitscheck
├── update.sh                         # Git-Update (sichert secret.json)
├── install.sh                        # Erstinstallation
├── settings.json                     # Konfiguration (Symbole, Risiko, Signal-Parameter)
├── secret.json                       # API-Keys & Telegram (nicht in Git)
├── artifacts/
│   └── tracker/
│       └── global_state.json         # Aktiver Trade-Status (nicht in Git)
│
└── src/mbot/
    ├── strategy/
    │   ├── momentum_logic.py         # Bollinger-Breakout Signal-Erkennung
    │   └── run.py                    # Pro-Symbol-Runner (signal | check Modus)
    │
    ├── analysis/
    │   ├── backtester.py             # Historische Simulation
    │   ├── run_backtest_cli.py       # CLI-Wrapper für run_pipeline.sh
    │   └── show_results.py           # Report: PnL, Win-Rate, MaxDD, Trade-Liste
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

**LONG-Signal** — alle 4 Bedingungen müssen erfüllt sein:
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

Beim nächsten Cronjob-Lauf ist entweder ein Trade aktiv (→ nur prüfen) oder alle Symbole werden erneut auf Signal geprüft.

---

## Konfiguration (`settings.json`)

```json
{
    "live_trading_settings": {
        "active_strategies": [
            {"symbol": "BTC/USDT:USDT", "timeframe": "15m", "active": true},
            {"symbol": "ETH/USDT:USDT", "timeframe": "15m", "active": true},
            {"symbol": "SOL/USDT:USDT", "timeframe": "15m", "active": true}
        ]
    },
    "risk": {
        "leverage":       20,
        "margin_mode":    "isolated",
        "sl_account_pct": 2.0,
        "tp_price_pct":   1.0
    },
    "signal": {
        "bb_period":              20,
        "bb_std":                 2.0,
        "volume_ma_period":       20,
        "min_body_ratio":         0.55,
        "min_volume_multiplier":  1.4,
        "rsi_period":             14,
        "rsi_max_long":           75,
        "rsi_min_short":          25
    }
}
```

| Parameter | Erklärung |
|---|---|
| `active_strategies` | Liste der Symbole + Timeframes. Wer zuerst signalisiert, tradet. |
| `leverage` | Hebel (Standard: 20). |
| `sl_account_pct` | Maximaler Kontoverlust pro Trade in % (Standard: 2.0). |
| `tp_price_pct` | Take-Profit als Preisbewegung in % (Standard: 1.0). |
| `bb_period` | Bollinger Bands Periode (Standard: 20). |
| `bb_std` | Standardabweichungen für BB-Breite (Standard: 2.0). |
| `min_body_ratio` | Mindest-Kerzenkörper als Anteil der Gesamtrange (Standard: 0.55 = 55%). |
| `min_volume_multiplier` | Volumen muss X-faches des MA sein (Standard: 1.4). |
| `rsi_max_long` | RSI-Obergrenze für Long-Entries — kein Chasing bei Überkauf (Standard: 75). |
| `rsi_min_short` | RSI-Untergrenze für Short-Entries (Standard: 25). |

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

#### 1. Symbole und Timeframes konfigurieren

```bash
nano settings.json
```

Empfohlene Timeframes für den Momentum-Bot:

| Timeframe | Charakter | Trades/Woche (ca.) |
|---|---|---|
| `5m` | Sehr aktiv, mehr Fehlsignale | 20–50 |
| `15m` | Gutes Gleichgewicht (Standard) | 5–15 |
| `1h` | Seltener, zuverlässiger | 1–5 |

#### 2. Backtest durchführen

```bash
./run_pipeline.sh
```

Interaktive Eingabe: Symbole, Timeframes, Zeitraum, Startkapital.
Die Pipeline lädt historische OHLCV-Daten von Bitget, simuliert alle Signale und zeigt Ergebnisse.
Optional: Getestete Symbole direkt in `settings.json` eintragen.

#### 3. Ergebnisse analysieren

```bash
./show_results.sh
```

| Modus | Funktion |
|---|---|
| **1) Zusammenfassung** | Übersicht: Trades, Win-Rate, PnL, MaxDD, Endkapital pro Symbol/TF |
| **2) Detail-Ansicht** | Komplette Trade-Liste mit Entry/Exit/PnL pro Trade |

#### 4. Tests laufen lassen

```bash
./run_tests.sh
```

Führt Unit-Tests (SL/TP-Kalkulation, Global State) und einen Live-Workflow-Test auf Bitget mit PEPE (kleines Mindestkapital) aus.

#### 5. Live schalten

```bash
nano settings.json
# active: true setzen für gewünschte Symbole
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

> Das `flock`-Kommando kann verwendet werden, um parallele Starts zu verhindern:
> ```
> */5 * * * * /usr/bin/flock -n /root/mbot/mbot.lock /bin/sh -c "cd /root/mbot && .venv/bin/python3 master_runner.py >> /root/mbot/logs/cron.log 2>&1"
> ```

---

## Tägliche Verwaltung ⚙️

#### Logs ansehen

```bash
# Cronjob-Log live verfolgen
tail -f logs/cron.log

# Einzelnes Symbol
tail -n 100 logs/mbot_BTCUSDTUSDT_15m.log

# Nach Fehlern suchen
grep -i "ERROR" logs/mbot_*.log

# Master Runner
tail -f logs/master_runner.log
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
- Immer erst `./run_pipeline.sh` (Backtest) bevor Live-Trading aktiviert wird
- Den Cronjob-Intervall dem Timeframe anpassen (15m-Strategie → 5min Cronjob)
- Mit sehr kleinem Kapital (< 50 USDT): `min_volume_multiplier` ggf. reduzieren — Bitget erfordert mind. 5 USDT Notional

---

## Abhängigkeiten

```
ccxt==4.3.5      # Exchange-Verbindung (Bitget)
pandas==2.1.3    # Datenverarbeitung
ta==0.11.0       # RSI (via ta-lib Wrapper)
numpy            # Array-Operationen
requests==2.31.0 # Telegram
pytest           # Tests
```
