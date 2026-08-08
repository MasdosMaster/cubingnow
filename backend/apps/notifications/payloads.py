import hashlib
from urllib.parse import urlsplit

from .models import NotificationEvent, NotificationType

LEVEL_TO_NOTIFICATION_TYPE = {
    "WR": NotificationType.RECORD_WR,
    "CR": NotificationType.RECORD_CR,
    "NR": NotificationType.RECORD_NR,
}
LEVEL_LABELS = {
    "WR": "World Record",
    "CR": "Continental Record",
    "NR": "NR",
}


def validate_relative_target_url(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise ValueError("Notification target must be a same-origin relative path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Notification target must be a same-origin relative path")
    return value


def _event_display_name(name: str, event_id: str) -> str:
    display = (name or event_id).replace("x", "×")
    return display.removesuffix(" Cube")


def build_record_payload(
    *,
    event: NotificationEvent,
    record,
    target_url: str,
    test: bool = False,
) -> dict:
    target_url = validate_relative_target_url(target_url)
    level_label = LEVEL_LABELS[record.record_level]
    event_name = _event_display_name(record.event_name, record.event_id)
    country_suffix = (
        f" ({record.country_code})"
        if record.record_level == "NR" and record.country_code
        else ""
    )
    prefix = "[TEST] " if test else ""
    return {
        "schema_version": 1,
        "notification_event_id": str(event.id),
        "notification_type": event.notification_type,
        "title": f"{prefix}New {event_name} {level_label}{country_suffix}",
        "body": (
            f"{record.competitor_name} — {record.formatted_result} "
            f"{record.kind} at {record.competition_name}"
        ),
        "target_url": target_url,
        "tag": "record:" + hashlib.sha256(event.deduplication_key.encode("utf-8")).hexdigest()[:32],
        "icon": "/icons/icon-192.png",
        "badge": "/icons/badge-96.png",
        "record_level": record.record_level,
        "event_id": record.event_id,
        "formatted_result": record.formatted_result,
        "kind": record.kind,
        "competitor_name": record.competitor_name,
        "country_code": record.country_code,
        "competition_name": record.competition_name,
        "detected_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
    }
