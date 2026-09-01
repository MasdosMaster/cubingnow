from django.conf import settings
from django.core import checks

from .providers import push_provider_is_configured


@checks.register(checks.Tags.security)
def check_web_push_configuration(app_configs, **kwargs):
    if settings.PUSH_NOTIFICATION_PROVIDER != "webpush":
        return []
    missing = [
        name
        for name in (
            "WEB_PUSH_VAPID_PUBLIC_KEY",
            "WEB_PUSH_VAPID_PRIVATE_KEY",
            "WEB_PUSH_VAPID_SUBJECT",
        )
        if not getattr(settings, name, "")
    ]
    if not missing:
        if push_provider_is_configured():
            return []
        return [
            checks.Warning(
                "Web Push VAPID credentials are invalid or do not match",
                hint="Keep one persistent matching VAPID key pair in deployment secrets.",
                id="notifications.W002",
            )
        ]
    message = "Web Push is not configured; missing: " + ", ".join(missing)
    return [
        checks.Warning(
            message,
            hint=(
                "The notification worker will refuse to start until persistent VAPID "
                "credentials are configured. Record-only ingestion workers may ignore this warning."
            ),
            id="notifications.W001",
        )
    ]
