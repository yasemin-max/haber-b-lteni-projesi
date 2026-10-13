import hashlib

from django.db import models
from django.utils import timezone

COUNTRIES = [
    ("TR", "Turkiye"),
    ("US", "ABD"),
    ("GB", "Birlesik Krallik"),
    ("DE", "Almanya"),
    ("FR", "Fransa"),
    ("RU", "Rusya"),
    ("CN", "Cin"),
    ("JP", "Japonya"),
    ("IN", "Hindistan"),
    ("BR", "Brezilya"),
    ("IR", "Iran"),
    ("IL", "Israel"),
    ("SA", "Suudi Arabistan"),
    ("UA", "Ukrayna"),
    ("IT", "Italya"),
    ("EG", "Misir"),
    ("QA", "Katar"),
    ("GR", "Yunanistan"),
    ("SG", "Singapur"),
]


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    color = models.CharField(max_length=16, default="#6b7280")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    in_playout = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Kategoriler"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Source(models.Model):
    API = "api"
    RSS = "rss"
    KIND_CHOICES = [(API, "API"), (RSS, "RSS")]

    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=200)
    url = models.URLField(max_length=500)
    lang = models.CharField(max_length=10, default="tr")
    country_code = models.CharField(max_length=2, choices=COUNTRIES, default="TR", db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=RSS)
    default_category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="sources")
    is_active = models.BooleanField(default=True)
    trust_score = models.IntegerField(default=0)
    api_key_env = models.CharField(max_length=200, blank=True, default="")
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    total_articles = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Kaynaklar"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Translation(models.Model):
    source_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Orijinal metnin ozeti",
    )
    source_text = models.TextField(help_text="Cevrilen orijinal metin")
    target_text = models.TextField(help_text="Turkce ceviri")
    source_lang = models.CharField(max_length=10, default="en")
    target_lang = models.CharField(max_length=10, default="tr")
    quality = models.FloatField(default=100.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Ceviriler"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.source_lang}->{self.target_lang}: {self.source_text[:50]}"

    @staticmethod
    def make_hash(text: str) -> str:
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Article(models.Model):
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True, default="")
    url = models.URLField(max_length=500, db_index=True)
    image_url = models.URLField(max_length=500, blank=True, default="")
    video_url = models.URLField(max_length=500, blank=True, default="")
    video_checked_at = models.DateTimeField(null=True, blank=True)
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="articles")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="articles")
    country_code = models.CharField(max_length=2, choices=COUNTRIES, default="TR", db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(default=timezone.now)
    language = models.CharField(max_length=10, default="tr")
    is_breaking = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Haberler"
        ordering = ["-published_at", "-ingested_at"]

    def __str__(self):
        return self.title[:80]

    @property
    def title_key(self) -> str:
        return " ".join(self.title.casefold().split())


class IngestRun(models.Model):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    STATUS_CHOICES = [
        (RUNNING, "Devam Ediyor"),
        (OK, "Basarili"),
        (ERROR, "Hata"),
    ]

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="ingest_runs",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    fetched_count = models.IntegerField(default=0, help_text="RSS'ten gelen ham kayit")
    new_count = models.IntegerField(default=0, help_text="Veritabanina eklenen")
    translated_count = models.IntegerField(default=0, help_text="Turkceye cevrilen")
    skipped_count = models.IntegerField(default=0, help_text="Mukerrer oldugu icin elenen")
    error_message = models.TextField(blank=True)
    duration_ms = models.IntegerField(default=0, help_text="Islem suresi (milisaniye)")

    class Meta:
        app_label = "news"
        verbose_name_plural = "Cekme Kayitlari"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.source.name} - {self.get_status_display()} ({self.new_count} yeni)"

    @property
    def is_success(self) -> bool:
        return self.status == self.OK


