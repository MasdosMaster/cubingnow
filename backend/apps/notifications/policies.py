from dataclasses import dataclass

from .models import NotificationType

SUPPORTED_NOTIFICATION_TYPES = tuple(NotificationType.values)
DEFAULT_PREFERENCES = {
    notification_type: True for notification_type in SUPPORTED_NOTIFICATION_TYPES
}


@dataclass(frozen=True)
class GuestPrincipal:
    endpoint_id: str | None = None


class NotificationEntitlementPolicy:
    """Future account/plan policy boundary; phase one permits guest record alerts."""

    def may_enable_notification(
        self,
        principal,
        notification_type: str,
        subject=None,
    ) -> bool:
        return notification_type in SUPPORTED_NOTIFICATION_TYPES


def may_enable_notification(principal, notification_type: str, subject=None) -> bool:
    return NotificationEntitlementPolicy().may_enable_notification(
        principal,
        notification_type,
        subject,
    )
