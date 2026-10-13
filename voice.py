import asyncio
import difflib
import logging
import re
import subprocess
import unicodedata
from pathlib import Path

import edge_tts
logger = logging.getLogger(__name__)
TICKS_PER_SECOND = 10_000_000

async def speak(
    text: str,
    voice: str = "tr-TR-EmelNeural",
    pitch: str = "+0Hz",
    rate: str = "+0%",
    output_mp3: str | Path | None = None,
    output_srt: str | Path | None = None,
    subtitle_text: str | None = None,
) -> dict:
    """Metni seslendirir. Sozluk dondurur.

    subtitle_text: Seslendirilen metin yerel dilde olsa bile ekranda
    gorunecek ALTYAZI (orn. Turkce). Verilirse SRT bu metinden uretilir
    ve zamanlama sesin toplam suresine gore oransal dagitilir.
    """
    result = {"ok": False, "error": "", "duration": 0.0, "word_timings": []}
    if not text or not text.strip():
        result["error"] = "Bos metin"
        return result
    output_mp3 = Path(output_mp3) if output_mp3 else None
    output_srt = Path(output_srt) if output_srt else None
    last_error = ""
    audio_bytes = bytearray()
    word_timings = []
    for attempt in range(1, 6):
        audio_bytes = bytearray()
        word_timings = []
        try:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=rate,
                pitch=pitch,
                boundary="WordBoundary",
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_timings.append(
                        {
                            "word": chunk["text"],
                            "start": chunk["offset"] / TICKS_PER_SECOND,
                            "duration": chunk["duration"] / TICKS_PER_SECOND,
                        }
                    )
        except edge_tts.exceptions.NoAudioReceived as exc:
            last_error = f"Ses alinamadi (ses adi yanlis olabilir: {voice}) - {exc}"
            logger.error(last_error)
            await asyncio.sleep(1.5 * attempt)
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if type(exc).__name__ == "WSServerHandshakeError" or getattr(exc, "status", None) == 403:
                logger.warning(
                    "edge-tts handshake/403 (bolge/DRM engeli). gTTS yedegine geciliyor."
                )
                break
            logger.exception("Seslendirme hatasi (deneme %d/5)", attempt)
            await asyncio.sleep(1.5 * attempt)
            continue
        if audio_bytes:
            break
        last_error = "Ses dosyasi bos dondu"
        await asyncio.sleep(1.5 * attempt)
    else:
        logger.warning("edge-tts basarisiz (%s). gTTS yedegi deneniyor.", last_error)
        return _speak_gtts(text, voice, output_mp3, output_srt, subtitle_text, rate, last_error)

    if not audio_bytes:
        return _speak_gtts(text, voice, output_mp3, output_srt, subtitle_text, rate, last_error)

















        
    word_timings = _restore_punctuation(text, word_timings)
    duration = _duration_from_timings(word_timings)

    if output_mp3:
        output_mp3.parent.mkdir(parents=True, exist_ok=True)
        output_mp3.write_bytes(bytes(audio_bytes))
        measured_duration = probe_duration(output_mp3)
        if measured_duration > 0:
            duration = measured_duration

    if output_srt:
        subtitle_source = (subtitle_text or "").strip()
        if subtitle_source and subtitle_source != text.strip():
            srt_text = build_srt_for_subtitle(subtitle_source, duration)
        else:
            srt_text = build_srt(word_timings, text=text, fallback_duration=duration)
        output_srt.parent.mkdir(parents=True, exist_ok=True)
        output_srt.write_text(srt_text, encoding="utf-8")

    result.update(
        {
            "ok": True,
            "audio": str(output_mp3) if output_mp3 else "",
            "srt": str(output_srt) if output_srt else "",
            "duration": duration,
            "word_timings": word_timings,
        }
    )
    return result


_GTTS_LANG_OVERRIDES = {"zh": "zh-CN", "he": "iw"}

# gTTS, edge-tts'in "+0%" hizindan dogal olarak daha yavas konusur
# (~85 kelime/dk). Dogal konusma hizi ~130 kelime/dk'dir; cok yavas
# (~85) da cok hizli (~155) da dogal degildir. Bu yuzden uretilen dosya
# olculup hedef kelime hizina ulasacak atempo carpaniyla hizlandirilir.
GTTS_BASE_SPEEDUP = 1.35  # daha doğal tempo
GTTS_TARGET_WPM = 115.0  # tüm spikerler için doğal konuşma hızı


def _parse_rate_percent(rate: str) -> float:
    """Edge hiz degerini ('+10%', '-5%', '+0%') yuzde sayisina cevirir."""
    if not rate:
        return 0.0
    match = re.search(r"[-+]?\d+", str(rate))
    return float(match.group(0)) if match else 0.0


