from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import socket
import time
import unicodedata
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

try:
    from .models import Article, Category, IngestRun, Source, Translation
except ImportError:  # pragma: no cover - fallback when used outside a Django package
    try:
        from models import Article, Category, IngestRun, Source, Translation
    except Exception:  # pragma: no cover - isolated execution fallback
        class _MissingManager:
            def __getattr__(self, _name: str):
                raise RuntimeError("Django model not configured. Initialize the app models before calling ingestion logic.")

        class _MissingModel:
            objects = _MissingManager()

            @classmethod
            def __getattr__(cls, name: str):
                raise RuntimeError(f"Model '{cls.__name__}' is not available. Configure the Django app models first.")

        class Translation(_MissingModel):
            @staticmethod
            def make_hash(text: str) -> str:
                raise RuntimeError("Translation model is not configured.")

        class IngestRun(_MissingModel):
            OK = "ok"
            ERROR = "error"

        class Source(_MissingModel):
            API = "api"

        class Article(_MissingModel):
            pass

        class Category(_MissingModel):
            pass

from sources_data import BREAKING_QUERIES, COUNTRY_QUERIES, CATEGORY_KEYWORDS, COUNTRY_KEYWORDS

logger = logging.getLogger(__name__)
# Ceviri saglayicilari kapali oldugunda her metin icin ayni uyari basilip
# logu dolduruyordu; bu bayrakla surec basina BIR kez uyaririz.
_warned_translation_unavailable = False
# 429 (cok istek) durumunda ust uste basarisiz ceviri denemesi servisi
# dakikalarca mesgul ediyordu. Bu devre kesici, art arda hatalardan sonra
# ceviriyi bir sure DEVRE DISI birakir; toplama hizla tamamlanir.
_translation_failures = 0
_translation_cooldown_until = 0.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TRANSLATE_CHUNK = 400
VIDEO_LOOKUP_LIMIT_PER_SOURCE = 3
DEFAULT_CATEGORY = "Gundem"
DEFAULT_COUNTRY = "TR"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _require_model(model_cls: type[Any], model_name: str) -> None:
    if getattr(model_cls, "objects", None) is None:
        raise RuntimeError(f"{model_name} modeli tanimli degil; Django app modelini yukleyin.")


def build_google_news_url(query: str, lang: str, gl: str, ceid: str, hours: int = 12) -> str:
    from urllib.parse import quote_plus

    url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(f'{query} when:{hours}h')}"
        f"&hl={lang}&gl={gl}&ceid={ceid}"
    )
    return url


def fetch_feed(url: str, timeout: int = 25) -> list[dict[str, Any]]:
    """RSS adresini ceker ve standart bir sozluk listesine cevirir."""
    if not url:
        logger.warning("RSS URL bos; islem atlandi.")
        return []

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("RSS cekilemedi: %s (%s)", url, exc)
        raise

    parsed = feedparser.parse(response.content or response.text)
    if not getattr(parsed, "entries", None):
        logger.info("RSS bos geldi (0 haber): %s", url)
        return []

    articles: list[dict[str, Any]] = []
    for entry in parsed.entries:
        title = clean_text(getattr(entry, "title", ""))
        summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        article_url = str(getattr(entry, "link", "") or "").strip()
        articles.append(
            {
                "title": title,
                "summary": summary,
                "url": article_url,
                "published": _extract_datetime(entry),
                "image": _extract_image(entry),
                "video": _extract_video(entry),
                "publisher": _extract_publisher(title),
            }
        )
    return articles


def _extract_datetime(entry) -> datetime | None:
    """RSS'ten yayin zamanini cikarir."""
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct:
        return datetime(*struct[:6], tzinfo=UTC)

    for field in ("published", "updated"):
        raw = getattr(entry, field, None)
        if raw:
            parsed = parse_datetime(raw)
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.utc)
                return parsed

    return timezone.now()


def _extract_image(entry) -> str:
    for media in getattr(entry, "media_content", []) or []:
        if media.get("url"):
            return media["url"]

    for thumb in getattr(entry, "media_thumbnail", []) or []:
        if thumb.get("url"):
            return thumb["url"]

    for enc in getattr(entry, "enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image"):
            return enc.get("href", "")

    summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, flags=re.IGNORECASE)
    if match:
        return html.unescape(match.group(1))

    return ""


