from django.db.models import Exists, OuterRef, Q

from .models import NotificationEndpoint, NotificationPreference, NotificationProvider
from .policies import DEFAULT_PREFERENCES


def eligible_endpoints(notification_type: str):
    """Resolve user preference, then endpoint preference, then the safe default."""

    endpoint_preference = NotificationPreference.objects.filter(
        endpoint=OuterRef("pk"),
        notification_type=notification_type,
        enabled=True,
    )
    any_endpoint_preference = NotificationPreference.objects.filter(
        endpoint=OuterRef("pk"), notification_type=notification_type
    )
    user_preference = NotificationPreference.objects.filter(
        user=OuterRef("user_id"),
        notification_type=notification_type,
        enabled=True,
    )
    any_user_preference = NotificationPreference.objects.filter(
        user=OuterRef("user_id"), notification_type=notification_type
    )

    queryset = NotificationEndpoint.objects.filter(
        active=True,
        provider=NotificationProvider.WEBPUSH,
        webpush_subscription__isnull=False,
    ).annotate(
        endpoint_enabled=Exists(endpoint_preference),
        endpoint_explicit=Exists(any_endpoint_preference),
        user_enabled=Exists(user_preference),
        user_explicit=Exists(any_user_preference),
    )
    allowed = Q(user_explicit=True, user_enabled=True) | Q(
        user_explicit=False,
        endpoint_explicit=True,
        endpoint_enabled=True,
    )
    if DEFAULT_PREFERENCES.get(notification_type, False):
        allowed |= Q(user_explicit=False, endpoint_explicit=False)
    return queryset.filter(allowed).distinct()
