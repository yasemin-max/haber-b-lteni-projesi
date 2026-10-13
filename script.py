import logging
import math
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from news.models import Anchor, Article, Playout, PlayoutItem
from prompts import (
    BREAKING_LEAD,
    COUNTRY_NAMES,
    OPENING_TEMPLATE,
    TICKER_TEMPLATE,
    VOICE_VARIANTS,
)
try:
    from services import translate_text
except Exception:  # pragma: no cover
    translate_text = None
from voice import clean_for_speech

logger = logging.getLogger(__name__)


SUMMARY_LIMIT = 600

# Gercek bir ana haber bultenine yaklasmak icin daha uzun bir akis hedeflenir.
MAX_ITEMS = 60

TR_QUOTA = 20

BREAKING_COUNT = 3

WORLD_SKIP_CATEGORIES = {"Spor"}

# Bir haberin gercekten "son dakika" sayilmasi icin iceriginde aranan
# ifadeler. "Cilt lekeleri..." gibi is_breaking=True yazilmis ama haber
# degeri olmayan basliklar bu esige takilir ve flas olarak sunulmaz.
BREAKING_KEYWORDS = (
    "deprem", "şehit", "ölü", "yaralı", "saldırı", "patlama", "yangın",
    "sel", "kaza", "uçak", "kriz", "istifa", "operasyon", "grev", "seçim",
    "tutukla", "dava", "yaptırım", "ateşkes", "savaş", "terör", "soykırım",
    "ambargo", "rekor", "skandal", "protesto",
)

# Kaynak adi basliga eklendiginde okunmamasi gereken takilari temizler.
# Ornek: "Barzani: ... - soL Haber." -> "Barzani: ..."
_SOURCE_SUFFIX_PATTERN = None

# --- ONEM SIRALAMASI --------------------------------------------------
# Editorler bir haberi secmek icin yalnizca tazelige bakmaz; konunun
# agirligina, kaynagin guvenilirligine ve kullanilan ifadelere de bakar.
# Asagidaki agirliklar guncel ve dikkat cekici haberlerin bultenin
# basinda yer almasini saglar.
CATEGORY_WEIGHT = {
    "Gundem": 5.0,
    "Siyaset": 5.2,
    "Ekonomi": 4.4,
    "Dunya": 3.6,
    "Saglik": 3.4,
    "Teknoloji": 3.2,
    "Cevre": 3.1,
    "Egitim": 3.0,
    "Bilim": 2.6,
    "Spor": 2.2,
    "Kultur-Sanat": 2.1,
    "Yasam": 2.0,
    "Asya": 2.0,
    "Avrupa": 2.0,
    "Amerika": 2.0,
    "Orta Dogu": 2.0,
    "Ukrayna-Rusya": 2.0,
}

IMPORTANCE_KEYWORDS = (
    "deprem", "savaş", "saldırı", "patlama", "yangın", "sel", "kaza",
    "uçak", "ölü", "yaralı", "şehit", "operasyon", "kriz", "acil",
    "zam", "faiz", "enflasyon", "dolar", "bütçe", "vergi", "işsizlik",
    "seçim", "anlaşma", "karar", "istifa", "atama", "tutukla", "dava",
    "yasa", "milyar", "rekor", "kritik", "uyarı", "yaptırım", "ateşkes",
    "soykırım", "protesto", "grev", "kıtlık", "salgın",
)

TRANSITION_TR = "Şimdi Türkiye gündemine bakıyoruz."
COUNTRY_LEAD = "Şimdi {ulke} gündemine bakıyoruz."
CLOSING_TEMPLATE = (
    "{kanal} bülteninin sonuna geldik. "
    "Gündemdeki gelişmeleri aktarmaya devam edeceğiz."
)


