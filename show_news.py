import os
import sys

import django
from django.db import DatabaseError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

django.setup()

from news.models import Article

try:
    print("TOPLAM HABER:", Article.objects.count())
    print()
    for article in Article.objects.select_related("source", "category")[:8]:
        cat = article.category.name if article.category else "-"
        print("[{}] {} ({})".format(article.country_code, cat, article.source.name))
        print("  BASLIK:", article.title)
        print("  OZET :", (article.summary or "")[:110])
        print("  YAZAR:", getattr(article, "author", None) or "-", "| okuma suresi:", getattr(article, "reading_seconds", 0), "sn")
        print()
except DatabaseError as exc:
    print("Veritabanı henüz hazır değil; tablo oluşturulmalı veya migrate çalıştırılmalı.")
    print(f"Hata: {exc}")
    raise SystemExit(1) from exc
except Exception as exc:
    print("Haber listesi okunurken beklenmeyen hata oluştu:")
    print(f"Hata: {exc}")
    raise SystemExit(1) from exc