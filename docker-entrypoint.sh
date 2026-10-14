#!/bin/sh
set -eu

export PORT="${PORT:-10000}"
export ENABLE_AUTOMATION="${ENABLE_AUTOMATION:-false}"

echo "[entrypoint] Uygulama hazirlaniyor. PORT=${PORT} DEBUG=${DJANGO_DEBUG:-false}"

echo "[entrypoint] Veritabani migration..."
python manage.py migrate --noinput || echo "[entrypoint] UYARI: migrate basarisiz oldu, devam ediliyor."

echo "[entrypoint] Statik dosyalar toplaniyor..."
python manage.py collectstatic --noinput || echo "[entrypoint] UYARI: collectstatic basarisiz oldu, devam ediliyor."

if [ "${ENABLE_AUTOMATION}" = "true" ]; then
  echo "[entrypoint] Kaynaklar ve spikerler arka planda hazirlaniyor..."
  (
    python manage.py seed_sources || echo "[entrypoint] UYARI: seed_sources basarisiz."
    python manage.py seed_anchors || echo "[entrypoint] UYARI: seed_anchors basarisiz."
    echo "[entrypoint] Otomasyon (run_studio) arka planda baslatiliyor..."
    python manage.py run_studio --interval=5 --max-age=60 --max-errors=5
  ) &
fi

echo "[entrypoint] Gunicorn baslatiliyor: 0.0.0.0:${PORT}"
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT}" --workers 2 --timeout 180 --access-logfile - --error-logfile -