def _extract_video(entry) -> str:
    """RSS'in doğrudan sunduğu oynatılabilir video URL'sini bul."""
    candidates = []
    candidates.extend(getattr(entry, "media_content", []) or [])
    candidates.extend(getattr(entry, "enclosures", []) or [])
    candidates.extend(getattr(entry, "links", []) or [])

    for media in candidates:
        url = str(media.get("url") or media.get("href") or "").strip()
        media_type = str(media.get("type") or "").lower()
        medium = str(media.get("medium") or "").lower()
        relation = str(media.get("rel") or "").lower()
        path = url.lower().split("?", 1)[0]
        is_video = (
            medium == "video"
            or media_type.startswith("video/")
            or (relation == "enclosure" and path.endswith((".mp4", ".webm", ".m3u8", ".mov")))
            or path.endswith((".mp4", ".webm", ".m3u8", ".mov"))
        )
        if is_video and url.startswith(("http://", "https://")):
            return url[:500]

    return ""


class _ArticleVideoParser(HTMLParser):
    """Yayıncı sayfasındaki doğrudan video kaynaklarını bulur."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.candidates: list[str] = []
        self._json_ld: list[str] = []
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "script" and "ld+json" in attributes.get("type", "").lower():
            self._in_json_ld = True
            return

        src = attributes.get("src", "")
        if tag in {"video", "source"} and src:
            self.candidates.append(src)
        elif tag == "meta":
            key = (
                attributes.get("property", "")
                or attributes.get("name", "")
            ).lower()
            value = attributes.get("content", "").strip()
            media_type = attributes.get("type", "").lower()
            path = value.lower().split("?", 1)[0]
            if key in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}:
                if media_type.startswith("video/") or path.endswith(
                    (".mp4", ".webm", ".m3u8", ".mov")
                ):
                    self.candidates.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_ld.append(data)


def _video_urls_from_json(value: Any):
    if isinstance(value, dict):
        type_value = value.get("@type", "")
        types = type_value if isinstance(type_value, list) else [type_value]
        if any(str(item).casefold() == "videoobject" for item in types):
            content_url = value.get("contentUrl")
            if isinstance(content_url, str):
                yield content_url
        for child in value.values():
            yield from _video_urls_from_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _video_urls_from_json(child)


def _parse_article_video_metadata(page_html: str, page_url: str) -> str:
    parser = _ArticleVideoParser()
    parser.feed(page_html)
    candidates = list(parser.candidates)
    for script in parser._json_ld:
        try:
            candidates.extend(_video_urls_from_json(json.loads(script)))
        except json.JSONDecodeError:
            logger.debug("Gecersiz JSON-LD video metaverisi: %s", page_url)

    for candidate in candidates:
        video_url = urljoin(page_url, html.unescape(candidate.strip()))
        parsed = urlparse(video_url)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return video_url[:500]
    return ""


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = {
            ipaddress.ip_address(result[4][0].split("%", 1)[0])
            for result in socket.getaddrinfo(parsed.hostname, parsed.port or default_port)
        }
    except (OSError, ValueError):
        return False
    return bool(addresses) and all(address.is_global for address in addresses)


def extract_article_video_url(article_url: str, timeout: float = 6.0) -> str:
    """API anahtarı kullanmadan sayfadan herkese açık doğrudan video URL'sini çıkar."""
    current_url = article_url
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=3.0),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for _ in range(4):
                if not _is_public_http_url(current_url):
                    logger.warning("Video metaverisi için özel/erişilemeyen URL reddedildi: %s", current_url)
                    return ""
                with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            return ""
                        current_url = urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    if "html" not in response.headers.get("content-type", "").lower():
                        return ""
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 1_000_000:
                            logger.warning("Haber sayfası video taraması için çok büyük: %s", current_url)
                            return ""
                    encoding = response.encoding or "utf-8"
                    page_html = content.decode(encoding, errors="replace")
                video_url = _parse_article_video_metadata(page_html, current_url)
                if video_url and _is_public_http_url(video_url):
                    return video_url
                return ""
    except (httpx.HTTPError, OSError, ValueError, UnicodeError) as exc:
        logger.info("Haber sayfasında video bulunamadı (%s): %s", article_url, exc)
    return ""


