from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .webpush import WebPushProvider


def get_push_provider():
    if settings.PUSH_NOTIFICATION_PROVIDER == "webpush":
        return WebPushProvider()
    raise ImproperlyConfigured(
        f"Unsupported PUSH_NOTIFICATION_PROVIDER: {settings.PUSH_NOTIFICATION_PROVIDER!r}"
    )


def push_provider_is_configured() -> bool:
    try:
        get_push_provider()
    except ImproperlyConfigured:
        return False
    return True
