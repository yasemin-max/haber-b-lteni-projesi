import re
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from news.models import Playout, PlayoutItem


class Command(BaseCommand):
    help = "Süresi dolmuş bülten ses ve video dosyalarını güvenli biçimde temizler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=int(getattr(settings, "MEDIA_RETENTION_DAYS", 30)),
            help="Medyanın saklanacağı gün sayısı (1-3650).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if not 1 <= days <= 3650:
            raise CommandError("--days 1 ile 3650 arasinda olmalidir.")

        media_root = Path(settings.MEDIA_ROOT).resolve()
        bulletin_root = media_root / "bultenler"
        resolved_bulletin_root = bulletin_root.resolve()
        try:
            resolved_bulletin_root.relative_to(media_root)
        except ValueError as exc:
            raise CommandError("Bülten medya dizini MEDIA_ROOT dışında olamaz.") from exc
        if bulletin_root.is_symlink():
            raise CommandError("Bülten medya dizini sembolik bağlantı olamaz.")
        if not bulletin_root.exists():
            self.stdout.write("Temizlenecek bülten medyası bulunamadı.")
            return

        cutoff = timezone.now() - timedelta(days=days)
        expired = Playout.objects.filter(created_at__lt=cutoff).exclude(
            status__in=(Playout.BUILDING, Playout.ON_AIR)
        )
        removed = 0
        for playout in expired.iterator():
            directory_name = f"playout_{playout.pk}"
            if not re.fullmatch(r"playout_[1-9][0-9]*", directory_name):
                raise CommandError(f"Güvenli olmayan medya dizini: {directory_name}")

            directory = bulletin_root / directory_name
            resolved_directory = directory.resolve()
            try:
                resolved_directory.relative_to(bulletin_root)
            except ValueError as exc:
                raise CommandError(
                    f"Medya dizini bülten arşivinin dışında: {directory_name}"
                ) from exc
            if directory.is_symlink():
                raise CommandError(f"Medya dizini sembolik bağlantı olamaz: {directory_name}")

            if directory.is_dir():
                shutil.rmtree(directory)
                removed += 1

            with transaction.atomic():
                PlayoutItem.objects.filter(playout=playout).update(
                    audio_path="",
                    subtitle_path="",
                )
                if playout.manifest_path:
                    playout.manifest_path = ""
                    playout.save(update_fields=["manifest_path"])

        self.stdout.write(
            self.style.SUCCESS(
                f"{removed} eski medya klasörü temizlendi; "
                f"{days} günden eski bülten kayıtları korundu."
            )
        )