def _extract_publisher(title: str) -> str:
    """Basliktan yayin evini cikarir; ' - ' ayracini kullanir."""
    if " - " in title:
        return title.rsplit(" - ", 1)[1].strip()
    return ""


def translate_text(
    text: str,
    source_lang: str = "en",
    target_lang: str = "tr",
) -> str:
    """Metni hedef dile cevirir.

    Varsayilan: yabanci metni Turkceye cevirir (target_lang="tr").
    Ulke spikerleri kendi dillerinde konusacagi icin target_lang ile
    ornegin Turkce bir ozeti Almancaya cevirebiliriz.

    Ceviri yapilamazsa orijinal metin OLDUGU GIBI doner ve onbellege
    YAZILMAZ; boylece sonraki denemede tekrar cevrilir.
    """
    global _warned_translation_unavailable, _translation_failures, _translation_cooldown_until

    if not text or len(text.strip()) < 3:
        return text

    if source_lang == target_lang:
        return text

    # Devre kesici: servis 429 veriyorsa bir sure hic denemeden gec.
    if time.monotonic() < _translation_cooldown_until:
        return text

    try:
        cache_key = f"{source_lang}>{target_lang}\x00{text}"
        text_hash = Translation.make_hash(cache_key)
        cached = Translation.objects.filter(source_hash=text_hash).first()
        if (
            cached
            and cached.target_text.strip()
            and cached.target_text.strip() != text.strip()
        ):
            logger.debug("Onbellekten dondu: %s", text[:40])
            return cached.target_text

        chunks = _chunk_text(text, TRANSLATE_CHUNK)
        translated_chunks = []

        for chunk in chunks:
            translated = _call_translator(chunk, source_lang, target_lang)
            if translated:
                translated_chunks.append(translated)

        result = " ".join(translated_chunks).strip()
        if not result or len(result) < 3 or result == text.strip():
            _translation_failures += 1
            if _translation_failures >= 8:
                _translation_cooldown_until = time.monotonic() + 300.0
                if not _warned_translation_unavailable:
                    logger.warning(
                        "Ceviri saglayicilari mesgul (429). 5 dk boyunca ceviri "
                        "devre disi; orijinal metinler kullanilacak."
                    )
                    _warned_translation_unavailable = True
            return text

        _translation_failures = 0
        Translation.objects.update_or_create(
            source_hash=text_hash,
            defaults={
                "source_text": text,
                "target_text": result,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "quality": 100.0,
            },
        )
        return result
    except RuntimeError:
        logger.warning("Ceviri modeli mevcut degil; orijinal metin korunuyor")
        return text

def _call_translator(text: str, source_lang: str, target_lang: str = "tr") -> str:
    """Tek metin parcasini hedef dile cevirir.

    Saglayici sirasi (ilk basarili olan kazanir):
      1. Google ucretsiz ucnokta  -> hizli, anahtarsiz, guvenilir
      2. MyMemory                 -> ucretsiz ama sik 429 verir
      3. LibreTranslate           -> istege bagli (TRANSLATION_PROVIDER)

    Ceviri yapilamazsa BOS STRING ("") doner. Cagiran taraf bunu
    "ceviri yok" sayip onbellege YAZMAZ; boylece sonraki denemede
    tekrar denenir.
    """
    if not text or not text.strip():
        return ""

    translated = _try_google(text, source_lang, target_lang)
    if translated:
        return translated

    provider = getattr(settings, "TRANSLATION_PROVIDER", "mymemory").lower()
    if provider == "libretranslate":
        translated = _try_libretranslate(text, source_lang, target_lang)
        if translated:
            return translated

    return _try_mymemory(text, source_lang, target_lang)


