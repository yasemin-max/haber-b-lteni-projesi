import asyncio
import logging
import os
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _validate_runtime_input(value, field_name: str, *, min_len: int = 1, max_len: int = 200) -> str:
    if value is None:
        raise ValueError(f"{field_name} bos olamaz.")
    text = str(value).strip()
    if len(text) < min_len:
        raise ValueError(f"{field_name} bos olamaz.")
    if len(text) > max_len:
        raise ValueError(f"{field_name} cok uzun: {max_len} karakteri gecemez.")
    if any(ch in text for ch in ("\x00", "\r", "\n")):
        raise ValueError(f"{field_name} gecersiz karakter iceriyor.")
    return text


def _load_runtime_modules():
    try:
        from news.models import Playout, PlayoutItem
    except Exception:  # pragma: no cover - flat project fallback
        try:
            from models import Playout, PlayoutItem
        except Exception as exc:  # pragma: no cover
            raise ImportError("Playout modelleri bulunamadi. Django app yapisini kontrol et.") from exc

    try:
        import anchor as anchor_service
    except Exception:  # pragma: no cover
        anchor_service = None

    try:
        import script as script_service
    except Exception:  # pragma: no cover
        script_service = None

    try:
        import video as video_service
    except Exception:  # pragma: no cover
        video_service = None

    try:
        import voice as voice_service
    except Exception:  # pragma: no cover
        voice_service = None

    return Playout, PlayoutItem, anchor_service, script_service, video_service, voice_service


logger = logging.getLogger("news.make_bulletin")