@transaction.atomic
def build_bulletin(
    title: str = "",
) -> Playout:
    """Bekleyen haberlerden dengeli bir yayin bolumu kurar.

    Denge kurali:
      - Once Turkiye (en fazla TR_QUOTA haber)
      - Sonra dunya: her ulkeden sirayla birer (ulke turu, atlanmaz)
      - En basta birkac son dakika
      - Bulten kapanisla biter
    """
    config = settings.HABER_AJANSI
    main_anchor = _get_main_anchor()

    if not main_anchor:
        raise RuntimeError(
            "Ana spiker yok. Once su komutu calistir: "
            "python manage.py build_anchor --country TR --name \"Türkiye Spikeri\" --main"
        )

    playout = Playout.objects.create(
        title=title or f"Bulten - {timezone.localtime().strftime('%d.%m.%Y %H:%M')}",
        status=Playout.DRAFT,
    )

    order = 0
    items: list[PlayoutItem] = []
    used_categories: list[str] = []

    def add_item(**kwargs) -> PlayoutItem:
        """Sirali yeni kalem ekler. (Ic fonksiyon: order otomatik artar)"""
        nonlocal order
        order += 1
        item = PlayoutItem.objects.create(playout=playout, order=order, **kwargs)
        items.append(item)
        return item

    def add_story(article: Article, *, breaking: bool, announce: bool = True) -> PlayoutItem:
        category_name = article.category.name if article.category else ""
        if category_name and category_name not in used_categories:
            used_categories.append(category_name)
        speaker = _get_anchor_for_country(article.country_code, main_anchor)
        baslik = _clean_title(article.title, article.source.name if article.source else "")
        if breaking:
            text = _breaking_text(article) if announce else _story_text(article)
            ticker = TICKER_TEMPLATE.format(baslik=_short(baslik, 70))
        else:
            text = _story_text(article)
            ticker = f"{_short(baslik, 70)}  •  {config['kanal_adi']}  •  CANLI YAYIN"
        return add_item(
            kind=PlayoutItem.BREAKING if breaking else PlayoutItem.STORY,
            article=article,
            text=text,
            anchor=speaker,
            country_code=article.country_code,
            category_name=category_name,
            title_text=baslik,
            source_text=_source_label(article),
            ticker_text=ticker,
        )

    def add_transition(text: str, *, label: str, anchor: Anchor) -> PlayoutItem:
        return add_item(
            kind=PlayoutItem.TRANSITION,
            text=text,
            anchor=anchor,
            country_code=anchor.country_code,
            title_text=label,
            ticker_text=f"{config['kanal_adi']}  •  CANLI YAYIN",
        )

    
    add_item(
        kind=PlayoutItem.OPENING,
        text=OPENING_TEMPLATE.format(
            spiker=main_anchor.name,
            saat=_format_clock(),
            kanal=config["kanal_adi"],
        ),
        anchor=main_anchor,
        country_code=main_anchor.country_code,
        title_text=main_anchor.name,
        ticker_text=f"{config['kanal_adi']}  •  CANLI YAYIN",
    )

    lineup = _build_lineup(_select_unplayed_articles())

    
    breaking = [a for a in lineup if _is_breaking_worthy(a)][:BREAKING_COUNT]
    breaking_ids = {a.id for a in breaking}
    announced = not bool(breaking)
    for article in breaking:
        add_story(article, breaking=True, announce=not announced)
        announced = True

    # Kültür sanat en sona: siralama oyunu yapmak yerine siyaset/gundem
    # oncelikli akis korunur; kulturel basliklar tum ulkelerin ardindan gelir.
    
    body = [a for a in lineup if a.id not in breaking_ids]
    grouped = _group_segments(body)
    for label, group in grouped:
        if label == "TR":
            add_transition(TRANSITION_TR, label="Türkiye", anchor=main_anchor)
        else:
            add_transition(
                COUNTRY_LEAD.format(ulke=COUNTRY_NAMES.get(label, label)),
                label=COUNTRY_NAMES.get(label, label),
                anchor=_get_anchor_for_country(label, main_anchor),
            )
        for article in group:
            add_story(article, breaking=False)

    
    add_item(
        kind=PlayoutItem.CLOSING,
        text=CLOSING_TEMPLATE.format(kanal=config["kanal_adi"]),
        anchor=main_anchor,
        country_code=main_anchor.country_code,
        title_text=config["kanal_adi"],
        ticker_text=f"{config['kanal_adi']}  •  CANLI YAYIN",
    )

    playout.categories_used = ", ".join(used_categories[:20])
    playout.item_count = len(items)
    playout.total_seconds = int(sum(i.estimate_duration(config["okuma_hizi"]) for i in items))
    playout.save(update_fields=["categories_used", "item_count", "total_seconds"])

    logger.info(
        "Bulten kuruldu: %s (%d kalem, ~%s)",
        playout.title, playout.item_count, playout.duration_display,
    )
    return playout