def _try_google(text: str, source_lang: str, target_lang: str = "tr") -> str:
    """Google'in ucretsiz ceviri ucnoktasindan ceviri ceker.

    Anahtar/kayit gerektirmez. Basarisiz olursa "" doner.
    """
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": source_lang,
                    "tl": target_lang,
                    "dt": "t",
                    "q": text,
                },
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            response.raise_for_status()
            # Try to decode as UTF-8 explicitly if needed
            try:
                data = response.json()
            except Exception:
                # force utf-8
                data = response.content.decode('utf-8', errors='replace')
                import json
                data = json.loads(data)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("Google ceviri hatasi: %s", exc)
        return ""

    try:
        translated = "".join(
            segment[0] for segment in data[0] if segment and segment[0]
        ).strip()
    except (TypeError, IndexError, KeyError):
        return ""

    return translated


def _try_mymemory(text: str, source_lang: str, target_lang: str = "tr") -> str:
    """MyMemory ucretsiz cevirisi (anahtar zorunlu degil). Basarisizsa ""."""
    params = {"q": text, "langpair": f"{source_lang}|{target_lang}"}
    mymemory_email = getattr(settings, "MYMEMORY_EMAIL", "")
    if mymemory_email:
        params["de"] = mymemory_email

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get("https://api.mymemory.translated.net/get", params=params)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("MyMemory ceviri hatasi: %s", exc)
        return ""

    if data.get("responseStatus") != 200:
        return ""

    return data.get("responseData", {}).get("translatedText", "") or ""


