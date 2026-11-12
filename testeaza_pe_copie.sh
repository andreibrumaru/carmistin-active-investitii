#!/bin/sh
# Rulează un script de test pe o COPIE a bazei, fără să atingă datele reale.
# Utilizare: ./testeaza_pe_copie.sh script_test.py
set -e
cd "$(dirname "$0")"
TMP=$(mktemp -d)
cp date/db.sqlite3 "$TMP/db.sqlite3"
cp -R date/fisiere "$TMP/fisiere" 2>/dev/null || mkdir -p "$TMP/fisiere"
SQLITE_PATH="$TMP/db.sqlite3" MEDIA_ROOT="$TMP/fisiere" .venv/bin/python -W ignore manage.py shell < "$1"
rm -rf "$TMP"
