#!/bin/sh
set -eu

export PORT="${PORT:-10000}"
export ENABLE_AUTOMATION="${ENABLE_AUTOMATION:-false}"

python manage.py migrate --noinput
python manage.py seed_sources
python manage.py bootstrap_cloud

if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  echo "Admin kullanicisi hazirlaniyor: ${DJANGO_SUPERUSER_USERNAME}"
  python manage.py createsuperuser --noinput \
    || echo "Admin zaten var veya olusturulamadi (devam ediliyor)."
fi

exec supervisord -n -c /app/supervisord.conf