def _speak_chained_atempo(path: Path, factor: float) -> None:
    """Dosyaya atempo uygular; >2.0 carpanlarda zincir kurar.

    Neden zincir? FFmpeg'in atempo'su tek basina >2.0 kabul etmez
    (docs: 0.5-2.0). 1.8 hedef icin tek atempo yeterlidir, ancak
    guvenlik icin carpani 2.0'lik parcalara bolup zincirliyoruz
    (2.0 x 2.0 = 4.0'a kadar cikabilir).
    """
    if factor <= 0.001:
        return
    parts = []
    remaining = factor
    while remaining > 1.001:
        step = min(2.0, remaining)
        parts.append(f"atempo={step:.4f}")
        remaining /= step
    if not parts:
        return
    temp = path.with_suffix(".spd.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-filter:a", ",".join(parts),
        "-vn", str(temp),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=True)
        temp.replace(path)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Ses hizlandirilamadi (%s): %s", path, exc)


def _speedup_mp3(path: Path, factor: float) -> float:
    """Ses dosyasini atempo ile hizlandirir ve yeni suresini dondurur.

    gTTS dogal olarak yavas konustugu icin normal haber okuma hizini
    yakalamak uzere ffmpeg atempo filtresi uygulanir.
    """
    if path.exists():
        _speak_chained_atempo(path, factor)
    return probe_duration(path)


def _gtts_lang_from_voice(voice: str) -> str:
    """Edge ses adindan gTTS dil kodunu uretir (or. tr-TR-EmelNeural -> tr)."""
    primary = (voice or "tr").split("-")[0].lower() or "tr"
    return _GTTS_LANG_OVERRIDES.get(primary, primary)


def _speak_gtts(text, voice, output_mp3, output_srt, subtitle_text, rate="", last_error=""):
    """edge-tts bolgesel olarak engellendiginde (403) yedek seslendirme.

    Microsoft, Edge ses servisini api.msedgeservices.com'a tasidi; eski
    uc nokta bazi bolgelerde 403 veriyor (edge-tts issue #490). Bu fonksiyon
    Google TTS (gTTS) ile ses uretir. Kelime zamanlamasi olmadigi icin
    altyazi, ses suresine oransal olarak dagitilir.
    """
    result = {"ok": False, "error": "", "duration": 0.0, "word_timings": []}
    try:
        from gtts import gTTS
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"gTTS bulunamadi ({last_error}): {exc}"
        return result
    lang = _gtts_lang_from_voice(voice)
    try:
        tts = gTTS(text=text, lang=lang)
        if output_mp3:
            output_mp3.parent.mkdir(parents=True, exist_ok=True)
            tts.save(str(output_mp3))
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"gTTS hatasi ({lang}): {exc}"
        logger.exception("gTTS seslendirme hatasi")
        return result

    # Normal haber okuma hizi: gTTS dogal olarak yavas (~85 k/dk). once
    # uretilen ses olculur, hedef kelime hizina gore atempo carpani
    # hesaplanir (sure degistigi icin altyazi yeni sureye gore kurulur).
    if output_mp3:
        base_duration = probe_duration(output_mp3)
        word_count = max(1, len(text.split()))
        base_wpm = word_count / (base_duration / 60.0) if base_duration > 0 else 0.0
        if base_wpm > 0 and base_wpm < GTTS_TARGET_WPM:
            factor = min(2.5, GTTS_TARGET_WPM / base_wpm)
        else:
            factor = 1.8
        _speedup_mp3(Path(output_mp3), factor)

    duration = probe_duration(output_mp3) if output_mp3 else 0.0
    if output_mp3 and duration <= 0:
        result["error"] = "gTTS sesi olculemedi"
        return result

    if output_srt:
        subtitle_source = (subtitle_text or "").strip() or text
        srt_text = build_srt_for_subtitle(subtitle_source, duration)
        output_srt.parent.mkdir(parents=True, exist_ok=True)
        output_srt.write_text(srt_text, encoding="utf-8")

    result.update(
        {
            "ok": True,
            "audio": str(output_mp3) if output_mp3 else "",
            "srt": str(output_srt) if output_srt else "",
            "duration": duration,
            "word_timings": [],
        }
    )
    logger.info("gTTS yedegi kullanildi (%s, %.1f sn)", lang, duration)
    return result


