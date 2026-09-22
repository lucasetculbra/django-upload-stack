"""Django settings for the django-upload-stack project.

Every deployment-specific value is read from the environment so the same image can run
locally, in CI and in production. See docs/variaveis.md for the full reference.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    return int(value) if value else default


def env_csv(name: str, default: str = "") -> list[str]:
    """Parse a comma-separated env var, dropping empty entries.

    An empty string split on "," yields [""], which would silently reject every host,
    so blank entries are filtered out.
    """
    return [item.strip() for item in env_str(name, default).split(",") if item.strip()]


# --- Core -------------------------------------------------------------------------------

SECRET_KEY = env_str("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY is required. Copy .env.example to .env and set a unique value "
        "(see docs/instalacao.md)."
    )

DEBUG = env_bool("DEBUG", False)

# "web" is included so the compose healthcheck and in-network calls are never rejected.
ALLOWED_HOSTS = env_csv("ALLOWED_HOSTS", "localhost,127.0.0.1,web")

# Needed because the app is reached through Nginx on a non-default port (e.g. :8080).
CSRF_TRUSTED_ORIGINS = env_csv("CSRF_TRUSTED_ORIGINS")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "uploads",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------------------
# DJANGO_DB=postgres (default) requires the POSTGRES_* variables. SQLite must be requested
# explicitly: an implicit fallback inside the container would silently store data on the
# container layer, which disappears on "docker compose down" - exactly what this project
# is meant to prove does not happen.

DJANGO_DB = env_str("DJANGO_DB", "postgres").lower()

if DJANGO_DB == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env_str("SQLITE_PATH") or str(BASE_DIR / "db.sqlite3"),
        }
    }
elif DJANGO_DB == "postgres":
    required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    missing = [name for name in required if not env_str(name)]
    if missing:
        raise ImproperlyConfigured(
            f"DJANGO_DB=postgres requires {', '.join(missing)}. "
            "Check your .env file (see .env.example)."
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env_str("POSTGRES_DB"),
            "USER": env_str("POSTGRES_USER"),
            "PASSWORD": env_str("POSTGRES_PASSWORD"),
            "HOST": env_str("POSTGRES_HOST", "db"),
            "PORT": env_str("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        }
    }
else:
    raise ImproperlyConfigured(f"DJANGO_DB must be 'postgres' or 'sqlite', got {DJANGO_DB!r}.")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalization ---------------------------------------------------------------

LANGUAGE_CODE = env_str("LANGUAGE_CODE", "pt-br")
TIME_ZONE = env_str("TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True

# --- Static and media -------------------------------------------------------------------
# Defaults are project-relative so pytest and runserver work on Windows; the container
# overrides them with absolute paths that are mounted as named volumes.

STATIC_URL = "/static/"
STATIC_ROOT = Path(env_str("STATIC_ROOT") or (BASE_DIR / "staticfiles"))

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env_str("MEDIA_ROOT") or (BASE_DIR / "media"))

# Nginx (running as user "nginx") serves these files directly, so they must be
# world-readable instead of depending on the umask Gunicorn happens to inherit.
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755

# Keep the application limit in sync with Nginx's client_max_body_size.
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 100)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Security ---------------------------------------------------------------------------
# The default stack is plain HTTP behind Nginx, so these stay off unless explicitly
# enabled: turning them on without TLS would break the cookie and redirect flow.

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if env_bool("TRUST_PROXY_SSL_HEADER", False):
    # Only safe because Nginx always overwrites X-Forwarded-Proto with $scheme.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env_str("LOG_LEVEL", "INFO")},
}
