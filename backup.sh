#!/bin/sh
# Copie de siguranță a bazei de date și a documentelor încărcate, în date/backup/<data-ora>/.
# Pe server: rulați zilnic din cron. Se păstrează ultimele 30 de copii.
set -e
cd "$(dirname "$0")"
DB=${SQLITE_PATH:-date/db.sqlite3}
[ -f "$DB" ] || { echo "Nu există bază de salvat."; exit 0; }
DEST="date/backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/db.sqlite3'" 2>/dev/null || cp "$DB" "$DEST/db.sqlite3"
[ -d date/fisiere ] && cp -R date/fisiere "$DEST/fisiere"
echo "Backup salvat în $DEST"
ls -1d date/backup/*/ 2>/dev/null | sort -r | tail -n +31 | xargs rm -rf 2>/dev/null || true
