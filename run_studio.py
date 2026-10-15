from __future__ import annotations

import time
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from news.locks import writer_lock
from news.models import Article, Playout
from script import _select_unplayed_articles


class Command(BaseCommand):
    help = "Haber cek -> bulten uret -> bekle dongusunu 7/24 calistirir."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=5,
            help="Dongu arasindaki bekleme suresi (dakika). Varsayilan 5.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Tek seferlik calistir ve cik (cron/zamanlayici icin).",
        )
        parser.add_argument(
            "--headlines",
            type=int,
            default=5,
            help="Geriye donuk uyumluluk; bekleyen tum haberler bultene eklenir.",
        )
        parser.add_argument(
            "--max-age",
            type=int,
            default=60,
            help="Yalnizca bu dakika icinde yayimlanan haberleri kullan "
                 "(0 = sinir yok). Varsayilan 60.",
        )
        parser.add_argument(
            "--max-errors",
            type=int,
            default=3,
            help="Ardisik hata sayisi. Asilirsa dongu durur. Varsayilan 3.",
        )
        parser.add_argument(
            "--build-videos",
            action="store_true",
            help="Yalnizca ses ve altyazi uret; video olusturma.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        interval = options["interval"]
        max_errors = options["max_errors"]
        if interval < 1:
            raise CommandError("--interval en az 1 dakika olmalidir.")
        if max_errors < 1:
            raise CommandError("--max-errors en az 1 olmalidir.")
        if options["max_age"] < 0:
            raise CommandError("--max-age negatif olamaz.")

        errors = 0

        self._log("Yayin dongusu basliyor. Durdurmak icin Ctrl+C.")
        try:
            call_command("bootstrap_cloud", "--generate-image", verbosity=0)
        except CommandError as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"Ana spiker gorseli baslangicta hazirlanamadi; "
                    f"bulten uretiminde tekrar denenecek: {exc}"
                )
            )

        while True:
            started = timezone.now()
            started_monotonic = time.monotonic()
            last_error = ""
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"=== {started:%d.%m.%Y %H:%M:%S} | dongu basladi ==="
                )
            )

            try:
                ok, message = self._cycle(options)
                if ok:
                    errors = 0
                    self.stdout.write(self.style.SUCCESS(f"  {message}"))
                else:
                    errors += 1
                    last_error = message
                    self.stderr.write(self.style.WARNING(f"  {message}"))

            except KeyboardInterrupt:
                # Ctrl+C normaldir; kullanici donguyu bitirmek istiyor.
                # Bunu yakalamazsak Python traceback basar ve operator
                # "program coktu" sanir.
                self.stdout.write("")
                self._log("Durduruldu. Tesekkurler.")
                return

            except Exception as exc:  # noqa: BLE001
                errors += 1
                last_error = f"{type(exc).__name__}: {exc}"
                self.stderr.write(
                    self.style.ERROR(f"  Beklenmeyen hata: {last_error}")
                )

            if options["once"] and errors:
                raise CommandError(f"Tek seferlik calistirma basarisiz: {last_error}")

            if errors >= max_errors:
                raise CommandError(
                    f"{errors} kez ardisik hata. Dongu durduruldu.\n"
                    f"Son hata: {last_error}"
                )

            if options["once"]:
                self._log("Tek seferlik calistirma bitti.")
                return

            
            elapsed = (time.monotonic() - started_monotonic) / 60
            wait = max(1.0, interval - elapsed)
            self._log(f"  Sonraki dongu: {wait:.0f} dk sonra ({started:%d.%m.%Y %H:%M:%S} baslangic)")

            try:
                # Parcali uyku: Ctrl+C'ye hemen tepki ver.
                for _ in range(int(wait * 2)):
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self._log("Durduruldu. Tesekkurler.")
                return

    
    def _cycle(self, options) -> tuple[bool, str]:
        """Bir turda: haber cek, sonra bulteni guncelle.

        Yazma isleri (temizlik + cekme + bulten) tek bir kilit altinda
        calisir. Boylece gunicorn istekleri ya da baska bir surec ayni
        anda veritabani YAZMAZ (SQLite "database is locked" onlenir).

        Returns:
            (basarili_mi, aciklama)
        """
        with writer_lock(timeout=120) as ok:
            if not ok:
                return False, "baska bir yazma islemi suruyor, tur atlandi"
            return self._cycle_locked(options)

    def _cycle_locked(self, options) -> tuple[bool, str]:
        call_command("cleanup_media", verbosity=0)

        # ---------- 1) HABER CEK ----------
        self.stdout.write("  [1/2] haberler cekiliyor...")
        try:
            call_command("fetch_news", verbosity=0)
            total = Article.objects.count()
            self.stdout.write(f"        veritabaninda {total} haber")
        except Exception as exc:  # noqa: BLE001
            return False, f"haber cekilemedi: {exc}"

        # ---------- 2) UYGUNLUK KONTROLU ----------
        
        window = Q()
        if options["max_age"] > 0:
            cut = timezone.now() - timedelta(minutes=options["max_age"])
            window &= Q(published_at__gte=cut)

        fresh = Article.objects.filter(window).count()
        pending_articles = _select_unplayed_articles()
        if fresh == 0 and not pending_articles:
            return True, f"yeni haber yok ({options['max_age']} dk), tur atlandi"

        # ---------- 3) BULTEN GUNCELLE ----------
        self.stdout.write(
            f"  [2/2] {len(pending_articles)} bekleyen haber, "
            "kesintisiz yayin bolumu hazirlaniyor..."
        )

       
        self.stdout.write("        yeni bulten kuruluyor")
        args = []

        if options["build_videos"]:
            args.append("--script-only")

        call_command("make_bulletin", *args, verbosity=0)

        playout = Playout.objects.order_by("-id").first()
        if playout is None:
            return False, "bulten komutu tamamlandi ancak veritabaninda bulten olusmadi"
        if playout.status != Playout.READY:
            return False, (
                f"bulten #{playout.pk} hazir degil "
                f"(durum: {playout.get_status_display()}): "
                f"{playout.error_message[:300]}"
            )
        return True, (
            f"bulten #{playout.pk} hazir: {playout.item_count} kalem, "
            f"{playout.duration_display}"
        )

   
    def _log(self, text: str) -> None:
        """Kendi logumuz. Neden?

        Django `self.stdout.write` sadece terminale yazar ve
        dosyaya DUSMEZ. 7/24 calisan bir sistemde "gece 03:00'te ne
        oldu" sorusunun cevabi dosyada olmali. Burada media/logs/
        altina ekliyoruz (dosya acmadan once klasoru yarat).
        """
        self.stdout.write(self.style.SUCCESS(f"[{timezone.localtime():%H:%M:%S}] {text}"))
        try:
            from pathlib import Path

            from django.conf import settings

            log_dir = Path(settings.MEDIA_ROOT) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "yayin.log", "a", encoding="utf-8") as fh:
                fh.write(f"{timezone.localtime():%d.%m.%Y %H:%M:%S} {text}\n")
        except OSError:
            # Log yazilamazsa yayini DURDURMA. Log bir yan etki;
            # asil is bulten uretmek.
            pass