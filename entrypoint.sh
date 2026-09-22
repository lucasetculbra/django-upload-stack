#!/bin/sh
# Container entrypoint: wait for PostgreSQL, apply migrations, collect static files,
# then hand over to the process given as CMD (Gunicorn).
set -e

if [ "${DJANGO_DB:-postgres}" = "postgres" ]; then
    echo "[entrypoint] waiting for postgres at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432} ..."
    attempt=1
    max_attempts="${DB_WAIT_ATTEMPTS:-60}"
    until python -c "
import os, sys
import psycopg

try:
    psycopg.connect(
        dbname=os.environ['POSTGRES_DB'],
        user=os.environ['POSTGRES_USER'],
        password=os.environ['POSTGRES_PASSWORD'],
        host=os.environ.get('POSTGRES_HOST', 'db'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        connect_timeout=3,
    ).close()
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; do
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "[entrypoint] database still unreachable after ${max_attempts} attempts, giving up" >&2
            exit 1
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    echo "[entrypoint] database is ready"
fi

echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

echo "[entrypoint] collecting static files"
python manage.py collectstatic --noinput --clear

echo "[entrypoint] starting: $*"
exec "$@"
