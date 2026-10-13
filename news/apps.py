from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def _configure_sqlite(sender, connection, **kwargs):
    """SQLite icin WAL modu: okuyucular yaziciyi bloklamaz.

    7/24 calisan sistemde runserver (okuma) ile run_studio (yazma) ayni
    anda eristiginde 'database is locked' hatasi olusuyordu. WAL ile bu
    kilit catismasi ortadan kalkar.
    """
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=60000;")
        cursor.execute("PRAGMA foreign_keys=ON;")


class NewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "news"
    verbose_name = "Haberler"