def _importance(article: Article) -> float:
    """Bir haberin yayin onemini puanlar (buyuk deger = daha once).

    Olcutler: tazelik (baskin), kategori agirligi, kaynak guvenilirligi ve
    mansete uygun anahtar kelimeler. Boylece bulten yalnizca "en yeni"
    degil, "en guncel ve en dikkat cekici" haberleri one alir.
    """
    score = 0.0

    moment = article.published_at or article.ingested_at
    if moment:
        age_hours = max(0.0, (timezone.now() - moment).total_seconds() / 3600.0)
        # Yarim omur ~18 saat: taze haber belirgin one gecer, ancak bir
        # gunluk onemli bir haber de tamamen dislanmaz.
        score += 6.0 * math.exp(-age_hours / 18.0)

    category_name = article.category.name if article.category else ""
    score += CATEGORY_WEIGHT.get(category_name, 2.0)

    source = getattr(article, "source", None)
    trust = getattr(source, "trust_score", None) if source is not None else None
    if trust:
        score += min(max(float(trust), 0.0), 100.0) / 100.0 * 2.0

    haystack = f"{article.title} {article.summary or ''}".lower()
    hits = sum(1 for keyword in IMPORTANCE_KEYWORDS if keyword in haystack)
    score += min(hits, 5) * 1.4

    if article.is_breaking:
        score += 1.5

    return score


def _select_unplayed_articles() -> list[Article]:
    """Yayinlanmis bultenlerde sunulmamis tum etkin haberleri dondurur.

    Sadece TAMAMLANMIS bultenlerdeki (READY / ON_AIR) haberler "sunulmus"
    sayilir. Yarida kesilen (BUILDING) veya hata veren (ERROR) bultenlerin
    haberleri havuza geri doner; boylece tek bir kesintida kanal susmaz.
    """
    candidates = (
        Article.objects.filter(is_active=True)
        .exclude(playout_items__isnull=False)
        .select_related("category", "source")
        .order_by("-published_at", "-ingested_at", "-pk")
        .distinct()
    )
    selected: list[Article] = []
    seen_titles: set[str] = set()
    for article in candidates:
        if article.title_key in seen_titles:
            continue
        seen_titles.add(article.title_key)
        selected.append(article)
    selected.sort(key=_importance, reverse=True)
    return selected


def _is_breaking_worthy(article: Article) -> bool:
    """Bir haberin flas/son dakika olarak sunulup sunulamayacagina karar verir.

    is_breaking bayragi ag zamanlarinda dolu gelir; ancak "Cilt lekelerini
    hafifletir" gibi icerik haber degeri tasimaz. Yalnizca icerikte gercek
    flas ifadeleri olanlar son dakika olarak sunulur.
    """
    if not article.is_breaking:
        return False
    text = f"{_clean_title(article.title, article.source.name if article.source else '')} {article.summary or ''}".lower()
    return any(keyword in text for keyword in BREAKING_KEYWORDS)


def _clean_title(title: str, source_name: str = "") -> str:
    """Basliktan kaynak adi takisini ayiklar.

    Kaynaklarin cogu basligin sonuna "- KaynakAdi" ekler ("... - Reuters").
    Spiker bunu okumamali; ancak haber iceriginde "- X" anlamli olabilir
    ("... - Samsun'da") diye YALNIZCA kaynagin gercek adiyla esleyen
    koctepe soyleriz, jenerik regex degil.
    """
    if not title:
        return ""
    cleaned = title.strip()
    if source_name and cleaned.endswith(" - " + source_name):
        cleaned = cleaned[: -len(" - " + source_name)].strip()
    known_sources = (
        "Reuters", "IGN", "Haberler", "Novak", "The Washington Post",
        "Anadolu Ajansı", "Business Insider", "Yahoo Sports", "CTV News",
        "CIDRAP", "Kitco", "The New York Times", "Türkiye Today", "Mondoweiss",
        "Pulse 2.0", "The Business Journals", "Le Monde", "Yeni Şafak",
    )
    for source in known_sources:
        suffix = " - " + source
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    return cleaned.strip().rstrip("-–:;").strip()