class Anchor(models.Model):
    FEMALE = "female"
    MALE = "male"
    GENDER_CHOICES = [
        (FEMALE, "Kadin"),
        (MALE, "Erkek"),
    ]

    name = models.CharField(max_length=80, help_text="Ekranda gorunecek ad")
    country_code = models.CharField(
        max_length=2, default="TR", db_index=True, help_text="ISO 3166-1 alpha-2"
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default=FEMALE)
    image_seed = models.IntegerField(default=42)
    image_path = models.CharField(
        max_length=300, blank=True, help_text="Uretilen gorselin sunucudaki yolu"
    )
    image_prompt = models.TextField(blank=True)
    image_width = models.IntegerField(default=768)
    image_height = models.IntegerField(default=1152)
    voice = models.CharField(
        max_length=40, default="tr-TR-EmelNeural", help_text="Edge TTS ses adi"
    )
    voice_pitch = models.CharField(max_length=10, default="+0Hz")
    voice_rate = models.CharField(max_length=10, default="+0%")
    is_main = models.BooleanField(
        default=False,
        help_text="Ana bultende kullanilan spiker (sadece 1 tane)",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Spikerler"
        ordering = ["-is_main", "name"]

    def __str__(self):
        flag = " [ANA]" if self.is_main else ""
        return f"{self.name} ({self.country_code}){flag}"

    @property
    def country_name(self) -> str:
        from prompts import COUNTRY_NAMES

        return COUNTRY_NAMES.get(self.country_code, self.country_code)


# ---------------------------------------------------------------------------
# BULTEN (PLAYOUT)
# ---------------------------------------------------------------------------
class Playout(models.Model):
    """Tek bir haber bulteni.

    Gercek yayincilerde buna " rundown " denir: hangi haber, hangi sirayla,
    kim okuyacak. Biz de ayni seyi veritabaninda tutuyoruz.
    """

    DRAFT = "draft"
    BUILDING = "building"   # ses/gorsel uretiliyor
    READY = "ready"         # hazir, yayina alinabilir
    ON_AIR = "on_air"       # su an yayinda
    ERROR = "error"

    STATUS_CHOICES = [
        (DRAFT, "Taslak"),
        (BUILDING, "Uretiliyor"),
        (READY, "Hazir"),
        (ON_AIR, "Yayinda"),
        (ERROR, "Hata"),
    ]

    title = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)

    # Bu bultende kac haber var, toplam kac saniye suruyor
    item_count = models.IntegerField(default=0)
    total_seconds = models.IntegerField(default=0)
    word_count = models.IntegerField(default=0)

    # Hangi kategori sirasina gore kuruldu (raporlama icin)
    categories_used = models.CharField(
        max_length=300, blank=True, help_text="Virgulle ayrilmis kategori listesi"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    built_at = models.DateTimeField(null=True, blank=True)
    # Dosya yollari
    manifest_path = models.CharField(max_length=400, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Bultenler"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or 'Bulten'} - {self.get_status_display()} ({self.item_count} kalem)"

    def recalculate(self) -> None:
        """Bultenin toplam suresini ve kalem sayisini yeniden hesaplar."""
        items = self.items.all()
        self.item_count = items.count()
        self.total_seconds = int(round(sum(i.duration_seconds or 0 for i in items)))
        self.word_count = sum(i.word_count for i in items)
        self.save(update_fields=["item_count", "total_seconds", "word_count"])

    @property
    def duration_display(self) -> str:
        """03:45 gibi okunabilir sure."""
        minutes, seconds = divmod(int(round(self.total_seconds or 0)), 60)
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def is_playable(self) -> bool:
        return self.status == self.READY and self.total_seconds > 0


# ---------------------------------------------------------------------------
# BULTEN KALEMI
# ---------------------------------------------------------------------------
class PlayoutItem(models.Model):
    """Bultendeki TEK bir sesli/gorseli parca.

    Cesitleri:
      opening   -> "Merhaba, ben Türkiye Spikeri..."
      transition-> "Simdi ekonomi haberleri"
      story     -> bir haberin okunusu
      breaking  -> son dakika manşeti
      commercial-> sponsor / arasi
      closing   -> "Bizi dinlediginiz icin tesekkur ederiz"

    Her kalem ayri bir ses dosyasi (mp3) ve ayri bir altyazi (srt) uretir.
    Video servis bunlari sirayla birlestirir.
    """

    OPENING = "opening"
    HEADLINE = "headline"        # manset gecisi
    TRANSITION = "transition"    # blok gecisi
    STORY = "story"              # haber
    BREAKING = "breaking"        # son dakika
    COMMERCIAL = "commercial"
    CLOSING = "closing"

    KIND_CHOICES = [
        (OPENING, "Acilis"),
        (HEADLINE, "Manset Gecisi"),
        (TRANSITION, "Blok Gecisi"),
        (STORY, "Haber"),
        (BREAKING, "Son Dakika"),
        (COMMERCIAL, "Sponsor"),
        (CLOSING, "Kapanis"),
    ]

    playout = models.ForeignKey(
        Playout, on_delete=models.CASCADE, related_name="items"
    )
    # Sira: 1, 2, 3... Sirayla oynatilir
    order = models.IntegerField(default=0)

    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=STORY)

    # Haber caliyorsa baglanir. Acilis/kapanis'te None kalir.
    article = models.ForeignKey(
        Article,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="playout_items",
    )

    
    text = models.TextField(help_text="Spikerin okuyacagi metin")
    word_count = models.IntegerField(default=0)

   
    anchor = models.ForeignKey(
        Anchor, on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    
    country_code = models.CharField(max_length=2, default="TR")
    category_name = models.CharField(max_length=60, blank=True)

    
    title_text = models.CharField(
        max_length=300, blank=True, help_text="Ekranda gosterilecek baslik"
    )
    source_text = models.CharField(
        max_length=120, blank=True, help_text="Ekranda 'Kaynak: Reuters'"
    )
    ticker_text = models.CharField(
        max_length=300, blank=True, help_text="Ust bandaki kayan yazi"
    )

    
    audio_path = models.CharField(max_length=400, blank=True)
    subtitle_path = models.CharField(max_length=400, blank=True)
    duration_seconds = models.FloatField(default=0.0)
    audio_error = models.TextField(blank=True)

    class Meta:
        app_label = "news"
        verbose_name_plural = "Bulten Kalemleri"
        
        ordering = ["playout_id", "order"]

    def __str__(self):
        return f"{self.order:02d}. [{self.get_kind_display()}] {self.text[:60]}"

   
    def estimate_duration(self, wpm: int = 150) -> float:
        """Metnin okunma suresini tahmin eder (ses uretilmeden once).

        150 kelime/dakika = spikerlerin gercek okuma hizi.
        45 kelime -> 18 saniye.

        Neden tahmin? FFmpeg video uretirken sesin suresini bilmemiz gerekiyor
        (video, ses kadar uzun olmali). Once tahmin edip sonra
        sesi uretip gercek sureyi duzeltiyoruz.
        """
        words = len(self.text.split())
        return max(2.0, (words / wpm) * 60)

    def save(self, *args, **kwargs):
        self.word_count = len(self.text.split())
        super().save(*args, **kwargs)
