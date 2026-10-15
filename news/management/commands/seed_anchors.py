"""Tum ulke spikerlerini (gorselleriyle) hazir eder.

Amac: bulut/bos veritabani senaryosunda (ör. Render) tek komutla 20 ulke
spikerini olusturmak ve Pollinations ile gorsellerini indirmek. Komut
idempotent'tir: var olan kaydi/kayitli gorseli tekrar uretmez. Boylece
her acilista hizli calisir.

Kullanim:
    python manage.py seed_anchors
    python manage.py seed_anchors --no-image
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from news.models import Anchor


COUNTRY_SEEDS = {
    "TR": 42,
    "US": 55,
    "GB": 71,
    "DE": 77,
    "FR": 63,
    "RU": 88,
    "CN": 42,
    "JP": 42,
    "IN": 66,
    "BR": 42,
    "IR": 29,
    "SA": 42,
    "EG": 42,
    "QA": 42,
    "IT": 42,
    "KR": 42,
    "UA": 80,
    "SG": 69,
}

# Gorseli uretilecek ulkeler. TR da dahil; TR ana spikerdir.
COUNTRIES = list(COUNTRY_SEEDS.keys())


class Command(BaseCommand):
    help = "Tum ulke spikerlerini olusturur ve gorsellerini indirir (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-image",
            action="store_true",
            help="Gorsel indirme; yalnizca spiker kayitlarini olustur.",
        )

    def handle(self, *args, **options):
        try:
            from prompts import COUNTRY_NAMES, voice_for_country
        except Exception as exc:  # pragma: no cover
            self.stderr.write(self.style.ERROR(f"prompts modulu yuklenemedi: {exc}"))
            return

        anchor_service = None
        if not options["no_image"]:
            try:
                import anchor as anchor_service
            except Exception as exc:  # pragma: no cover
                self.stderr.write(
                    self.style.WARNING(
                        f"Gorsel modulu yuklenemedi, gorselsiz devam: {exc}"
                    )
                )
                anchor_service = None

        created = 0
        updated = 0
        images = 0

        for code in COUNTRIES:
            name = f"{COUNTRY_NAMES.get(code, code)} Spikeri"
            voice = voice_for_country(code, "female")
            seed = COUNTRY_SEEDS.get(code, 42)
            is_main = code == "TR"

            anchor = Anchor.objects.filter(country_code=code).order_by("-is_main", "pk").first()
            if anchor is None:
                anchor = Anchor(name=name, country_code=code)
                created += 1
            else:
                updated += 1

            # Var olan gorsel dosyasi kendi seed'ini tasir; yuzu degistirmemek
            # icin mevcut gorselin seed'ini koruruz (ornek: spikerler/tr_42.jpg).
            if anchor.image_path:
                match = re.search(r"_(\d+)\.[A-Za-z0-9]+$", anchor.image_path)
                if match and (Path(settings.MEDIA_ROOT) / anchor.image_path).exists():
                    seed = int(match.group(1))

            anchor.name = name
            anchor.country_code = code
            anchor.voice = voice
            # Tum spikerler dogal (normal) hizda ve notr perdede konsun.
            anchor.voice_pitch = "+0Hz"
            anchor.voice_rate = "+0%"
            anchor.image_seed = seed
            anchor.is_active = True
            anchor.is_main = is_main
            anchor.save()

            if is_main:
                Anchor.objects.exclude(pk=anchor.pk).filter(is_main=True).update(is_main=False)

            if anchor_service is not None:
                try:
                    result = anchor_service.generate_anchor_image(anchor)
                    if result.get("ok"):
                        images += 1
                except Exception as exc:  # pragma: no cover
                    self.stderr.write(
                        self.style.WARNING(f"  {code}: gorsel uretilemedi: {exc}")
                    )

            self.stdout.write(
                self.style.SUCCESS(f"  {code}: {name} ({voice}) gorsel-hazir={bool(anchor.image_path)}")
            )

        # Listede olmayan ulke spikerlerini kaldir; boylece yalnizca izinli
        # ulkelerin spikerleri kalir.
        removed = Anchor.objects.exclude(country_code__in=COUNTRIES).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Spikerler hazir. yeni={created} guncel={updated} gorsel={images} "
                f"silinen={removed[0]}"
            )
        )
