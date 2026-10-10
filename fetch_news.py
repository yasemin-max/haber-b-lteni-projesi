import time

from django.core.management.base import BaseCommand, CommandError
from django.db.utils import OperationalError, ProgrammingError

from ingest.models import IngestRun, Translation
from news.models import Article, Source


class Command(BaseCommand):
    help = "Tum RSS kaynaklarindan haber ceker, Turkceye cevirir ve kaydeder."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=30, help="Kaynak basina maks. haber (1-100)")
        parser.add_argument("--source", type=str, default="", help="Tek kaynagi cek (slug)")
        parser.add_argument("--breaking-only", action="store_true", help="Sadece son dakika")

    def handle(self, *args, **options):
        started = time.monotonic()
        limit = options["limit"]
        if not 1 <= limit <= 100:
            raise CommandError("--limit 1 ile 100 arasinda olmalidir.")

        try:
            Source.objects.count()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError(
                "Veritabani hazir degil; python manage.py migrate calistirin."
            ) from exc

        if options["source"]:
            source = Source.objects.filter(slug=options["source"]).first()
            if not source:
                raise CommandError(f"Kaynak bulunamadi: {options['source']}")

            try:
                from ingest.services import ingest_source
            except ImportError as exc:
                raise CommandError("Haber cekme bagimliliklari yuklu degil.") from exc

            self.stdout.write(f"Cekiliyor: {source.name} ...")
            run = ingest_source(source, limit=limit)
            self._print_run(run)
            return

        
        self.stdout.write(self.style.HTTP_INFO(
            f"\n HABER TOPLAMA BASLADI "
            f"(kaynak basina en fazla {limit} haber)\n"
            f" Ceviri kotasini korumak icin --limit 10 ile baslamak mantikli.\n"
        ))

        if options["breaking_only"]:
            from ingest.services import (
                BREAKING_QUERIES,
                _ingest_google_news,
                build_google_news_url,
            )

            for name, keyword, lang, country, cat in BREAKING_QUERIES:
                url = build_google_news_url(
                    keyword, lang, country, f"{country}:{lang.split('-')[0]}", hours=1
                )
                run = _ingest_google_news(name, url, country, cat, is_breaking=True, limit=limit)
                if run:
                    self._print_run(run, name)
            return

       
        try:
            from ingest.services import ingest_all
        except ImportError as exc:
            raise CommandError("Haber cekme bagimliliklari yuklu degil.") from exc
        stats = ingest_all(limit_per_source=limit)

        elapsed = time.monotonic() - started
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(" TOPLAMA TAMAMLANDI"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f" Basarili kaynak : {stats['ok']}")
        self.stdout.write(f" Hata veren kaynak: {stats['error']}")
        self.stdout.write(f" Yeni haber       : {stats['total_new']}")
        self.stdout.write(f"   (son dakika)   : {stats['breaking']}")
        self.stdout.write(f" Ceviri kullanimi : {Translation.objects.count()} benzersiz metin")
        self.stdout.write(f" Toplam sure      : {elapsed:.1f} saniye")

        self._print_breakdown()

   
    def _print_run(self, run: IngestRun, label: str = "") -> None:
        """Tek bir cekme isleminin sonucunu yazdirir."""
        name = label or run.source.name
        if run.status == IngestRun.OK:
            style = self.style.SUCCESS
            detail = (
                f"{run.fetched_count} cekildi -> {run.new_count} yeni "
                f"({run.translated_count} cevrildi, {run.skipped_count} elendi)"
            )
        else:
            style = self.style.ERROR
            detail = f"HATA: {run.error_message[:120]}"

        self.stdout.write(style(f"  {name:<26} {detail}"))

    
    def _print_breakdown(self) -> None:
        """Haberlerin ulke ve kategori dagilimini gosterir.

        Bu cikti sunum icin degerli: "20 ulkeden 340 haber, 18 kategoride
        dagildi" diyebilirsin.
        """
        from django.db.models import Count

        total = Article.objects.count()
        if not total:
            self.stdout.write(self.style.WARNING(" Veritabaninda haber yok."))
            return

        self.stdout.write("")
        self.stdout.write(" ULKELERE GORE:")
        rows = (
            Article.objects.values("country_code")
            .annotate(adet=Count("id"))
            .order_by("-adet")[:12]
        )
        for row in rows:
            self.stdout.write(f"   {row['country_code']}  {row['adet']:>4} haber")

        self.stdout.write("")
        self.stdout.write(" KATEGORILERE GORE:")
        rows = (
            Article.objects.values("category__name")
            .annotate(adet=Count("id"))
            .order_by("-adet")[:12]
        )
        for row in rows:
            self.stdout.write(f"   {(row['category__name'] or '-'):<18} {row['adet']:>4} haber")

        breaking = Article.objects.filter(is_breaking=True).count()
        self.stdout.write("")
        self.stdout.write(f" SON DAKIKA ISARETLI: {breaking} haber")