def _try_libretranslate(text: str, source_lang: str, target_lang: str = "tr") -> str:
    """LibreTranslate cevirisi (istege bagli). Basarisizsa ""."""
    endpoint = getattr(settings, "LIBRETRANSLATE_URL", "https://libretranslate.com/translate")
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            payload = {
                "q": text,
                "source": source_lang,
                "target": target_lang,
                "format": "text",
            }
            api_key = getattr(settings, "LIBRETRANSLATE_API_KEY", "")
            if api_key:
                payload["api_key"] = api_key
            response = client.post(endpoint, json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("LibreTranslate ceviri hatasi: %s", exc)
        return ""

    translated = (
        data.get("translatedText")
        or data.get("translation")
        or data.get("data", {}).get("translatedText")
    )
    return translated if translated else ""


_TURKISH_CHARS = set("çğıİöşüÇĞÖŞÜ")
_TURKISH_STOPWORDS = {
    "ve", "bir", "bu", "için", "ile", "olarak", "sonra", "daha", "de", "da",
    "mi", "mı", "ki", "ya", "veya", "ama", "ancak", "çok", "türkiye", "yeni",
    "oldu", "dedi", "yıl", "gün", "haber", "karşı", "göre", "kadar", "en",
}


def is_likely_turkish(text: str) -> bool:
    """Metnin buyuk olasilikla Turkce olup olmadigini tahmin eder.

    Ceviri cagirmadan once gereksiz istegi onlemek icin: Turkce'ye ozgu
    harf varsa ya da Turkce yaygin kelimelerin orani yeterince yuksekse
    metin "zaten Turkce" kabul edilir.
    """
    if not text or not text.strip():
        return True
    if any(ch in _TURKISH_CHARS for ch in text):
        return True

    words = [
        word.strip(".,!?;:\"'()[]{}").lower()
        for word in text.split()
    ]
    words = [word for word in words if word]
    if len(words) < 4:
        return True

    hits = sum(1 for word in words if word in _TURKISH_STOPWORDS)
    return (hits / len(words)) >= 0.12


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """Metni karakter sayisina gore boler; bos parcalar eklemez."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    words = text.split()
    chunks: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        elif not current:
            current = word
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks



def detect_category(text: str, fallback: str = DEFAULT_CATEGORY) -> str:
    """Metindeki anahtar kelimelere gore kategori tespit eder."""
    if not text:
        return fallback

    text_lower = text.lower()
    best_category = fallback
    best_score = 0

    for category_name, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_category = category_name

    return best_category


def detect_country(text: str, fallback: str = DEFAULT_COUNTRY) -> str:
    """Metindeki anahtar kelimelere gore ulke kodu tespit eder."""
    if not text:
        return fallback

    text_lower = text.lower()
    best_country = fallback
    best_score = 0

    for country_code, keywords in COUNTRY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_country = country_code

    return best_country


def ingest_source(source: Source, limit: int = 40, is_breaking: bool = False) -> IngestRun:
    """Verilen kaynaktan haberleri ceker ve veritabanina kaydeder."""
    _require_model(Source, "Source")
    _require_model(IngestRun, "IngestRun")
    _require_model(Article, "Article")

    started = time.monotonic()
    run = IngestRun.objects.create(source=source)

    try:
        entries = fetch_feed(getattr(source, "url", ""))
        run.fetched_count = len(entries)

        translated = 0
        skipped = 0
        new_articles_created = 0
        video_lookups = 0

        for entry in entries[: max(0, limit)]:
            article_url = str(entry.get("url") or "").strip()
            title = clean_text(entry.get("title") or "").strip()
            summary = clean_text(entry.get("summary") or "").strip()
            published = entry.get("published")

            if not title or not article_url:
                continue

            try:
                existing = Article.objects.filter(url=article_url[:500]).first()
                if existing:
                    video_url = str(entry.get("video") or "").strip()[:500]
                    update_fields = []
                    if not existing.video_url and video_url.startswith(("https://", "http://")):
                        existing.video_url = video_url
                        update_fields.append("video_url")
                    elif not existing.video_url and existing.video_checked_at is None:
                        if video_lookups < VIDEO_LOOKUP_LIMIT_PER_SOURCE:
                            existing.video_url = extract_article_video_url(article_url)
                            existing.video_checked_at = timezone.now()
                            video_lookups += 1
                            update_fields.extend(("video_url", "video_checked_at"))
                    if update_fields:
                        existing.save(update_fields=update_fields)
                    skipped += 1
                    continue
            except Exception:
                logger.debug("Makale kontrolu gecildi: %s", article_url)

            source_lang = _guess_source_lang(source)
            if source_lang != "tr" and getattr(settings, "TRANSLATE_ON_INGEST", True):
                translated_title = translate_text(title, source_lang)
                translated_summary = translate_text(summary[:600], source_lang)
                if translated_title != title:
                    translated += 1
                title, summary = translated_title, translated_summary

            default_category_name = getattr(getattr(source, "default_category", None), "name", None) or DEFAULT_CATEGORY
            combined = f"{title} {summary}".strip()
            category = detect_category(combined, fallback=default_category_name)
            country = detect_country(combined, fallback=getattr(source, "country_code", None) or DEFAULT_COUNTRY)
            video_url = str(entry.get("video") or "").strip()[:500]
            video_checked_at = timezone.now() if video_url else None
            if not video_url and video_lookups < VIDEO_LOOKUP_LIMIT_PER_SOURCE:
                video_url = extract_article_video_url(article_url)
                video_checked_at = timezone.now()
                video_lookups += 1

            try:
                Article.objects.create(
                    title=title[:500],
                    summary=summary,
                    url=article_url[:500],
                    image_url=str(entry.get("image") or "")[:500],
                    video_url=video_url,
                    video_checked_at=video_checked_at,
                    source=source,
                    category=_get_or_create_category(category),
                    country_code=country,
                    published_at=published,
                    language="tr",
                    is_breaking=is_breaking,
                )
                new_articles_created += 1
            except Exception:
                skipped += 1
                logger.warning("Makale kaydi olusturulamadi: %s (%s)", article_url, source)
                continue

        run.new_count = new_articles_created
        run.translated_count = translated
        run.skipped_count = skipped
        run.status = IngestRun.OK

    except Exception as exc:
        run.status = IngestRun.ERROR
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        logger.exception("Kaynak cekilemedi: %s", getattr(source, "name", source))

    run.finished_at = timezone.now()
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.save()

    if hasattr(source, "last_fetched_at"):
        source.last_fetched_at = timezone.now()
    if hasattr(source, "last_status"):
        source.last_status = run.status
    if hasattr(source, "last_error"):
        source.last_error = (run.error_message or "")[:500]
    if hasattr(source, "total_articles"):
        source.total_articles = Article.objects.filter(source=source).count()

    update_fields = [
        field
        for field in ["last_fetched_at", "last_status", "last_error", "total_articles"]
        if hasattr(source, field)
    ]
    if update_fields:
        try:
            source.save(update_fields=update_fields)
        except Exception:
            logger.exception("Source metadatasi kaydedilemedi: %s", getattr(source, "name", source))

    return run


def _is_recent(moment, hours: int = 2) -> bool:
    
    if not moment:
        return False
    age = timezone.now() - moment
    return age.total_seconds() < hours * 3600


def _guess_source_lang(source: Source) -> str:
   
    url = source.url.lower()
    name = source.name.lower()

    if any(marker in url for marker in ("/tr/", ".com.tr", "tr/rss", "/tr?")):
        return "tr"
    if any(marker in name for marker in ("turkiye", "turkish", "sputnik")):
        return "tr"
    if "dw.com" in url or "deutsche" in name:
        return "de"
    if "nhk" in url or "japon" in name:
        return "ja"

    return "en"


def _get_or_create_category(name: str) -> Category:
    """Kategori varsa dondur, yoksa olustur."""
    _require_model(Category, "Category")
    category, _ = Category.objects.get_or_create(
        name=name, defaults={"slug": _slugify_tr(name), "color": "#6b7280"}
    )
    return category


def _slugify_tr(text: str) -> str:
    """Turkce karakterleri ASCII'ye cevirip slug uretir.

    Django'nun slugify() fonksiyonu Turkce karakterleri siler: "Gündem" ->
    "gundem" olur, sorun yok. Ama bazi kenar durumlarda (Saglik -> "saglik")
    elle de normalize ediyoruz.
    """
    from django.utils.text import slugify

    tr_map = str.maketrans("çÇğĞıİöÖşŞüÜâÂ", "cCgGiIoOsSuUaA")
    return slugify(text.translate(tr_map))



def ingest_all(limit_per_source: int = 30) -> dict:
    """Tum kaynaklari + Google News sorgularini topluca ceker.

    Siralama onemli: once kayitli RSS kaynaklari (guvenilir, hizli),
    sonra Google News sorgulari (son dakika + dunya turu).
    """
    stats = {"ok": 0, "error": 0, "total_new": 0, "breaking": 0}

    if not hasattr(Source, "objects") or not hasattr(IngestRun, "OK"):
        return stats

    try:
        source_queryset = Source.objects.filter(is_active=True).exclude(
            slug__startswith="google-news-query-"
        )
    except Exception:
        source_queryset = Source.objects.all()

    for source in source_queryset:
        run = ingest_source(source, limit=limit_per_source)
        if run.status == IngestRun.OK:
            stats["ok"] += 1
            stats["total_new"] += run.new_count
        else:
            stats["error"] += 1

    for name, keyword, lang, country, category_name in BREAKING_QUERIES:
        url = build_google_news_url(keyword, lang, country, f"{country}:{lang.split('-')[0]}", hours=1)
        run = _ingest_google_news(name, url, country, category_name, is_breaking=True, limit=15)
        if run and run.status == IngestRun.OK:
            stats["ok"] += 1
            stats["total_new"] += run.new_count
            stats["breaking"] += run.new_count

    for cc, keyword, lang, gl, ceid, category_name in COUNTRY_QUERIES:
        url = build_google_news_url(keyword, lang, gl, ceid, hours=12)
        run = _ingest_google_news(f"GN-{cc}", url, cc, category_name, is_breaking=False, limit=12)
        if run and run.status == IngestRun.OK:
            stats["ok"] += 1
            stats["total_new"] += run.new_count

    return stats


def _ingest_google_news(
    name: str, url: str, country: str, category_name: str, is_breaking: bool, limit: int
) -> IngestRun | None:
    """Google News sorgusunu kalici kaynak olarak cekip haberleri saklar."""
    _require_model(Source, "Source")

    category = _get_or_create_category(category_name)
    source, _ = Source.objects.update_or_create(
        slug=_slugify_tr(f"google-news-query-{name}"),
        defaults={
            "name": f"Google News: {name}",
            "url": url,
            "kind": Source.API,
            "country_code": country,
            "default_category": category,
            "is_active": True,
        },
    )
    run = ingest_source(source, limit=limit, is_breaking=is_breaking)
    Article.objects.filter(source=source).update(
        country_code=country,
        category=category,
    )
    return run