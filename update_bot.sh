#!/bin/bash

# Bricht das Skript bei Fehlern sofort ab
set -e

SECRET_FILE="secret.json"
BACKUP_FILE="secret.json.bak"

echo "--- Sicheres Update für mbot wird ausgeführt (v3 - vollautomatisch) ---"

# Schritt 1: Backup der sensiblen Schlüsseldatei erstellen
echo "1. Erstelle ein Backup von '$SECRET_FILE' nach '$BACKUP_FILE'..."
cp "$SECRET_FILE" "$BACKUP_FILE"

# Schritt 2: Lokale Änderungen (insbesondere die secret.json) sicher beiseite legen
echo "2. Lege alle lokalen Änderungen mit 'git stash' sicher beiseite..."
git stash push --include-untracked

# Schritt 3: Neuesten Stand des Bot-Codes von GitHub holen
echo "3. Hole die neuesten Updates von GitHub (vollautomatisch)..."
git pull origin main --no-rebase --no-edit

# Schritt 4: Lokale Änderungen aus dem Zwischenspeicher zurückholen
echo "4. Hole die lokalen Änderungen (deine Keys) aus dem Zwischenspeicher zurück..."
git stash pop

# Schritt 5: Backup wiederherstellen, um absolute Sicherheit zu garantieren
echo "5. Stelle den Inhalt von '$SECRET_FILE' aus dem Backup wieder her..."
cp "$BACKUP_FILE" "$SECRET_FILE"

echo "✅ mbot Update erfolgreich und vollautomatisch abgeschlossen."
