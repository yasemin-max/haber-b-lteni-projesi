"""Uygulama acilisinda kendi kendini kurma (tek tik calistirma).

Render gibi ortamlarda baslatma komutu ne olursa olsun (gunicorn/wsgi),
uygulama yuklendiginde kaynaklari ve spikerleri hazirlar; ardindan haber
studyosu dongusunu (run_studio) ayri bir surecte baslatir. Boylece tek bir
baglantiyla, hicbir komut gorunmeden bulten uretilir.

Yerelde (DEBUG=True), testlerde ve yonetim komutlarinda calismaz.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("news.bootstrap")

_started = False
_lock_handle = None
_studio_proc = None
_watchdog_started = False


def _enabled() -> bool:
    if os.environ.get("GUNDEM_BOOTSTRAP", "1") == "0":
        return False
    if "test" in sys.argv:
        return False
    argv0 = (sys.argv[0] or "").lower()
    if argv0.endswith("manage.py") or "pytest" in argv0:
        return False
    try:
        from django.conf import settings

        if settings.DEBUG:
            return False
    except Exception:
        return False
    return True


def _acquire_lock() -> bool:
    global _lock_handle
    try:
        import fcntl
    except Exception:
        return True
    try:
        from django.conf import settings

        lock_path = Path(settings.MEDIA_ROOT) / ".studio.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _lock_handle = open(lock_path, "w")
        fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except Exception:
        return False


def start() -> None:
    """Uygulama yuklenirken bir kez cagrilir. Kendi thread'ini acar."""
    global _started
    if _started:
        return
    _started = True
    if not _enabled():
        return
    threading.Thread(target=_run, name="gundem-bootstrap", daemon=True).start()


def _run() -> None:
    time.sleep(5)
    from django.db import close_old_connections

    close_old_connections()
    try:
        if not _acquire_lock():
            logger.info("Kurulum baska bir surecte yapiliyor, atlaniyor.")
            return
        from news.locks import writer_lock

        with writer_lock(timeout=300) as ok:
            if ok:
                _ensure_migrated()
                _seed(images=False)
        _spawn_studio()
        with writer_lock(timeout=300) as ok:
            if ok:
                _seed(images=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bootstrap hatasi: %s", exc)
    finally:
        close_old_connections()


def _ensure_migrated() -> None:
    from django.core.management import call_command
    from django.db import connection

    try:
        connection.ensure_connection()
        if "news_anchor" not in connection.introspection.table_names():
            call_command("migrate", interactive=False, verbosity=0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("migrate kontrolu: %s", exc)


def _seed(images: bool = True) -> None:
    from django.core.management import call_command

    try:
        call_command("seed_sources", verbosity=0)
        logger.info("seed_sources tamam.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_sources hatasi: %s", exc)

    kwargs = {"verbosity": 0}
    if not images:
        kwargs["no_image"] = True
    try:
        call_command("seed_anchors", **kwargs)
        logger.info("seed_anchors tamam (gorsel=%s).", images)
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_anchors hatasi: %s", exc)


def _spawn_studio() -> None:
    global _studio_proc
    try:
        from django.conf import settings

        cmd = [
            sys.executable,
            "manage.py",
            "run_studio",
            "--interval=5",
            "--max-age=60",
            # Cok yuksek tutuyoruz: gecici bir hata (kaynak 502, ceviri kotasi)
            # yuzunden dongu kalici olarak DURMASIN. Durdurursak bir daha
            # bulten uretilmez ve canli yayin bos kalir.
            "--max-errors=100000",
        ]
        kwargs = {}
        env = os.environ.copy()
        # Alt surecin ciktisi log dosyasina blok tamponla yaziliyordu; bu
        # yuzden ilerleme canli gorunmuyordu. Satir tamponu icin zorla.
        env["PYTHONUNBUFFERED"] = "1"
        kwargs["env"] = env
        if os.name == "posix":
            kwargs["start_new_session"] = True
        try:
            log_path = Path(settings.MEDIA_ROOT) / "run_studio.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
            kwargs["stdout"] = log_file
            kwargs["stderr"] = subprocess.STDOUT
        except Exception:  # noqa: BLE001
            pass
        _studio_proc = subprocess.Popen(cmd, cwd=os.getcwd(), **kwargs)
        logger.info("run_studio ayri surecte baslatildi.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_studio baslatilamadi: %s", exc)
    _ensure_watchdog()


def _ensure_watchdog() -> None:
    """run_studio bir sekilde olurse (OOM/cokme) yeniden baslatacak nobetci.

    Aksi halde ucretsiz sunucuda tek bir cokme, bir daha bulten
    uretilmemesine ve canli yayinin bos kalmasina yol aciyordu.
    """
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    threading.Thread(
        target=_studio_watchdog, name="gundem-studio-watchdog", daemon=True
    ).start()


def _studio_watchdog() -> None:
    while True:
        time.sleep(20)
        try:
            if _studio_proc is not None and _studio_proc.poll() is None:
                continue
            logger.warning("run_studio durmus gorunuyor; yeniden baslatiliyor.")
            _spawn_studio()
        except Exception as exc:  # noqa: BLE001
            logger.warning("run_studio watchdog hatasi: %s", exc)
