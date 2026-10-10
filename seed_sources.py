from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError

try:
    from ingest.sources_data import CATEGORIES, SOURCES
except Exception:  # pragma: no cover - fallback for a flat project layout
    from sources_data import CATEGORIES, SOURCES

try:
    from news.models import Category, Source
except Exception:  # pragma: no cover - fallback for a flat project layout
    from models import Category, Source


class Command(BaseCommand):
    help = "Kategorileri ve dogrulanmis RSS kaynaklarini yukler (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Mevcut kaynaklari silip bastan yukler (TEHLIKELI)",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="--reset ile birlikte silme islemini onayla",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            Source.objects.count()
            Category.objects.count()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError(
                "Veritabani hazir degil; python manage.py migrate calistirin."
            ) from exc

        if options.get("reset"):
            if not options["yes"]:
                raise CommandError(
                    "--reset kaynaklari ve bu kaynaklara bagli haberleri silebilir. "
                    "Onaylamak icin --reset --yes kullanin."
                )
            deleted_sources = Source.objects.count()
            deleted_cats = Category.objects.count()
            if deleted_sources or deleted_cats:
                self.stdout.write(
                    self.style.WARNING(
                        f" --reset secildi: {deleted_sources} kaynak ve {deleted_cats} kategori silinecek"
                    )
                )
                Source.objects.all().delete()
                Category.objects.all().delete()

        created_cats = 0
        for name, color, _order in CATEGORIES:
            _, was_created = Category.objects.update_or_create(
                name=name,
                defaults={
                    "slug": _slug(name),
                    "color": color,
                    "sort_order": _order,
                },
            )
            created_cats += int(was_created)

        self.stdout.write(
            self.style.SUCCESS(
                f" Kategoriler hazir: {Category.objects.count()} toplam "
                f"({created_cats} tanesi yeni)"
            )
        )

        created_srcs = 0
        updated = 0
        skipped = 0
        errors = []

        for item in SOURCES:
            if not item.get("rss_flag", True):
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f" Atlandi: {item.get('name', item.get('slug', 'Bilinmeyen'))} "
                        f"(ucretsiz RSS kaynagi degil)"
                    )
                )
                continue

            if not item.get("url"):
                errors.append(f"{item.get('slug', 'unknown')}: URL eksik")
                continue

            category = Category.objects.filter(name=item["category"]).first()
            if not category:
                errors.append(f"{item['slug']}: kategori bulunamadi ({item['category']})")
                continue

            _, was_created = Source.objects.update_or_create(
                slug=item["slug"],
                defaults={
                    "name": item["name"],
                    "kind": Source.RSS,
                    "url": item["url"],
                    "default_category": category,
                    "country_code": item["country"],
                    "lang": item["lang"],
                    "trust_score": item["trust"],
                    "is_active": True,
                },
            )
            if was_created:
                created_srcs += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f" Kaynaklar hazir: {created_srcs} yeni, {updated} guncellendi, "
                f"{skipped} atlandi, toplam {Source.objects.filter(is_active=True).count()} aktif"
            )
        )

        try:
            try:
                from ingest.services import _google_news_source
            except Exception:  # pragma: no cover - fallback for a flat project layout
                from services import _google_news_source

            _google_news_source()
            self.stdout.write(" Google News kaynagi hazir (22 sorgu bunu paylasacak)")
        except Exception as exc:  # pragma: no cover - optional external dependency
            self.stdout.write(
                self.style.WARNING(f" Google News otomasyonu atlandi: {exc}")
            )

        if errors:
            self.stdout.write(self.style.WARNING(" Dikkat edilmesi gerekenler:"))
            for err in errors:
                self.stdout.write(self.style.WARNING(f"   - {err}"))

        self.stdout.write("")
        self.stdout.write(" Kaynak listesi:")
        for source in Source.objects.filter(is_active=True).order_by("-trust_score"):
            self.stdout.write(
                f"   {source.trust_score:>3}  {source.name:<22} "
                f"[{source.country_code}/{source.lang}] "
                f"{'-> ' + source.default_category.name if source.default_category else ''}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(" Siradaki adim:  python manage.py fetch_news")
        )


def _slug(text: str) -> str:
    """Turkce karakterleri ASCII'ye cevirip slug uretir."""
    from django.utils.text import slugify

    tr_map = str.maketrans("çÇğĞıİöÖşŞüÜâÂ", "cCgGiIoOsSuUaA")
    return slugify(text.translate(tr_map))
