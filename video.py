import logging
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

FFMPEG = "ffmpeg"


if os.name == "nt":
    FONT_BOLD = "C\\:/Windows/Fonts/arialbd.ttf"
    FONT_REGULAR = "C\\:/Windows/Fonts/arial.ttf"
else:
    FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"


def _sanitize_text(value, max_len=120):
    """FFmpeg drawtext filtresi icin metni guvenli hale getirir."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text.replace("\x00", "")

def check_environment() -> dict:
    """FFmpeg'in gerekli ozellikleri var mi kontrol eder.

    Sunumdan once calistirip "sistemimde FFmpeg kurulu, yazi (drawtext)
    ve altyazi (subtitles/libass) destegi var" diyebilmen lazim.
    """
    report = {"ffmpeg": False, "drawtext": False, "subtitles": False, "version": ""}

    if shutil.which(FFMPEG) is None:
        return report

    report["ffmpeg"] = True

    try:
        version = subprocess.run(
            [FFMPEG, "-version"], capture_output=True, text=True, timeout=20, check=True
        )
        if version.stdout:
            report["version"] = version.stdout.splitlines()[0]

        filters = subprocess.run(
            [FFMPEG, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
        return report

    listing = filters.stdout or ""
    report["drawtext"] = "drawtext" in listing
    report["subtitles"] = "subtitles " in listing

    return report


def build_anchor_shot(
    anchor_image,
    audio,
    subtitle,
    output,
    duration,
    lower_third="",
    lower_third_sub="",
    ticker="",
    background=None,
    headline_text="",
) -> dict:
    """Tek bulten kaleminin videosunu uretir.

    Donus: {"ok": bool, "path": str, "error": str}
    """
    config = getattr(settings, "HABER_AJANSI", {})
    if not config:
        return {"ok": False, "path": "", "error": "HABER_AJANSI ayarlari bulunamadi."}

    width = int(config.get("video_genislik", 768))
    height = int(config.get("video_yukseklik", 1152))
    fps = int(config.get("fps", 30))

    if width <= 0 or height <= 0 or fps <= 0:
        return {"ok": False, "path": "", "error": "video boyutlari veya fps gecersiz"}

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    anchor_image = Path(anchor_image)
    audio = Path(audio)

    if duration <= 0:
        return {"ok": False, "path": "", "error": "video suresi sifirdan buyuk olmali"}
    if not anchor_image.exists():
        return {"ok": False, "path": "", "error": "spiker gorseli yok: " + str(anchor_image)}
    if not audio.exists():
        return {"ok": False, "path": "", "error": "ses dosyasi yok: " + str(audio)}

    if not anchor_image.is_file() or not audio.is_file():
        return {"ok": False, "path": "", "error": "girdi dosyalari gecersiz"}

    frames = max(2, int(duration * fps))

    # A) KATMANLARI: [0] = spiker, [1] = arka plan
    background_path = Path(background) if background is not None else None
    has_background = background_path is not None and str(background_path).strip() and background_path.exists() and background_path.is_file()

    inputs = [
        "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}",
        "-i", str(anchor_image),
    ]
    if has_background:
        inputs += [
            "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}",
            "-i", str(background_path),
        ]
    audio_input_index = 2 if has_background else 1
    inputs += ["-i", str(audio)]

    steps = []

    steps.append(
        "[0:v]"
        "scale={w}:{h}:force_original_aspect_ratio=increase,"
        "crop={w}:{h},"
        "zoompan=z='min(zoom+0.0009,1.15)':d={n}:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        "fps={fps}:s={w}x{h},setsar=1[anchor]".format(
            w=width, h=height, n=frames, fps=fps
        )
    )

    if has_background:
        # Arka plan: bulaniklastir ve koyulastir ki yazi okunabilsin.
        steps.append(
            "[1:v]"
            "scale={w}:{h}:force_original_aspect_ratio=increase,"
            "crop={w}:{h},boxblur=10:2,eq=brightness=-0.22:saturation=0.7[bg]".format(
                w=width, h=height
            )
        )
        steps.append("[bg][anchor]overlay=x='W*0.55':y='(H-h)/2'[base]")
    else:
        steps.append("[anchor]null[base]")

    draws = []

    if ticker:
        ticker_text = _sanitize_text(ticker, max_len=80)
        if ticker_text:
            draws.append(
                _drawtext(
                    text=ticker_text,
                    x="w-text_w-(t*55)",
                    y="28",
                    size=27,
                    color="white",
                    boxcolor="black@0.80",
                    boxborderw=14,
                )
            )

    if headline_text:
        headline = _sanitize_text(headline_text, max_len=100)
        if headline:
            draws.append(
                _drawtext(
                    text=headline,
                    x="(w-text_w)/2",
                    y="118",
                    size=40,
                    color="white",
                    boxcolor="black@0.72",
                    boxborderw=16,
                    enable="between(t,0.3,8)",
                    bold=True,
                )
            )

    if lower_third:
        lower_text = _sanitize_text(lower_third, max_len=60)
        if lower_text:
            draws.append(
                _drawtext(
                    text=lower_text,
                    x="44",
                    y="h-170",
                    size=44,
                    color="white",
                    boxcolor="0xB91C1C@0.92",
                    boxborderw=14,
                    enable="between(t,0.2,5.5)",
                    bold=True,
                )
            )
    if lower_third_sub:
        lower_sub_text = _sanitize_text(lower_third_sub, max_len=70)
        if lower_sub_text:
            draws.append(
                _drawtext(
                    text=lower_sub_text,
                    x="46",
                    y="h-112",
                    size=26,
                    color="0xFFE4E4",
                    boxcolor="0xB91C1C@0.92",
                    boxborderw=14,
                    enable="between(t,0.2,5.5)",
                )
            )

    chain = "[base]"
    for index, draw in enumerate(draws):
        next_label = "[v{}]".format(index)
        steps.append("{}{}{}".format(chain, draw, next_label))
        chain = next_label

    subtitle_file = Path(subtitle) if subtitle else None
    if subtitle_file:
        if not subtitle_file.is_file():
            return {"ok": False, "path": "", "error": "altyazi dosyasi yok: " + str(subtitle_file)}
        subtitle_path = _escape_filter_path(subtitle_file.resolve())
        next_label = "[video]"
        steps.append(
            "{}subtitles='{}':force_style='FontSize=20,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&HB0000000,"
            "BorderStyle=3,Outline=3,Shadow=0,Alignment=2,MarginV=110'{}".format(
                chain, subtitle_path, next_label
            )
        )
        video_label = next_label
    else:
        video_label = chain

    command = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    command += inputs
    command += [
        "-filter_complex", ";".join(steps),
        "-map", video_label,
        "-map", f"{audio_input_index}:a",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output.resolve()),
    ]

    return _run(command, output)


def _escape_filter_path(path: Path) -> str:
    """Escape a path for a quoted FFmpeg filter option."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")



