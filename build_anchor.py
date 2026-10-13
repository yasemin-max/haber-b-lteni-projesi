from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_django() -> None:
    """Klasik Django settings'i her calisma modunda dogru yukler."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.conf import settings
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Django kurulumu eksik. Sanal ortam aktif mi?") from exc
    if not settings.configured:
        import django

        django.setup()


_configure_django()

from django.core.management.base import BaseCommand, CommandError

from news.models import Anchor


DEFAULT_SEEDS = {
    "TR": 42,
    "DE": 77,
    "KR": 101,
    "US": 55,
    "RU": 88,
    "FR": 63,
    "GB": 71,
    "CN": 93,
    "IR": 29,
    "IL": 37,
}

DEFAULT_VOICES = {
    "TR": "tr-TR-EmelNeural",
    "DE": "de-DE-KatjaNeural",
    "KR": "ko-KR-SunHiNeural",
    "US": "en-US-AriaNeural",
    "RU": "ru-RU-SvetlanaNeural",
    "FR": "fr-FR-DeniseNeural",
    "GB": "en-GB-SoniaNeural",
    "CN": "zh-CN-XiaoxiaoNeural",
    "IR": "fa-IR-DilaraNeural",
    "IL": "he-IL-HilaNeural",
}


class Command(BaseCommand):
    help = "Spiker kaydi olusturur ve yapay zeka gorselini uretir."

    def add_arguments(self, parser):
        parser.add_argument("--country", required=True, help="ISO kod: TR, DE, KR, US...")
        parser.add_argument("--name", required=True, help="Ekranda gorunecek ad")
        parser.add_argument(
            "--gender", default="female", choices=["female", "male"], help="Cinsiyet"
        )
        parser.add_argument("--main", action="store_true", help="Bu kisi ANA spiker olsun")
        parser.add_argument("--seed", type=int, default=None, help="Yuz tohumu")
        parser.add_argument("--voice", default=None, help="Edge TTS sesi (tr-TR-EmelNeural)")
        parser.add_argument("--force", action="store_true", help="Gorseli yeniden uret")
        parser.add_argument(
            "--no-image", action="store_true", help="Gorsel indirme, sadece kayit olustur"
        )

    def handle(self, *args, **options):
        country = (options["country"] or "").strip().upper()
        name = " ".join(str(options["name"] or "").split())
        gender = options["gender"]
        is_main = bool(options["main"])

        if not country or len(country) != 2 or not country.isalpha():
            raise CommandError(f"Ulke kodu 2 harf olmalidir. '{country or options['country']}' yazdin.")

        if not name:
            raise CommandError("Spiker adi bos olamaz.")
        if len(name) > 80:
            raise CommandError("Spiker adi 80 karakterden uzun olamaz.")
        if re.search(r"[<>\\/\x00]", name):
            raise CommandError("Spiker adi gecersiz karakter iceremez.")

        if gender not in dict(Anchor.GENDER_CHOICES):
            raise CommandError(f"Gecersiz cinsiyet: {gender}")

        if options["seed"] is not None and options["seed"] < 0:
            raise CommandError("Seed negatif olamaz.")
        if options["seed"] is not None and options["seed"] > 1000000:
            raise CommandError("Seed cok buyuk; 1.000.000'i gecemez.")

        anchor_service = None
        if not options["no_image"]:
            try:
                import anchor as anchor_service
            except ImportError as exc:
                raise CommandError(
                    "Spiker gorseli icin gereken bagimliliklar yuklu degil."
                ) from exc
            if not hasattr(anchor_service, "generate_anchor_image"):
                raise CommandError("anchor.generate_anchor_image() bulunamadi.")

        try:
            seed = options["seed"] if options["seed"] is not None else DEFAULT_SEEDS.get(country, 42)
            voice = (options["voice"] or DEFAULT_VOICES.get(country, "tr-TR-EmelNeural")).strip()

            existing = Anchor.objects.filter(country_code=country, name__iexact=name).first()
            if existing is None:
                existing = Anchor.objects.filter(name__iexact=name).first()

            if existing:
                existing.name = name
                existing.country_code = country
                existing.gender = gender
                existing.image_seed = seed
                existing.voice = voice
                existing.is_active = True
                existing.is_main = is_main or existing.is_main
                existing.save()
                anchor = existing
                self.stdout.write(self.style.WARNING(f"Mevcut spiker guncellendi: {anchor}"))
            else:
                anchor = Anchor.objects.create(
                    name=name,
                    country_code=country,
                    gender=gender,
                    image_seed=seed,
                    voice=voice,
                    is_main=is_main,
                )
                self.stdout.write(self.style.SUCCESS(f"Spiker olusturuldu: {anchor} (seed={seed})"))

            if is_main or anchor.is_main:
                others = Anchor.objects.filter(is_main=True).exclude(pk=anchor.pk)
                if others.exists():
                    self.stdout.write(
                        self.style.WARNING(
                            "Uyarı: birden fazla ana spiker tespit edildi. Eski ana spiker kapatiliyor: "
                            + ", ".join(str(item) for item in others)
                        )
                    )
                    others.update(is_main=False)
                    anchor.is_main = True
                    anchor.save(update_fields=["is_main"])

            if options["no_image"]:
                self.stdout.write("--no-image verildi, gorsel atlandi.")
                return

            self.stdout.write("Gorsel uretiliyor...")
            result = anchor_service.generate_anchor_image(anchor, force=options["force"])

            if not isinstance(result, dict):
                raise CommandError("Gorsel uretim fonksiyonundan gecersiz cevap geldi.")

            if not result.get("ok"):
                raise CommandError(
                    "Gorsel uretilemedi: " + str(result.get("error") or "bilinmeyen hata")
                    + "\nYine de kayit olustu. Internet baglantisini kontrol edip tekrar dene:"
                    + f"\n  python manage.py build_anchor --country {country} --name \"{name}\" --force"
                )

            if result.get("cached"):
                self.stdout.write(f"Gorsel zaten vardi (yeniden uretilmedi): {result['path']}")
            else:
                self.stdout.write(self.style.SUCCESS(f"Gorsel indirildi: {result['path']}"))
                if result.get("prompt"):
                    self.stdout.write(self.style.NOTICE("\nKullanilan prompt:\n" + result["prompt"]))

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSpiker hazir: {anchor.name} | {anchor.voice} | "
                    f"gorsel: {anchor.image_path or result.get('path', '')}"
                )
            )
        except Exception as exc:  # pragma: no cover - keep command feedback friendly
            if isinstance(exc, CommandError):
                raise
            raise CommandError(f"Spiker isleminde beklenmeyen hata: {exc}") from exc


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import django

    django.setup()
    from django.core.management import execute_from_command_line

    execute_from_command_line([str(ROOT / "manage.py"), "build_anchor", *sys.argv[1:]])
