from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from news.models import Article, Category


class Command(BaseCommand):
    help = "Haber kategorilerini mevcut sınıflandırma kurallarıyla yeniden hesaplar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Değişiklikleri veritabanına kaydet (varsayılan: önizleme).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="İşlenecek en fazla haber sayısı (1-50000).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            from services import detect_category
        except ImportError as exc:
            raise CommandError("Kategori siniflandirma bagimliliklari yuklu degil.") from exc

        limit = options["limit"]
        if not 1 <= limit <= 50000:
            raise CommandError("--limit 1 ile 50000 arasinda olmalidir.")

        categories = {category.name: category for category in Category.objects.all()}
        articles = (
            Article.objects.select_related("category")
            .order_by("pk")
            .iterator(chunk_size=200)
        )
        examined = 0
        changed = 0

        for article in articles:
            if examined >= limit:
                break
            examined += 1
            fallback = article.category.name if article.category else "Gundem"
            detected_name = detect_category(
                f"{article.title} {article.summary}",
                fallback=fallback,
            )
            detected = categories.get(detected_name)
            if detected is None or detected.pk == getattr(article.category, "pk", None):
                continue

            changed += 1
            self.stdout.write(
                f"{article.pk}: {article.category or '-'} -> {detected.name}"
            )
            if options["apply"]:
                article.category = detected
                article.save(update_fields=["category"])

        mode = "kaydedildi" if options["apply"] else "önizleme; veritabanı değişmedi"
        self.stdout.write(
            self.style.SUCCESS(
                f"{examined} haber incelendi, {changed} kategori değişikliği ({mode})."
            )
        )
