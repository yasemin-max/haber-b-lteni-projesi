import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

try:
    from news.bootstrap import start as _bootstrap_start

    _bootstrap_start()
except Exception:  # noqa: BLE001 - acilis asla engellenmemeli
    pass