def _build_lineup(candidates: list[Article]) -> list[Article]:
    """Turkiye once, sonra ulke turuyla dengeli secim yapar.

    - Turkiye: en taze TR_QUOTA haber.
    - Dunya: ulkeye gore grupla; ulkeleri en yeni haberine gore sirala;
      her ulkeden sirayla birer haber al (boylece tek bir ulke -or. 96
      ABD haberi- yayin akisini doldurmaz, her ulke temsil edilir).
    """
    turkey = [a for a in candidates if a.country_code == "TR"]
    world = [
        a
        for a in candidates
        if a.country_code != "TR"
        and (a.category.name if a.category else "") not in WORLD_SKIP_CATEGORIES
    ]

    selected: list[Article] = []
    seen: set[int] = set()

    def take(article: Article) -> None:
        if article.id in seen:
            return
        seen.add(article.id)
        selected.append(article)

    for article in turkey[:TR_QUOTA]:
        take(article)

    def recency(article: Article):
        # Siralamayi tazelik + kategori + kaynak + anahtar kelimeye gore
        # yapan onem puani. Isim geriye donuk uyumluluk icin 'recency'.
        return _importance(article)

    by_country: dict[str, list[Article]] = {}
    for article in world:
        by_country.setdefault(article.country_code, []).append(article)
    for group in by_country.values():
        group.sort(key=recency, reverse=True)

    country_order = sorted(by_country, key=lambda cc: recency(by_country[cc][0]), reverse=True)
    index = {cc: 0 for cc in country_order}

    while len(selected) < MAX_ITEMS:
        progressed = False
        for cc in country_order:
            if len(selected) >= MAX_ITEMS:
                break
            position = index[cc]
            if position < len(by_country[cc]):
                take(by_country[cc][position])
                index[cc] = position + 1
                progressed = True
        if not progressed:
            break

    return selected


def _group_segments(articles: list[Article]) -> list[tuple[str, list[Article]]]:
    """Turkiye tek blok; dunya ulke ulke; kultur-sanat EN SONA.

    Kultur ve sanat basliklari bir haber bulteninin sonunda yer alir;
    ilk acilista yer alan bir "kultur sanat" haberi profesyonel degildir.
    """
    segments: list[tuple[str, list[Article]]] = []
    culture = [
        a for a in articles
        if (a.category.name if a.category else "") in {"Kultur-Sanat", "Yasam"}
    ]
    culture_ids = {a.id for a in culture}
    rest = [a for a in articles if a.id not in culture_ids]

    turkey = [a for a in rest if a.country_code == "TR"]
    if turkey:
        segments.append(("TR", turkey))

    groups: dict[str, list[Article]] = {}
    for article in rest:
        if article.country_code == "TR":
            continue
        groups.setdefault(article.country_code, []).append(article)
    for country_code, group in groups.items():
        segments.append((country_code, group))

    if culture:
        segments.append(("TR", culture))
    return segments


def _story_text(article: Article) -> str:
    """Bir haberin okunacak metnini kurar.

    Yapi:  BASLIK. OZET.

    Neden bu sira? Gercek spikerler once OLAYI soyler ("Bakan aciklama
    yapti"), sonra detayi verir. Detayi once verirse izleyici ne
    oldugunu anlamadan gider.

    Neden sadece ozet? RSS tam metin vermiyor. Bu bir eksiklik degil:
    zaten gercek TV haberleri de ozet okur.
    """
    summary = (article.summary or "").strip()
    summary = _short(summary, SUMMARY_LIMIT)

    baslik = _clean_title(article.title, article.source.name if article.source else "")
    text = f"{baslik.rstrip('.')}. {summary}".strip()

    text = text.replace("..", ".").replace(". .", ".").strip()
    # Alt yazılar ve sunum Türkçe olsun diye içeriği Türkçeye çevir
    if translate_text and article.country_code != "TR":
        try:
            tr_text = translate_text(text, "auto", "tr")
            if tr_text and tr_text.strip() and tr_text.strip() != text.strip():
                text = tr_text
        except Exception:
            pass
    return clean_for_speech(text)


def _breaking_text(article: Article) -> str:
    """Son dakika haberinin metni.

    Gercek ajans farki: son dakika metni daha RESMI ve DUYGUSAL olur.
    "Son dakika" ile baslar. Kaynak adi OKUNMAZ; kaynak bilgisi ekranda
    (item.source_text) gosterilir.
    """
    body = _short((article.summary or article.title).strip(), SUMMARY_LIMIT)
    baslik = _clean_title(article.title, article.source.name if article.source else "")
    text = BREAKING_LEAD.format(baslik=baslik.rstrip(".")) + " " + body
    text = text.replace("..", ".").strip()
    if translate_text and article.country_code != "TR":
        try:
            tr_text = translate_text(text, "auto", "tr")
            if tr_text and tr_text.strip() and tr_text.strip() != text.strip():
                text = tr_text
        except Exception:
            pass
    return clean_for_speech(text)


def _source_label(article: Article) -> str:
    """Ekranda gosterilecek kaynak etiketi: "Kaynak: Reuters"

    Modelde mevcut olan kayıtlı kaynak adını kullanır.
    """
    if article.source:
        return f"Kaynak: {article.source.name}"
    return "Kaynak: dosya"


