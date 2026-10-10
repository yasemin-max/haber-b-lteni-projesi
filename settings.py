import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_load_env_file(ENV_FILE)

DEBUG = _env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY zorunludur; guclu ve gizli bir anahtar belirleyin."
        )
    SECRET_KEY = "local-development-only-not-for-deployment"

if not DEBUG and (
    len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
    or re.search(r"change-me|insecure|example|placeholder", SECRET_KEY, re.I)
):
    raise ImproperlyConfigured(
        "Uretim icin DJANGO_SECRET_KEY en az 50 karakter ve guclu olmalidir."
    )

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0"
    ).split(",")
    if host.strip()
]

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3")
if DATABASE_URL.startswith(("postgres://", "postgresql://")):
    database_url = urlparse(DATABASE_URL)
    query = parse_qs(database_url.query)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(database_url.path.lstrip("/")),
            "USER": unquote(database_url.username or ""),
            "PASSWORD": unquote(database_url.password or ""),
            "HOST": database_url.hostname or "",
            "PORT": database_url.port or "",
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"sslmode": query.get("sslmode", ["require"])[0]},
        }
    }
elif DATABASE_URL.startswith("sqlite:///"):
    sqlite_path = unquote(DATABASE_URL[len("sqlite:///"):])
    if not sqlite_path:
        sqlite_path = "db.sqlite3"
    if sqlite_path == ":memory:":
        sqlite_file = Path(":memory:")
    else:
        is_windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", sqlite_path))
        db_path = Path(sqlite_path)
        sqlite_file = db_path if db_path.is_absolute() or is_windows_absolute else BASE_DIR / db_path
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(sqlite_file),
        }
    }
else:
    raise ImproperlyConfigured(
        "DATABASE_URL yalnizca sqlite:/// veya postgres:// / postgresql:// olabilir."
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "news",
    "ingest",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Europe/Istanbul"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

HABER_AJANSI = {
    "kanal_adi": os.environ.get("KANAL_ADI", "HABER 24"),
    "ana_spiker": os.environ.get("ANA_SPIKER", "Deniz Aksoy"),
    "okuma_hizi": int(os.environ.get("OKUMA_HIZI", "150")),
    "min_haber_suresi": 20,
    "max_haber_suresi": 180,
    "blokta_haber_sayisi": 6,
    "ingest_aralik_dakika": 5,
    "spiker_seed": int(os.environ.get("SPIKER_SEED", "42")),
    "spiker_genislik": int(os.environ.get("SPIKER_GORSEL_GENISLIK", "768")),
    "spiker_yukseklik": int(os.environ.get("SPIKER_GORSEL_YUKSEKLIK", "1152")),
    "ana_ses": os.environ.get("ANA_SESI", "tr-TR-EmelNeural"),
    "bilidirm_sesi": os.environ.get("BILDIRIM_SESI", "tr-TR-EmelNeural"),
    "ses_hizi": "+8%",
    "ses_perde": "+0Hz",
    "video_genislik": 768,
    "video_yukseklik": 1152,
    "fps": 30,
}

COMMERCIAL_SECONDS = 30
CEVIRI_ANAHTARI = os.environ.get("CEVIRI_ANAHTARI", "")
MYMEMORY_EMAIL = os.environ.get("MYMEMORY_EMAIL", CEVIRI_ANAHTARI)
TRANSLATION_PROVIDER = os.environ.get("TRANSLATION_PROVIDER", "mymemory").lower()
LIBRETRANSLATE_URL = os.environ.get(
    "LIBRETRANSLATE_URL", "https://libretranslate.com/translate"
)
LIBRETRANSLATE_API_KEY = os.environ.get("LIBRETRANSLATE_API_KEY", "")
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media" / "yayin")))
MEDIA_RETENTION_DAYS = int(os.environ.get("MEDIA_RETENTION_DAYS", "30"))
MEDIA_YAYIN = MEDIA_ROOT
MEDIA_URL = "/media/"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
