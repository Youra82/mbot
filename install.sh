#!/bin/bash
# mbot - Installations-Skript

echo "=== mbot Installation ==="

# Virtual Environment erstellen
python3 -m venv .venv
echo "venv erstellt."

# Packages installieren
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "Packages installiert."

# Verzeichnisse anlegen
mkdir -p logs artifacts/tracker

# Skripte ausfuehrbar machen
chmod +x *.sh

# secret.json pruefen
if [ ! -f "secret.json" ]; then
    echo "WARNUNG: secret.json fehlt! Bitte secret.json mit API-Keys befuellen."
    echo "Vorlage: secret.json.example"
else
    echo "secret.json gefunden."
fi

echo ""
echo "=== Installation abgeschlossen ==="
echo ""
echo "Naechste Schritte:"
echo "  1. secret.json mit Bitget API-Keys und Telegram-Bot befuellen"
echo "  2. settings.json anpassen (Symbole, Timeframe, Risiko)"
echo "  3. Cronjob einrichten:"
echo "     */5 * * * * cd $(pwd) && .venv/bin/python3 master_runner.py >> logs/cron.log 2>&1"
echo ""
