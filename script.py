import logging

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
    build_headline_transition,
)
from voice import clean_for_speech

logger = logging.getLogger(__name__)


SUMMARY_LIMIT = 260


MAX_ITEMS = 24

TR_QUOTA = 12

BREAKING_COUNT = 3

WORLD_SKIP_CATEGORIES = {"Spor"}

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
            "python manage.py build_anchor --country TR --name \"Lee Joung\" --main"
        )

    playout = Playout.objects.create(
        title=title or f"Bulten - {timezone.now().strftime('%d.%m.%Y %H:%M')}",
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

    def add_story(article: Article, *, breaking: bool) -> PlayoutItem:
        category_name = article.category.name if article.category else ""
        if category_name and category_name not in used_categories:
            used_categories.append(category_name)
        speaker = _get_anchor_for_country(article.country_code, main_anchor)
        return add_item(
            kind=PlayoutItem.BREAKING if breaking else PlayoutItem.STORY,
            article=article,
            text=_breaking_text(article) if breaking else _story_text(article),
            anchor=speaker,
            country_code=article.country_code,
            category_name=category_name,
            title_text=article.title,
            source_text=_source_label(article),
            ticker_text=TICKER_TEMPLATE.format(baslik=_short(article.title, 70)),
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

    
    breaking = [a for a in lineup if a.is_breaking][:BREAKING_COUNT]
    breaking_ids = {a.id for a in breaking}
    if breaking:
        add_item(
            kind=PlayoutItem.HEADLINE,
            text=build_headline_transition(len(breaking)),
            anchor=main_anchor,
            country_code=main_anchor.country_code,
            title_text="Manşet",
            ticker_text=f"{config['kanal_adi']}  •  SON DAKİKA",
        )
        for article in breaking:
            add_story(article, breaking=True)

    
    body = [a for a in lineup if a.id not in breaking_ids]
    for label, group in _group_segments(body):
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


def _select_unplayed_articles() -> list[Article]:
    """Yayinlanmis bultenlerde sunulmamis tum etkin haberleri dondurur.

    Sadece TAMAMLANMIS bultenlerdeki (READY / ON_AIR) haberler "sunulmus"
    sayilir. Yarida kesilen (BUILDING) veya hata veren (ERROR) bultenlerin
    haberleri havuza geri doner; boylece tek bir kesintida kanal susmaz.
    """
    candidates = (
        Article.objects.filter(is_active=True)
        .exclude(
            playout_items__playout__status__in=(
                Playout.READY,
                Playout.ON_AIR,
            )
        )
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
    return selected


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
        return article.published_at or article.ingested_at

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
    """Turkiye tek blok; dunya ulke ulke (lineup sirasi korunur)."""
    segments: list[tuple[str, list[Article]]] = []
    turkey = [a for a in articles if a.country_code == "TR"]
    if turkey:
        segments.append(("TR", turkey))

    groups: dict[str, list[Article]] = {}
    for article in articles:
        if article.country_code == "TR":
            continue
        groups.setdefault(article.country_code, []).append(article)
    for country_code, group in groups.items():
        segments.append((country_code, group))
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

    text = f"{article.title.rstrip('.')}. {summary}".strip()

    text = text.replace("..", ".").replace(". .", ".").strip()

    source_label = _source_label(article)
    return clean_for_speech(f"{text} {source_label}.")


def _breaking_text(article: Article) -> str:
    """Son dakika haberinin metni.

    Gercek ajans farki: son dakika metni daha RESMI ve DUYGUSAL olur.
    "Son dakika" ile baslar, kaynak belirtilir.
    """
    country_name = COUNTRY_NAMES.get(article.country_code, "kaynaklardan")
    body = _short((article.summary or article.title).strip(), SUMMARY_LIMIT)
    text = BREAKING_LEAD.format(
        ulke=country_name,
        baslik=article.title.rstrip("."),
    ) + " " + body + " " + _source_label(article) + "."
    text = text.replace("..", ".").strip()
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
    """14.30 biciminde saat.

    Neden 14.30 (nokta) degil 14:30 (iki nokta)?
    Türk spikerleri nokta ile okur ve altyazi ortasinda iki nokta
    daha okunakli. Iki nokta karisabilir diye nokta tercih edildi.
    """
    return timezone.localtime().strftime("%H.%M")


def _get_main_anchor() -> Anchor | None:
    """Ana spikeri dondurur."""
    return Anchor.objects.filter(is_main=True).first()


# Bu ulkeler icin ayri spiker olusturulmaz; haberleri ana spiker sunar.
COUNTRIES_WITHOUT_DEDICATED_ANCHOR = {"IL"}


def _get_anchor_for_country(country_code: str, main_anchor: Anchor) -> Anchor:
    """Haberi, ülke için kayıtlı spikerle eşleştir; yoksa yerel spiker kaydı aç."""
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