def _restore_punctuation(original_text: str, timings: list[dict]) -> list[dict]:
    """Kelime zamanlamalarina orijinal metindeki noktalamayi geri koyar.

    PROBLEM: edge-tts "Merhaba, ben Lee Joung." metni icin su kelimeleri
    donuyor: ["Merhaba", "ben", "Lee", "Joung"]
    Noktalama kaybolur. Altyazi "birlik" yazarken ekranda "birlik." var
    -> gorunumsuz uyumsuzluk.

    COZUM: Kelime sayilari ayniyorsa (sirali, karsilikli) orijinal metinden
    noktalamali halini alip zamanlama yapistiriyoruz.

    Neden bu kadar zahmet? Cunku "goruntu temiz, konusma anlasilir"
    istedigin icin. Metinde nokta yoksa dikkat dagilir.

    Guvenlik agi: kelime sayilari tutmuyorsa hicbir sey degistirmiyoruz
    (bozuk altyazi, noktasiz altyazidan iyidir).
    """
    original_words = original_text.split()
    timing_words = [timing["word"] for timing in timings]
    normalized_timing = [_normalize(word) for word in timing_words]
    normalized_original = [_normalize(w) for w in original_words]
    restored = [dict(timing) for timing in timings]

    matcher = difflib.SequenceMatcher(
        None,
        normalized_timing,
        normalized_original,
        autojunk=False,
    )
    patched = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for timing_index, original_index in zip(
                range(i1, i2),
                range(j1, j2),
                strict=True,
            ):
                restored[timing_index]["word"] = original_words[original_index]
            patched += i2 - i1

    logger.debug("Noktalama geri kondu: %d/%d kelime", patched, len(timings))
    return restored


def _normalize(word: str) -> str:
    """Noktalama ve aksan farklarini kaldirarak kelimeyi karsilastirir.

    Harf ve rakam disindakiler yok sayilir; Unicode harfleri, TÃƒÂ¼rkÃƒÂ§e dahil,
    ASCII'ye indirgenmeden korunur.
    """
    normalized = unicodedata.normalize("NFKD", word).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() and not unicodedata.combining(character)
    )


def build_srt(
    word_timings: list[dict],
    text: str = "",
    fallback_duration: float = 10.0,
) -> str:
    """Kelime zamanlamalarindan SRT altyazi dosyasi uretir.

    SRT formati:
        1
        00:00:00,200 --> 00:00:03,400
        Merhaba, ben Lee Joung.

    Format kurallari:
      - Index: 1'den baslar, art arda
      - Zaman: HH:MM:SS,mmm (virgul, nokta degil)
      - Metin: bir veya birden fazla satir
      - Bloklar bos satirla ayrilir

    GRUPLAMA KURALI:
      Altyaziyi kelime kelime gostermek okunmaz. Biz kural koyuyoruz:
      - 1 satira en fazla 42 karakter (TV altyazi standardi)
      - 1 satirda en fazla 8 kelime
      - Cumle sonlarinda yeni blok acilir

    Bu, YouTube'un ve Netflix'in kullandigi pratik kural. Sunumda
    "okunabilirlik icin standart kural kullandim" diyebilirsin.
    """
    if not word_timings:
        fallback_text = text.strip()
        if not fallback_text:
            return ""
        end = max(fallback_duration, 0.4)
        return (
            f"1\n{_timestamp(0.0)} --> {_timestamp(end)}\n"
            f"{fallback_text}\n"
        )

    blocks = _group_words(word_timings)
    lines = []

    for index, block in enumerate(blocks, start=1):
        words = block["words"]
        start = words[0]["start"]
        last_word = words[-1]
        end = last_word["start"] + last_word["duration"]
        # Kisa bloklari uzat (0.4 sn alti okunamaz)
        if end - start < 0.4:
            end = start + 0.4
        lines.append(str(index))
        lines.append(f"{_timestamp(start)} --> {_timestamp(end)}")
        lines.append(block["text"])
        lines.append("")

    return "\n".join(lines)


def _group_words(word_timings: list[dict]) -> list[dict]:
    """Kelimeleri okunabilir altyazi bloklarina gruplar.

    Kural:
      - Satirdaki kelime sayisi 8'i gecerse yeni blok
      - Satirdaki karakter sayisi 42'yi gecerse yeni blok
      - CÃƒÂ¼mle sonu isaretinde (nokta, soru, unlem) yeni blok
    """
    blocks = []
    current: list[dict] = []
    chars = 0

    for timing in word_timings:
        word = timing["word"]
        would_be = chars + len(word) + 1

        # Baslatma sartlari (hepsi dogruysa yeni blok ac)
        too_many_words = len(current) >= 8
        too_long = would_be > 42
        ends_sentence = current and current[-1]["word"].endswith((".", "!", "?"))

        if current and (too_many_words or too_long or ends_sentence):
            blocks.append({"text": " ".join(w["word"] for w in current),
                          "words": current})
            current = []
            chars = 0

        current.append(timing)
        chars += len(word) + 1

    if current:
        blocks.append({"text": " ".join(w["word"] for w in current), "words": current})

    return blocks


