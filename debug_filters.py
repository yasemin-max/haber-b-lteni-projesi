import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import django  # noqa: E402

django.setup()

import video  # noqa: E402

temp_root = Path(os.environ.get("TEMP") or ROOT / "tmp")
TEMP = temp_root / "opencode" / "video_test"
TEMP.mkdir(parents=True, exist_ok=True)
srt = temp_root / "opencode" / "tts_test" / "test.srt"
img = TEMP / "spiker.jpg"
aud = temp_root / "opencode" / "tts_test" / "test.mp3"

captured = {}


def fake_run(command, output, cwd=None):
    captured["cmd"] = command
    result = subprocess.run(
        command, capture_output=True, text=True, cwd=str(cwd) if cwd else None
    )
    captured["rc"] = result.returncode
    captured["stderr"] = result.stderr
    return {"ok": result.returncode == 0, "path": "", "error": ""}


video._run = fake_run

result = video.build_anchor_shot(
    anchor_image=img,
    audio=aud,
    subtitle=srt,
    output=TEMP / "capture.mp4",
    duration=11.0,
    lower_third="Lee Joung",
    lower_third_sub="Gündem Haberleri",
    ticker="HABER 24  •  CANLI YAYIN",
    headline_text="Gündemin başındayız",
)

if not result.get("ok"):
    print(f"video üretilemedi: {result.get('error') or 'bilinmeyen hata'}")
    raise SystemExit(0)

command = captured["cmd"]
graph = command[command.index("-filter_complex") + 1]

print("=" * 74)
print("URETILEN FILTER_COMPLEX")
print("=" * 74)
for index, part in enumerate(graph.split(";")):
    print("{}. {}".format(index, part))
print()

print("=" * 74)
print("FFmpeg TAM HATA CIKTISI")
print("=" * 74)
print(captured.get("stderr", "(cikti yok)"))
print()

# --- Her parcayi tek tek dene: hangisi bozuk? ---
print("=" * 74)
print("ADIM ADIM TEKRAR")
print("=" * 74)

parts = graph.split(";")
running = ""
for index in range(len(parts)):
    trial = ";".join(parts[: index + 1])
    if not trial.endswith("[video]"):
        trial = "{}null[video]".format(trial)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-t", "2", "-i", str(img),
        "-i", str(aud),
        "-filter_complex", trial,
        "-map", "[video]", "-map", "1:a",
        "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str((TEMP / "step{}.mp4".format(index)).resolve()),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(TEMP))
    first_line = (res.stderr or "").strip().splitlines()
    note = first_line[0][:130] if first_line else ""
    print("  {:<2} {}".format(index, "OK  " if res.returncode == 0 else "HATA"))
    if note:
        print("      ->", note)