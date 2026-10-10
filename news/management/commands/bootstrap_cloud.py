from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from news.models import Anchor


class Command(BaseCommand):
    help = "Bulut dağıtımı için ana spiker kaydını güvenli/idempotent biçimde hazırlar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--generate-image",
            action="store_true",
            help="Spiker görselini uzaktaki görsel servisiyle üret.",
        )

    def handle(self, *args, **options):
        config = getattr(settings, "HABER_AJANSI", {})
        name = str(config.get("ana_spiker", "")).strip()
        if not name:
            raise CommandError("HABER_AJANSI.ana_spiker ayarı boş olamaz.")

        anchor = (
            Anchor.objects.filter(country_code="TR", name__iexact=name).first()
            or Anchor.objects.filter(is_main=True).first()
        )
        if anchor is None:
            anchor = Anchor(name=name, country_code="TR")

        Anchor.objects.exclude(pk=anchor.pk).filter(is_main=True).update(is_main=False)
        anchor.name = name
        anchor.country_code = "TR"
        anchor.gender = Anchor.FEMALE
        anchor.image_seed = int(config.get("spiker_seed", 42))
        anchor.image_width = int(config.get("spiker_genislik", 768))
        anchor.image_height = int(config.get("spiker_yukseklik", 1152))
        anchor.voice = str(config.get("ana_ses", "tr-TR-EmelNeural"))
        anchor.is_active = True
        anchor.is_main = True
        anchor.save()

        image_state = "ilk bülten üretiminde oluşturulacak"
        if options["generate_image"]:
            try:
                import anchor as anchor_service
            except ImportError as exc:
                raise CommandError("Görsel üretim bağımlılıkları yüklenmemiş.") from exc
            result = anchor_service.generate_anchor_image(anchor)
            if not result.get("ok"):
                raise CommandError(
                    f"Ana spiker kaydı hazır, görsel oluşturulamadı: "
                    f"{result.get('error') or 'bilinmeyen hata'}"
                )
            image_state = "hazır"

        self.stdout.write(
            self.style.SUCCESS(
                f"Ana spiker hazır: {anchor.name} (id={anchor.pk}, "
                f"görsel={image_state})"
            )
        )