# 3) drawtext YARDIMCISI

def _drawtext(
    text,
    x,
    y,
    size,
    color="white",
    boxcolor="",
    boxborderw=12,
    enable="",
    bold=False,
) -> str:
    """drawtext filtresini kurar.

    ESCAPE KURALLARI (FFmpeg metin formatinda her karakterin anlami var):
      :  ->  kacisli   filtre ayiricisi
      '  ->  degistir   metin sinirlayicisini bozmamak icin tipografik tirnak
      %  ->  kacisli   yuzde bicimlendirme karakteri
      ,  ->  kacisli   filtre secenek ayiricisi
      \\ ->  kacisli   kacinislama karakterinin kendisi

    ⚠️ Turkce karakterler: FFmpeg'in libass/librsvg surumu UTF-8 bekliyor.
    Dosyaya UTF-8 yaziyoruz, sorun cikmamasi gerekir. Cikmadiysa
    "Liberation Sans" fontunu dene (daha genis Unicode destegi).
    """
    font = FONT_BOLD if bold else FONT_REGULAR

    safe = _sanitize_text(text, max_len=200)
    safe = safe.replace("\\", "\\\\")
    safe = safe.replace(":", "\\:")
    safe = safe.replace("'", "\u2019")   # duz tirnak -> tipografik tirnak
    safe = safe.replace('"', "\u201c")
    safe = safe.replace("%", "\\%")
    safe = safe.replace(",", "\\,")

    parts = [
        "drawtext=fontfile='{font}'".format(font=font),
        "text='{text}'".format(text=safe),
        "x={x}".format(x=x),
        "y={y}".format(y=y),
        "fontsize={size}".format(size=size),
        "fontcolor={color}".format(color=color),
    ]

    if boxcolor:
        parts.append("box=1")
        parts.append("boxcolor={box}".format(box=boxcolor))
        parts.append("boxborderw={border}".format(border=boxborderw))

    if enable:
        parts.append("enable='{enable}'".format(enable=enable))

    return ":".join(parts)