def _short(text: str, limit: int) -> str:
    """Metni kisaltir, ama KELIME ORTASINDA kesmez.

    Neden kelime ortasinda kesmiyoruz? "depremden sonra b" -> ekranda
    "depremden sonra b..." gorunur, amat. Kelime sinirinda kesip sonuna
    uc nokta koyuyoruz.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text

    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(",;:-") + "..."


def _format_clock() -> str:
    """Konusma metni icin saatin Turkce okunusu.

    Neden "22.33" degil "22 saat 33"? gTTS ve TTS motorlari nokta/iki
    noktayi yanlis okur ("22 nokta 33", "yirmi iki uc uc"). Profesyonel
    bir spiker saati "yirmi iki otuz uc" diye okur; biz de metni oyle
    kurariz. Ekranda dijital saat ayrica gosterilir.
    """
    now = timezone.localtime()
    hour = _turkish_number(now.hour)
    minute = "sıfır " + _turkish_number(now.minute) if now.minute < 10 else _turkish_number(now.minute)
    return f"{hour} {minute}"


_TURKISH_UNITS = {
    0: "sıfır", 1: "bir", 2: "iki", 3: "üç", 4: "dört", 5: "beş",
    6: "altı", 7: "yedi", 8: "sekiz", 9: "dokuz",
    10: "on", 11: "on bir", 12: "on iki", 13: "on üç", 14: "on dört",
    15: "on beş", 16: "on altı", 17: "on yedi", 18: "on sekiz", 19: "on dokuz",
    20: "yirmi", 21: "yirmi bir", 22: "yirmi iki", 23: "yirmi üç",
    24: "yirmi dört", 25: "yirmi beş", 26: "yirmi altı", 27: "yirmi yedi",
    28: "yirmi sekiz", 29: "yirmi dokuz", 30: "otuz", 31: "otuz bir",
    32: "otuz iki", 33: "otuz üç", 34: "otuz dört", 35: "otuz beş",
    36: "otuz altı", 37: "otuz yedi", 38: "otuz sekiz", 39: "otuz dokuz",
    40: "kırk", 41: "kırk bir", 42: "kırk iki", 43: "kırk üç",
    44: "kırk dört", 45: "kırk beş", 46: "kırk altı", 47: "kırk yedi",
    48: "kırk sekiz", 49: "kırk dokuz", 50: "elli", 51: "elli bir",
    52: "elli iki", 53: "elli üç", 54: "elli dört", 55: "elli beş",
    56: "elli altı", 57: "elli yedi", 58: "elli sekiz", 59: "elli dokuz",
}


def _turkish_number(value: int) -> str:
    return _TURKISH_UNITS.get(value, str(value))


def _get_main_anchor() -> Anchor | None:
    """Ana spikeri dondurur."""
    return Anchor.objects.filter(is_main=True).first()


# Bu ulkeler icin ayri spiker olusturulmaz; haberleri ana spiker sunar.
COUNTRIES_WITHOUT_DEDICATED_ANCHOR = set()


IL_COUNTRY_CODE = "IL"
KR_COUNTRY_CODE = "KR"


def _get_anchor_for_country(country_code: str, main_anchor: Anchor) -> Anchor:
    """Haberi, ülke için kayıtlı spikerle eşleştir; yoksa yerel spiker kaydı aç."""
    if country_code == IL_COUNTRY_CODE:
        main_anchor_local = _get_main_anchor() or main_anchor
        return main_anchor_local

    if country_code == main_anchor.country_code:
        return main_anchor
    if country_code in COUNTRIES_WITHOUT_DEDICATED_ANCHOR:
        return main_anchor

    speaker = (
        Anchor.objects.filter(country_code=country_code, is_active=True)
        .order_by("-is_main", "pk")
        .first()
    )
    if speaker:
        return speaker

    voice, pitch, rate = VOICE_VARIANTS.get(
        country_code,
        VOICE_VARIANTS.get(main_anchor.country_code, VOICE_VARIANTS["TR"]),
    )
    country_name = COUNTRY_NAMES.get(country_code, country_code)
    return Anchor.objects.create(
        name=f"{country_name} Spikeri",
        country_code=country_code,
        gender=Anchor.FEMALE,
        voice=voice,
        voice_pitch=pitch,
        voice_rate=rate,
        is_main=False,
        is_active=True,
    )