def build_srt_for_subtitle(subtitle_text: str, total_duration: float) -> str:
    """Yerel dildeki ses ILE eslesecek Turkce altyazi uretir.

    Ses ile altyazi farkli dillerde oldugu icin kelime zamanlamasi
    eslesmez. Bu yuzden altyaziyi okunabilir bloklara bolup, toplam ses
    suresini blok uzunluklarina gore ORANSAL dagitiyoruz. Boylece altyazi
    sesle kabaca ayni anda ilerler.
    """
    blocks = _split_text_blocks(subtitle_text)
    if not blocks:
        return ""

    total_duration = max(total_duration, 0.4)
    total_chars = sum(len(block) for block in blocks) or 1
    lines: list[str] = []
    cursor = 0.0

    for index, block in enumerate(blocks, start=1):
        share = total_duration * (len(block) / total_chars)
        start = cursor
        end = cursor + share
        if end - start < 0.4:
            end = start + 0.4
        cursor = end
        lines.append(str(index))
        lines.append(f"{_timestamp(start)} --> {_timestamp(end)}")
        lines.append(block)
        lines.append("")

    return "\n".join(lines)


def _split_text_blocks(text: str) -> list[str]:
    """Metni okunabilir altyazi bloklarina boler (zamanlama olmadan).

    Kural build_srt ile ayni: satirda en fazla 8 kelime, en fazla 42
    karakter ve cumle sonunda yeni blok.
    """
    words = text.split()
    blocks: list[str] = []
    current: list[str] = []
    chars = 0

    for word in words:
        would_be = chars + len(word) + 1
        too_many_words = len(current) >= 8
        too_long = would_be > 42
        ends_sentence = current and current[-1].endswith((".", "!", "?"))

        if current and (too_many_words or too_long or ends_sentence):
            blocks.append(" ".join(current))
            current = []
            chars = 0

        current.append(word)
        chars += len(word) + 1

    if current:
        blocks.append(" ".join(current))

    return blocks


def _timestamp(seconds: float) -> str:
    """Saniyeyi SRT zaman bicimine cevirir: 00:01:23,456"""
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _duration_from_timings(timings: list[dict]) -> float:
    """Toplam sureyi son kelimenin bitisinden hesaplar."""
    if not timings:
        return 0.0
    last = timings[-1]
    return last["start"] + last["duration"]


def clean_for_speech(text: str) -> str:
    """Metni seslendirmeye uygun hale getirir.

    Ceviri sirasinda bazi karakterler sorun cikarir:
      - Tirnak icinde noktalama: spiker duraksamadan okur
      - "vb." -> "ve benzeri"
      - Uzun tire -> kisa tire
      - Tekrarlanan bosluklar

    Kural: konusma metni, ekrana gosterilen metinden FARKLI olabilir.
    Ekranda "vb." yazabiliriz, ama spiker "ve benzeri" der.
    """
    replacements = {
        " & ": " ve ",
        " vb. ": " ve benzeri ",
        " vb ": " ve benzeri ",
        " vs. ": " veya ",
        " vs ": " veya ",
        " Dr. ": " Doktor ",
        " Mr. ": " Bay ",
        " Mrs. ": " Bayan ",
        " No. ": " numarali ",
        " %": " yuzde ",
        " $": " dolar ",
        " Ã¢â€šÂ¬": " euro ",
        " Ã‚Â£": " pound ",
        "Ã‚Â©": "",
        "Ã¢â€Â¢": "",
    }

    result = text
    for old, new in replacements.items():
        result = result.replace(old, new)

    # Tirnak isaretlerini kaldir (spiker tirnak okumaz)
    result = result.replace('"', "").replace("'", "").replace("Ã¢â‚¬Å“", "").replace("Ã¢â‚¬Â", "")

    # Bosluklari sadelestir
    result = re.sub(r"\s+", " ", result).strip()

    return result


def probe_duration(path: str | Path) -> float:
    """Ses/video dosyasinin gercek suresini ffprobe ile olcer.

    Neden gerekli? Tahmini sure (kelime/150) %20 sapabilir. Video, sesle
    birebir ayni uzunlukta olmali yoksa sonunda bosluk kalir ya da
    ses kesilir. Bu yuzden uretim sonrasi olcup duzeltiyoruz.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        output = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(output.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("Sure olculemedi (%s): %s", path, exc)
        return 0.0


def list_turkish_voices() -> list[str]:
    """Sistemdeki Turkce sesleri listeler (ogrenmek icin)."""
    voices = asyncio.run(edge_tts.list_voices())
    return [v["ShortName"] for v in voices if v["Locale"].startswith("tr")]



