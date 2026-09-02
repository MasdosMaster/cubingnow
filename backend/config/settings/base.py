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
    "apps.notifications",
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
    "DEFAULT_THROTTLE_RATES": {"notifications": "120/minute"},
}

# Guest Web Push. VAPID keys are persistent deployment secrets; the application
# never generates or rotates them automatically.
PUSH_NOTIFICATION_PROVIDER = os.getenv("PUSH_NOTIFICATION_PROVIDER", "webpush")
PUSH_RECORD_EVENT_SOURCE = os.getenv("PUSH_RECORD_EVENT_SOURCE", "disabled")
WEB_PUSH_VAPID_PUBLIC_KEY = os.getenv("WEB_PUSH_VAPID_PUBLIC_KEY", "")
WEB_PUSH_VAPID_PRIVATE_KEY = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "")
WEB_PUSH_VAPID_SUBJECT = os.getenv(
    "WEB_PUSH_VAPID_SUBJECT", "mailto:contact@cubingnow.com"
)
PUSH_WORKER_POLL_INTERVAL_SECONDS = float(
    os.getenv("PUSH_WORKER_POLL_INTERVAL_SECONDS", "5")
)
PUSH_WORKER_BATCH_SIZE = int(os.getenv("PUSH_WORKER_BATCH_SIZE", "50"))
PUSH_WORKER_MAX_ATTEMPTS = int(os.getenv("PUSH_WORKER_MAX_ATTEMPTS", "5"))
PUSH_WORKER_RETRY_SCHEDULE_SECONDS = tuple(
    int(value)
    for value in os.getenv(
        "PUSH_WORKER_RETRY_SCHEDULE_SECONDS", "60,300,1800,7200"
    ).split(",")
    if value.strip()
)
PUSH_WORKER_CLAIM_TIMEOUT_SECONDS = int(
    os.getenv("PUSH_WORKER_CLAIM_TIMEOUT_SECONDS", "300")
)
PUSH_WORKER_IDENTIFIER = os.getenv("PUSH_WORKER_IDENTIFIER", "")
WEB_PUSH_TTL_SECONDS = int(os.getenv("WEB_PUSH_TTL_SECONDS", "300"))
WEB_PUSH_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("WEB_PUSH_REQUEST_TIMEOUT_SECONDS", "10")
)

# WCA Live observational record-ingestion experiment. The subscription window
# rolls from Wednesday through Tuesday unless both date overrides are supplied.
WCA_LIVE_API_URL = os.getenv(
    "WCA_LIVE_API_URL",
    os.getenv("WCA_LIVE_GRAPHQL_URL", "https://live.worldcubeassociation.org/api"),
)
WCA_LIVE_WS_URL = os.getenv(
    "WCA_LIVE_WS_URL", "wss://live.worldcubeassociation.org/socket/websocket"
)
WCA_WEEKEND_START = os.getenv("WCA_WEEKEND_START", "")
WCA_WEEKEND_END = os.getenv("WCA_WEEKEND_END", "")
WCA_WEEKEND_TIME_ZONE = os.getenv("WCA_WEEKEND_TIME_ZONE", "Europe/Amsterdam")
WCA_COMPETITION_LOOKBACK_DAYS = int(os.getenv("WCA_COMPETITION_LOOKBACK_DAYS", "7"))
WCA_API_POLL_INTERVAL_SECONDS = int(os.getenv("WCA_API_POLL_INTERVAL_SECONDS", "60"))
WCA_ROUND_DISCOVERY_INTERVAL_SECONDS = int(
    os.getenv("WCA_ROUND_DISCOVERY_INTERVAL_SECONDS", "900")
)
WCA_SUBSCRIPTION_CATCHUP_MINUTES = int(os.getenv("WCA_SUBSCRIPTION_CATCHUP_MINUTES", "60"))
WCA_RETRY_BASE_SECONDS = float(os.getenv("WCA_RETRY_BASE_SECONDS", "1"))
WCA_RETRY_MAX_SECONDS = float(os.getenv("WCA_RETRY_MAX_SECONDS", "60"))
WCA_RETRY_MAX_ATTEMPTS = int(os.getenv("WCA_RETRY_MAX_ATTEMPTS", "5"))
WCA_RECORD_VALIDATION_BATCH_SIZE = int(
    os.getenv("WCA_RECORD_VALIDATION_BATCH_SIZE", "250")
)
SOURCE_OBSERVATION_RETENTION_DAYS = int(
    os.getenv("SOURCE_OBSERVATION_RETENTION_DAYS", "30")
)
SOURCE_OBSERVATION_RETENTION_INTERVAL_SECONDS = int(
    os.getenv("SOURCE_OBSERVATION_RETENTION_INTERVAL_SECONDS", "86400")
)

# Public accepted-registration synchronization. This is deliberately separate
# from the fixed WCA Live verification window above.
ATTENDANCE_WINDOW_TIME_ZONE = os.getenv(
    "ATTENDANCE_WINDOW_TIME_ZONE", "Europe/Amsterdam"
)
ATTENDANCE_SYNC_STALE_HOURS = int(os.getenv("ATTENDANCE_SYNC_STALE_HOURS", "12"))
WCA_PUBLIC_BASE_URL = os.getenv(
    "WCA_PUBLIC_BASE_URL", "https://www.worldcubeassociation.org"
)
WCA_PUBLIC_EXPORT_URL = os.getenv(
    "WCA_PUBLIC_EXPORT_URL",
    "https://www.worldcubeassociation.org/api/v0/export/public",
)
CUBINGCHINA_BASE_URL = os.getenv("CUBINGCHINA_BASE_URL", "https://cubing.com")
CUBINGCHINA_WS_URL = os.getenv("CUBINGCHINA_WS_URL", "wss://cubing.com/ws")
CUBINGCHINA_DISCOVERY_INTERVAL_SECONDS = int(
    os.getenv("CUBINGCHINA_DISCOVERY_INTERVAL_SECONDS", "900")
)
CUBINGCHINA_LOOKBACK_DAYS = int(os.getenv("CUBINGCHINA_LOOKBACK_DAYS", "1"))
CUBINGCHINA_LOOKAHEAD_DAYS = int(os.getenv("CUBINGCHINA_LOOKAHEAD_DAYS", "7"))
CUBINGCHINA_COMPLETION_GRACE_MINUTES = int(
    os.getenv("CUBINGCHINA_COMPLETION_GRACE_MINUTES", "720")
)
CUBINGCHINA_MAX_CONNECTIONS = int(os.getenv("CUBINGCHINA_MAX_CONNECTIONS", "10"))
CUBINGCHINA_RETRY_BASE_SECONDS = float(
    os.getenv("CUBINGCHINA_RETRY_BASE_SECONDS", "1")
)
CUBINGCHINA_RETRY_MAX_SECONDS = float(
    os.getenv("CUBINGCHINA_RETRY_MAX_SECONDS", "60")
)
CUBINGCHINA_KEEPALIVE_SECONDS = float(os.getenv("CUBINGCHINA_KEEPALIVE_SECONDS", "55"))
WORKER_TELEMETRY_INTERVAL_SECONDS = max(
    float(os.getenv("WORKER_TELEMETRY_INTERVAL_SECONDS", "5")), 1
)

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
        "integrations.cubingchina": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps.records.management.commands": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps.records.baseline_export": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps.competitors": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps.notifications": {
            "handlers": ["console"],
            "level": os.getenv("CUBINGNOW_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
