#!/bin/sh
set -eu

if [ -n "${PRINTVAULT_DATABASE_URL_FILE:-}" ]; then
    if [ ! -r "$PRINTVAULT_DATABASE_URL_FILE" ]; then
        echo "PrintVault database secret file is unreadable" >&2
        exit 1
    fi
    database_url=$(tr -d '\r\n' < "$PRINTVAULT_DATABASE_URL_FILE")
    if [ -z "$database_url" ]; then
        echo "PrintVault database secret file is empty" >&2
        exit 1
    fi
    PRINTVAULT_MIGRATION_DATABASE_URL="$database_url" python3 -c \
        'import os; from app.migrations import run_migrations; run_migrations(os.environ["PRINTVAULT_MIGRATION_DATABASE_URL"])'
fi

if [ -f /app/backend/app/main.py ]; then
    python3 -m uvicorn "${PRINTVAULT_APP_MODULE:-app.main:app}" --host 127.0.0.1 --port 8000 &
else
    python3 /app/docker/placeholder_server.py &
fi
api_pid="$!"

cleanup() {
    kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

nginx -t
exec nginx -g 'daemon off;'
