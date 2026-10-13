
import logging
import time
from pathlib import Path

import httpx
from django.conf import settings

from prompts import build_anchor_prompt, build_background_prompt, build_image_url

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


BACKGROUND_DIR = "medya/arka_plan"



def generate_anchor_image(anchor, force: bool = False) -> dict:
    """Spikerin gorselini uretir ve kaydeder.

    Akis:
      1. Daha once uretildi mi? (anchor.image_path dolu mu) -> tekrar uretme
      2. Prompt'u kur  (studio/prompts.py)
      3. Adresi kur
      4. Indir
      5. Dosyaya yaz + Anchor kaydini guncelle

    `force=True` -> yeni seed ile yeniden uret (yuz degistirmek icin)
    """
    target_dir = Path(settings.MEDIA_ROOT) / "spikerler"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{anchor.country_code.lower()}_{anchor.image_seed}.jpg"

    
    if target.exists() and not force:
        logger.info("Spiker gorseli zaten var: %s", target.name)
        anchor.image_path = f"spikerler/{target.name}"
        anchor.save(update_fields=["image_path"])
        return {"ok": True, "path": str(target), "cached": True, "error": ""}

    
    prompt = build_anchor_prompt(anchor.country_code, anchor.gender)
    url = build_image_url(
        prompt,
        width=anchor.image_width,
        height=anchor.image_height,
        seed=anchor.image_seed,
    )

    
    result = _download(url, target, timeout=180)
    if not result["ok"]:
        # Gorsel servisi (orn. pollinations) limitli/basarisiz olabilir. Bu
        # durumda spikeri gorselsiz birakip kalemi ATLAMAK yerine, mevcut
        # bir spiker gorseline duseriz. Boylece dunya haberleri de yayina
        # girer. Gercek gorsel bir sonraki turda tekrar denenir (target
        # dosyasi olusmadigi icin onbellek sayilmaz).
        fallback = _fallback_image(target_dir, anchor.country_code)
        if fallback is not None:
            anchor.image_path = f"spikerler/{fallback.name}"
            anchor.save(update_fields=["image_path"])
            logger.warning(
                "Spiker gorseli uretilemedi (%s); yedek gorsel kullaniliyor: %s",
                result.get("error"),
                fallback.name,
            )
            return {
                "ok": True,
                "path": str(fallback),
                "cached": False,
                "fallback": True,
                "error": result.get("error", ""),
            }
        return result

    
    anchor.image_path = f"spikerler/{target.name}"
    anchor.image_prompt = prompt      # sunumda "bu yuz neden boyle?" diye bakariz
    anchor.save(update_fields=["image_path", "image_prompt"])

    logger.info("Spiker gorseli uretildi: %s (seed=%s)", target.name, anchor.image_seed)
    return {"ok": True, "path": str(target), "cached": False, "error": "", "prompt": prompt}



def generate_background_image(category_name: str, width: int = 768, height: int = 1152) -> dict:
    """Kategori arka planini uretir (onbellekli).

    Arka plan videoda haberin arkasinda gorunuyor. Sabit bir doku yerine
    kategoriye uygun bir sahne daha "gercek ajans" hissi veriyor:
    ekonomi -> borsa ekranlari, spor -> stadyum, bilim -> teleskop.

    ONBELLEK: dosya adi kategoriye gore sabit -> bir kez uretilir, sonsuz
    kullanilir. (Yuzlerce video icin tek istek.)
    """
    from django.utils.text import slugify

    slug = slugify(category_name) or "gundem"
    directory = Path(settings.MEDIA_ROOT) / "arka_plan"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{slug}.jpg"

    if target.exists():
        return {"ok": True, "path": str(target), "cached": True, "error": ""}

    prompt = build_background_prompt(category_name)
    # Arka plan icin seed SABIT: ayni kategori hep ayni doku -> kanal
    # gorunumu tutarli kalir. (Spikerden farkli: orada yuzu donduruyoruz,
    # burada sahneyi kuruyoruz.)
    url = build_image_url(prompt, width=width, height=height, seed=1000)

    result = _download(url, target, timeout=180)
    if result["ok"]:
        logger.info("Arka plan uretildi: %s", category_name)
    return result



def _fallback_image(target_dir: Path, country_code: str) -> Path | None:
    """Gorsel uretilemediginde kullanilacak yerel spiker gorselini secer.

    Oncelik: ayni ulkenin gorseli -> ana spiker gorseli -> herhangi bir jpg.
    """
    if not target_dir.exists():
        return None
    candidates = sorted(target_dir.glob("*.jpg"))
    if not candidates:
        return None
    for image in candidates:
        if image.name.lower().startswith(country_code.lower() + "_"):
            return image
    preferred = target_dir / "tr_42.jpg"
    if preferred in candidates:
        return preferred
    return candidates[0]


def _download(
    url: str,
    target: Path,
    timeout: int = 180,
    retries: int = 2,
    max_bytes: int = 15 * 1024 * 1024,
) -> dict:
    """Gorseli indirir. Gerekirse yeniden dener.

    Neden yeniden deneme? Ucretsiz servisler arada sicaklik veya
    kotada verir. 7/24 yayinda tek bir hata bulteni durdurmamali.
    """
    last_error = ""

    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(
                timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
            ) as client:
                with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        logger.warning(
                            "Gorsel istegi basarisiz (%s), %d. deneme",
                            last_error,
                            attempt,
                        )
                        time.sleep(2 * attempt)
                        continue

                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith("image/"):
                        last_error = f"gorsel degil, gelen: {content_type or 'bilinmiyor'}"
                        logger.warning(last_error)
                        time.sleep(2 * attempt)
                        continue

                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except ValueError:
                            last_error = "gorsel sunucusu gecersiz Content-Length dondurdu"
                            logger.warning(last_error)
                            continue
                        if declared_size > max_bytes:
                            last_error = (
                                f"gorsel boyutu izin verilen siniri ({max_bytes} bayt) asiyor"
                            )
                            logger.warning(last_error)
                            continue

                    content = bytearray()
                    too_large = False
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            too_large = True
                            break
                    if too_large:
                        last_error = f"gorsel boyutu izin verilen siniri ({max_bytes} bayt) asiyor"
                        logger.warning(last_error)
                        continue

            if len(content) < 3000:
                last_error = f"gorsel cok kucuk ({len(content)} bayt)"
                logger.warning(last_error)
                time.sleep(2 * attempt)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_target = target.with_name(f".{target.name}.download")
            temporary_target.write_bytes(content)
            temporary_target.replace(target)
            return {"ok": True, "path": str(target), "cached": False, "error": ""}

        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Gorsel indirme hatasi: %s", last_error)
            time.sleep(2 * attempt)

    return {"ok": False, "path": "", "cached": False, "error": last_error or "bilinmeyen hata"}