class Command(BaseCommand):
    help = "Haberlerden tam bulten videosu uretir (senaryo + ses + video + birlestirme)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--headlines",
            type=int,
            default=3,
            help="Eski surum uyumlulugu icin tutulur; tum bekleyen haberler kullanilir.",
        )
        parser.add_argument(
            "--items",
            type=int,
            default=3,
            help="Eski surum uyumlulugu icin tutulur; haber sayisini sinirlamaz.",
        )
        parser.add_argument(
            "--blocks",
            type=int,
            default=5,
            help="Eski surum uyumlulugu icin tutulur; kategori siniri uygulanmaz.",
        )
        parser.add_argument(
            "--no-country-tour",
            action="store_true",
            help="Eski surum uyumlulugu icin tutulur; tum haberler ayni akista sunulur.",
        )
        parser.add_argument("--title", default="", help="Bulten basligi")
        parser.add_argument("--playout", type=int, default=0, help="Var olan bulten ID'si")
        parser.add_argument("--dry-run", action="store_true", help="Sadece senaryoyu yazdir, dosya uretme")
        output_group = parser.add_mutually_exclusive_group()
        output_group.add_argument("--script-only", action="store_true", help="Sadece ses + altyazi uret (MP3/SRT)")
        output_group.add_argument("--video-only", action="store_true", help="Sadece video uret (ses atlanir)")
        parser.add_argument("--no-background", action="store_true", help="Kategori arka plani kullanma")
        parser.add_argument(
            "--only-stories",
            action="store_true",
            help="Sadece haber kalemlerini uret; acilis ve devam anonslarini atla.",
        )

    def handle(self, *args, **options):
        try:
            import sys

            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
        if options["dry_run"] and not options["playout"]:
            with transaction.atomic():
                self._handle_command(*args, **options)
                transaction.set_rollback(True)
            return
        self._handle_command(*args, **options)

    def _handle_command(self, *args, **options):
        if options["playout"] < 0:
            raise CommandError("--playout negatif olamaz.")

        try:
            if options.get("title") is not None:
                options["title"] = _validate_runtime_input(options["title"], "title", min_len=0, max_len=200)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        Playout, PlayoutItem, anchor_service, script_service, video_service, voice_service = _load_runtime_modules()

        try:
            from services import translate_text
        except Exception:  # pragma: no cover
            translate_text = None

        try:
            from prompts import language_for_country
        except Exception:  # pragma: no cover
            def language_for_country(country_code: str) -> str:
                return "tr"

        config = getattr(settings, "HABER_AJANSI", {})
        if not config:
            raise CommandError("HABER_AJANSI ayarlari bulunamadi. settings.py kontrol et.")

        missing = []
        if script_service is None or not hasattr(script_service, "build_bulletin"):
            missing.append("script")
        needs_video = not options["dry_run"] and not options["script_only"]
        needs_audio = not options["dry_run"] and not options["video_only"]
        if needs_video and (
            anchor_service is None or not hasattr(anchor_service, "generate_anchor_image")
        ):
            missing.append("anchor")
        if needs_video and (
            video_service is None or not hasattr(video_service, "build_anchor_shot")
        ):
            missing.append("video")
        if needs_audio and (voice_service is None or not hasattr(voice_service, "speak")):
            missing.append("voice")
        if missing:
            raise CommandError(
                "Gerekli servis modulleri eksik: " + ", ".join(missing) + ". "
                "Proje yapisini kontrol et."
            )

        if needs_video:
            if not hasattr(video_service, "check_environment"):
                raise CommandError("video.check_environment() bulunamadi.")

            report = video_service.check_environment()
            if not report.get("ffmpeg"):
                raise CommandError(
                    "FFmpeg bulunamadi. Kurulum:\n"
                    "  winget install Gyan.FFmpeg\n"
                    "Sonra terminali KAPATIP yeniden ac (PATH degissin)."
                )
            if not (report.get("drawtext") and report.get("subtitles")):
                raise CommandError(
                    "FFmpeg 'drawtext' veya 'subtitles' filtresini desteklemiyor.\n"
                    "Tam surumlu (full build) FFmpeg gerekli."
                )

        if options["playout"]:
            playout = Playout.objects.filter(pk=options["playout"]).first()
            if not playout:
                raise CommandError(f"Playout {options['playout']} bulunamadi.")
            self.stdout.write(f"Mevcut playout kullaniliyor: {playout}")
        else:
            if not hasattr(script_service, "build_bulletin"):
                raise CommandError("script.build_bulletin() bulunamadi.")
            try:
                playout = script_service.build_bulletin(title=options["title"])
            except RuntimeError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                self.style.SUCCESS(
                    f"Senaryo kuruldu: {playout} ({playout.item_count} kalem, "
                    f"tahmini {playout.duration_display})"
                )
            )

        items = list(playout.items.select_related("anchor", "article", "article__source"))

        if options["only_stories"]:
            items = [
                item for item in items if item.kind in (PlayoutItem.STORY, PlayoutItem.BREAKING)
            ]
            self.stdout.write(f"--only-stories: {len(items)} kalem secildi.")

        if not items:
            raise CommandError("Bultende uretilecek kalem yok. Once haber topla.")

        if options["dry_run"]:
            self._print_script(items)
            return

        playout.status = Playout.BUILDING
        playout.built_at = timezone.now()
        playout.error_message = ""
        playout.save(update_fields=["status", "built_at", "error_message"])

        try:
            return self._build_playout(
                playout, items, options, config, translate_text, anchor_service,
                script_service, video_service, voice_service, Playout,
            )
        except Exception as exc:  # noqa: BLE001
            playout.status = Playout.ERROR
            playout.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            playout.save(update_fields=["status", "error_message"])
            self.stderr.write(self.style.ERROR(f"Bulten uretimi coktu: {playout.error_message}"))
            raise

    def _build_playout(
        self, playout, items, options, config, translate_text,
        anchor_service, script_service, video_service, voice_service, Playout,
    ):
        out_dir = Path(settings.MEDIA_ROOT) / "bultenler" / f"playout_{playout.pk}"
        audio_dir = out_dir / "ses"
        video_dir = out_dir / "video"
        audio_dir.mkdir(parents=True, exist_ok=True)
        video_dir.mkdir(parents=True, exist_ok=True)

        segments: list[str] = []
        failures: list[str] = []

        for position, item in enumerate(items, start=1):
            self.stdout.write("")
            self.stdout.write(self.style.HTTP_INFO(f"[{position}/{len(items)}] {item}"))

            speaker = item.anchor or getattr(script_service, "_get_main_anchor", lambda: None)()
            if not speaker:
                failures.append(f"kalem {item.order}: spiker yok")
                self.stdout.write(self.style.ERROR("  spiker yok, atlandi"))
                continue

            mp3 = audio_dir / f"{item.pk:04d}.mp3"
            srt = audio_dir / f"{item.pk:04d}.srt"
            duration = float(item.duration_seconds or 0.0)

            if not options["video_only"]:
                spoken_text = item.text
                subtitle_text_tr = item.text
                speaker_lang = language_for_country(speaker.country_code)
                if speaker_lang != "tr" and translate_text:
                    translated_spoken = translate_text(item.text, "tr", speaker_lang)
                    if translated_spoken and translated_spoken.strip():
                        spoken_text = translated_spoken
                    translated_sub = translate_text(item.text, "tr", "tr")
                    if translated_sub and translated_sub.strip():
                        subtitle_text_tr = translated_sub
                else:
                    subtitle_text_tr = item.text

                speech = asyncio.run(
                    voice_service.speak(
                        spoken_text,
                        voice=speaker.voice,
                        output_mp3=mp3,
                        output_srt=srt,
                        pitch=getattr(speaker, "voice_pitch", "+0Hz"),
                        rate=getattr(speaker, "voice_rate", "+0%"),
                        subtitle_text=subtitle_text_tr,
                    )
                )

                if not speech.get("ok") and spoken_text != item.text:
                    self.stdout.write(
                        self.style.WARNING("  yerel ses basarisiz, Turkce'ye donuluyor")
                    )
                    main_anchor = getattr(script_service, "_get_main_anchor", lambda: None)()
                    fallback_voice = (
                        getattr(main_anchor, "voice", "tr-TR-EmelNeural")
                        if main_anchor
                        else "tr-TR-EmelNeural"
                    )
                    speech = asyncio.run(
                        voice_service.speak(
                            item.text,
                            voice=fallback_voice,
                            output_mp3=mp3,
                            output_srt=srt,
                            subtitle_text=subtitle_text_tr,
                        )
                    )

                if not speech.get("ok"):
                    item.audio_error = speech.get("error") or "Ses uretilemedi"
                    item.save(update_fields=["audio_error"])
                    failures.append(f"kalem {item.order}: ses - {item.audio_error}")
                    self.stdout.write(self.style.ERROR("  ses hatasi: " + item.audio_error))
                    continue
                duration = float(speech.get("duration") or duration or 0.0)
                item.audio_path = _relative(mp3)
                item.subtitle_path = _relative(srt)
                item.duration_seconds = duration
                item.audio_error = ""
                item.save(
                    update_fields=["audio_path", "subtitle_path", "duration_seconds", "audio_error"]
                )
                self.stdout.write(f"  ses: {duration:.1f} sn")

            if options["script_only"]:
                continue

            image_result = anchor_service.generate_anchor_image(speaker)
            if not isinstance(image_result, dict) or not image_result.get("ok"):
                message = image_result.get("error") if isinstance(image_result, dict) else "gorsel uretilemedi"
                failures.append(f"kalem {item.order}: spiker gorseli - {message}")
                self.stdout.write(self.style.ERROR("  gorsel hatasi: " + str(message)))
                continue

            background = None
            if not options["no_background"] and item.category_name:
                bg_result = anchor_service.generate_background_image(item.category_name)
                if isinstance(bg_result, dict) and bg_result.get("ok"):
                    background = bg_result.get("path")

            mp4 = video_dir / f"{item.pk:04d}.mp4"
            shot = video_service.build_anchor_shot(
                anchor_image=image_result["path"],
                audio=mp3,
                subtitle=srt,
                output=mp4,
                duration=duration,
                lower_third=speaker.name,
                lower_third_sub=item.category_name or "",
                ticker=item.ticker_text or f"{config['kanal_adi']}  •  CANLI YAYIN",
                headline_text=(item.title_text or item.text)[:70],
                background=background,
                credit=item.source_text or "",
            )

            if not shot.get("ok"):
                failures.append(f"kalem {item.order}: video - {shot.get('error') or 'video hatasi'}")
                self.stdout.write(self.style.ERROR("  video hatasi: " + str(shot.get("error") or "video hatasi")[:200]))
                continue

            segments.append(shot["path"])
            self.stdout.write(f"  video: {Path(shot['path']).name}")

        if not options["script_only"] and segments:
            final = out_dir / "bulten.mp4"
            merged = video_service.concat_segments(segments, final)
            if merged.get("ok"):
                playout.manifest_path = _relative(final)
                self.stdout.write("")
                self.stdout.write(self.style.SUCCESS(f"BIRLESIK VIDEO: {merged['path']}"))
            else:
                failures.append("birlestirme: " + str(merged.get("error") or "birlestirme hatasi"))
                self.stdout.write(self.style.ERROR("Birlestirme hatasi: " + str(merged.get("error") or "birlestirme hatasi")))

        playout.recalculate()
        has_some_output = bool(segments) or options["script_only"]
        playout.status = Playout.READY if has_some_output else Playout.ERROR
        playout.error_message = "\n".join(failures) if failures else ""
        playout.save(update_fields=["status", "error_message", "manifest_path"])

        self.stdout.write("")
        self.stdout.write("=" * 64)
        if failures:
            self.stdout.write(self.style.WARNING(f"{len(failures)} kalem basarisiz oldu:"))
            for line in failures[:10]:
                self.stdout.write("  - " + line[:160])
        self.stdout.write(
            self.style.SUCCESS(
                f"Bulten: {playout}  |  {playout.item_count} kalem  |  {playout.duration_display}"
            )
        )
        self.stdout.write(f"Klasor: {out_dir}")
        self.stdout.write("=" * 64)

    def _print_script(self, items):
        """Senaryoyu ekrana yazar (dry-run)."""
        self.stdout.write("=" * 64)
        self.stdout.write("BULTEN SENARYOSU (kuru calisma)")
        self.stdout.write("=" * 64)
        total = 0.0

        for item in items:
            speaker = item.anchor
            name = speaker.name if speaker else "-"
            seconds = item.estimate_duration()
            total += seconds
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"{item.order:02d}. [{item.get_kind_display()}] "
                    f"({name}, ~{seconds:.0f} sn)"
                )
            )
            if item.source_text:
                self.stdout.write(f"    {item.source_text}")
            self.stdout.write(self.style.SUCCESS(f"    {item.text}"))

        self.stdout.write("")
        self.stdout.write("=" * 64)
        self.stdout.write(f"Toplam {len(items)} kalem, tahmini {int(total)} saniye")
        self.stdout.write("=" * 64)


def _relative(path: Path) -> str:
    """Medya kokunu kaldirir: media/bultenler/... yerine bultenler/..."""
    try:
        return str(Path(path).relative_to(Path(settings.MEDIA_ROOT)))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import django

    django.setup()
    from django.core.management import execute_from_command_line

    execute_from_command_line([str(ROOT / "manage.py"), "make_bulletin", *sys.argv[1:]])
