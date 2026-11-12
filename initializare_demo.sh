#!/bin/sh
# Creează baza demo: structură, curs BNR, import registru Excel, date demonstrative.
#
# ATENȚIE: dacă baza există deja (cu date introduse de utilizatori), scriptul se OPREȘTE.
# Pentru a o reseta intenționat: ./initializare_demo.sh --reseteaza "<registru.xlsx>" "<folder rapoarte zero>"
# Chiar și atunci, baza și documentele existente se salvează întâi în date/backup/<data-ora>/.
set -e
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}
RESET=0
if [ "$1" = "--reseteaza" ]; then RESET=1; shift; fi
DB=${SQLITE_PATH:-date/db.sqlite3}
if [ -f "$DB" ]; then
  if [ "$RESET" != "1" ]; then
    echo "Baza $DB există deja și poate conține date introduse de utilizatori. Nu o modific."
    echo "Pentru resetare intenționată folosiți --reseteaza (se face întâi backup)."
    exit 1
  fi
  ./backup.sh
  rm -f "$DB"
  [ -z "$SQLITE_PATH" ] && rm -rf date/fisiere
fi
mkdir -p date
$PY -W ignore manage.py migrate -v0
for an in 2024 2025 2026; do $PY -W ignore manage.py actualizeaza_curs --an $an || true; done
$PY -W ignore manage.py import_registru "$1"
$PY -W ignore manage.py date_demo --pdf-dir "${2:-}"
