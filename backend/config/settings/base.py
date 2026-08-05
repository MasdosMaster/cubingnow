import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-development-only")
DEBUG = False
ALLOWED_HOSTS = [host for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if host]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.records",
    "apps.competitions",
    "apps.competitors",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default="postgresql://cuberecord:cuberecord@localhost:5432/cuberecord",
        conn_max_age=60,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    origin for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin
]
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# WCA Live observational record-ingestion experiment. Dates are deliberately
# centralized here and can be overridden without changing code or containers.
WCA_LIVE_API_URL = os.getenv(
    "WCA_LIVE_API_URL",
    os.getenv("WCA_LIVE_GRAPHQL_URL", "https://live.worldcubeassociation.org/api"),
)
WCA_LIVE_WS_URL = os.getenv(
    "WCA_LIVE_WS_URL", "wss://live.worldcubeassociation.org/socket/websocket"
)
WCA_WEEKEND_START = os.getenv("WCA_WEEKEND_START", "2026-08-06")
WCA_WEEKEND_END = os.getenv("WCA_WEEKEND_END", "2026-08-10")
WCA_COMPETITION_LOOKBACK_DAYS = int(os.getenv("WCA_COMPETITION_LOOKBACK_DAYS", "7"))
WCA_API_POLL_INTERVAL_SECONDS = int(os.getenv("WCA_API_POLL_INTERVAL_SECONDS", "60"))
WCA_ROUND_DISCOVERY_INTERVAL_SECONDS = int(
    os.getenv("WCA_ROUND_DISCOVERY_INTERVAL_SECONDS", "900")
)
WCA_SUBSCRIPTION_CATCHUP_MINUTES = int(os.getenv("WCA_SUBSCRIPTION_CATCHUP_MINUTES", "60"))
WCA_RETRY_BASE_SECONDS = float(os.getenv("WCA_RETRY_BASE_SECONDS", "1"))
WCA_RETRY_MAX_SECONDS = float(os.getenv("WCA_RETRY_MAX_SECONDS", "60"))
WCA_RETRY_MAX_ATTEMPTS = int(os.getenv("WCA_RETRY_MAX_ATTEMPTS", "5"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "worker": {
            "format": "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "worker",
        }
    },
    "loggers": {
        "integrations.wca_live": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps.records.management.commands": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
