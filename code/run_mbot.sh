#!/bin/bash
# Beispiel-Skript zur Ausführung des Bots, z.B. per Cronjob

# Passe den Pfad zu deinem mbot-Verzeichnis an
source /home/ubuntu/mbot/code/.venv/bin/activate
python3 /home/ubuntu/mbot/code/strategies/envelope/run.py
