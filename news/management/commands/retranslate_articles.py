from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import OperationalError

from news.models import Article
from services import is_likely_turkish, translate_text


def _with_retry(func, attempts: int = 6, delay: float = 1.5):
    """SQLite 'database is locked' hatasinda bekleyip yeniden dener."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except OperationalError as exc:
            last_exc = exc
            if attempt == attempts:
                break
            time.sleep(delay * attempt)
    raise last_exc


class Command(BaseCommand):
    help = "Aktif haberleri (baslik + ozet) Turkceye cevirir; zaten Turkce olanlara dokunmaz."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="En fazla kac haber islensin (0 = hepsi).",
        )
        parser.add_argument(
            "--batch-log",
            type=int,
            default=50,
            help="Kac haberde bir ilerleme yazilsin.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        batch_log = max(1, options["batch_log"])

        active = Article.objects.filter(is_active=True).order_by(
            "-published_at", "-ingested_at", "-pk"
        )
        total = active.count()
        queryset = active[:limit] if limit else active
        if limit:
            total = min(limit, total)

        self.stdout.write(f"Toplam {total} haber islenecek.")

        updated = 0
        skipped = 0
        failed = 0

        for index, article in enumerate(queryset.iterator(), start=1):
            try:
                new_title = article.title
                new_summary = article.summary

                if article.title and not is_likely_turkish(article.title):
                    translated = translate_text(article.title, "auto")
                    if translated and translated != article.title:
                        new_title = translated[:500]

                if article.summary and not is_likely_turkish(article.summary):
                    translated = translate_text(article.summary, "auto")
                    if translated and translated != article.summary:
                        new_summary = translated

                changed = (new_title != article.title) or (new_summary != article.summary)
                if changed:
                    article.title = new_title
                    article.summary = new_summary
                    _with_retry(
                        lambda a=article: a.save(update_fields=["title", "summary"])
                    )
                    updated += 1
                else:
                    skipped += 1
            except OperationalError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  {index}. haber kilitli: {exc}"))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.stdout.write(self.style.ERROR(f"  {index}. haber hata: {exc}"))

            if index % batch_log == 0:
                self.stdout.write(
                    f"  {index}/{total} islendi | guncellenen={updated} "
                    f"atlanan={skipped} basarisiz={failed}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Bitti. Guncellenen: {updated} | Atlanan (zaten Turkce): {skipped} "
                f"| Basarisiz: {failed}"
            )
        )
