#!/bin/sh
set -eu

export PORT="${PORT:-10000}"
export ENABLE_AUTOMATION="${ENABLE_AUTOMATION:-false}"

python manage.py migrate --noinput 2>/dev/null || true

if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  echo "Admin kullanicisi hazirlaniyor: ${DJANGO_SUPERUSER_USERNAME}"
  python manage.py createsuperuser --noinput 2>/dev/null || true
fi

exec supervisord -n -c /app/supervisord.conf
