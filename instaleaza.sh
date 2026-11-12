#!/bin/sh
# Instalare pentru un dezvoltator nou (după git clone). Creează o bază GOALĂ, locală – nu atinge datele altcuiva.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-dev.txt
[ -f .env ] || { cp .env.example .env; echo "Am creat .env din .env.example – completați-l (SECRET_KEY etc.)."; }
mkdir -p date
.venv/bin/python -W ignore manage.py migrate -v0
echo "Gata. Creați-vă un cont de administrator:  .venv/bin/python manage.py createsuperuser"
echo "Apoi porniți aplicația:                     ./porneste.sh 8001"