# 4) PARCALARI BIRLESTIRME

def concat_segments(segments, output) -> dict:
    """Coklu video parcasini tek dosyada birlestirir.

    Neden "concat demuxer"?
      - demuxer: parcalari OLDUGU GIBI yapistirir. Hizli, kalite KORUNUR.
        AMA hepsi ayni kodek/ cozunurlukle uretilmis olmali.
      - concat filter: her parcayi YENIDEN kodlar. Yavas, kalite duser.
    Biz hepsini ayni ayarlarla urettigimiz icin demuxer dogru secim.

    Liste dosyasi:
        ffconcat version 1.0
        file 'C:/yol/bir.mp4'
        file 'C:/yol/iki.mp4'
    Yollarda duz "/" kullanmamiz lazim.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    valid = []
    for segment in segments:
        path = Path(segment)
        if path.exists():
            valid.append(path)

    if not valid:
        return {"ok": False, "path": "", "error": "birlestirilecek video yok"}

    list_file = output.parent / (output.stem + "_liste.txt")
    lines = ["ffconcat version 1.0"]
    for path in valid:
        lines.append("file '" + path.as_posix() + "'")
    list_file.write_text("\n".join(lines), encoding="utf-8")

    command = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",              # yeniden kodlama yok
        "-movflags", "+faststart",
        str(output),
    ]

    result = _run(command, output)
    if result["ok"]:
        list_file.unlink(missing_ok=True)
    return result



# 5) FFmpeg CALISTIRMA 
def _run(command, output, cwd=None):
    """FFmpeg komutunu calistirir, hatayi insan tarafindan okunur hale getirir.

    FFmpeg hata mesajlari cok uzundur; sadece SON 6 satiri gosteriyoruz.
    Sunumda "neden olmus?" diye sorulursa ekrana bakarsin.

    `cwd`: komutun calisacagi klasor. Altyazi dosyasi goreli yolla
    referanslandigi icin dogru klasor onemli.
    """
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=600, cwd=str(cwd) if cwd else None
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": "", "error": "FFmpeg zaman asimi (600 saniye)"}
    except FileNotFoundError:
        return {"ok": False, "path": "", "error": "ffmpeg bulunamadi. PATH'e eklemelisin."}
   
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        tail = " | ".join(lines[-6:])
        logger.error("FFmpeg hatasi: %s", tail)
        return {"ok": False, "path": "", "error": tail[:800]}

    if not Path(output).exists():
        return {"ok": False, "path": "", "error": "cikti dosyasi olusmadi"}

    size_kb = Path(output).stat().st_size / 1024
    logger.info("Video hazir: %s (%.0f KB)", output.name, size_kb)
    return {"ok": True, "path": str(output), "error": ""}