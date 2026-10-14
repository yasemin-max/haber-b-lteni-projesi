#!/bin/sh
set -eu

export PORT="${PORT:-10000}"
export ENABLE_AUTOMATION="${ENABLE_AUTOMATION:-false}"

python manage.py migrate --noinput 2>/dev/null || true
python manage.py collectstatic --noinput 2>/dev/null || true

exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 180
