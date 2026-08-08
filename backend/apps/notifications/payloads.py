import hashlib
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .models import NotificationEvent, NotificationType

LEVEL_TO_NOTIFICATION_TYPE = {
    "WR": NotificationType.RECORD_WR,
    "CR": NotificationType.RECORD_CR,
    "NR": NotificationType.RECORD_NR,
}
REFERENCE_DATA = Path(__file__).resolve().parents[2] / "reference_data"
CONTINENT_ICON_KEYS = {
    "Africa": "AfR",
    "Asia": "AsR",
    "Europe": "ER",
    "North America": "NAR",
    "South America": "SAR",
    "Oceania": "OcR",
}


def validate_relative_target_url(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise ValueError("Notification target must be a same-origin relative path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Notification target must be a same-origin relative path")
    return value


@lru_cache(maxsize=1)
def _reference_data() -> tuple[dict, dict]:
    with (REFERENCE_DATA / "events.json").open(encoding="utf-8") as file:
        events = json.load(file)["events"]
    with (REFERENCE_DATA / "countries.json").open(encoding="utf-8") as file:
        countries = json.load(file)["countries"]
    return events, countries


def _country(code: str) -> dict:
    return _reference_data()[1].get((code or "").upper(), {})


def _competition_country_code(record) -> str:
    explicit = getattr(record, "competition_country_code", "")
    if explicit:
        return explicit
    try:
        competition = record.source_payload["result"]["round"]["competitionEvent"][
            "competition"
        ]
        venues = competition.get("venues") or []
        return venues[0].get("country", {}).get("iso2", "") if venues else ""
    except (KeyError, TypeError):
        return ""


def _notification_icon(record_level: str, country_code: str) -> str:
    level_label = _level_label(record_level, country_code)
    if level_label == "CR":
        return "/icons/icon-192.png"
    return f"/notification_icons/notification_icon_{level_label}.png"


def _level_label(record_level: str, country_code: str) -> str:
    if record_level != "CR":
        return record_level
    return CONTINENT_ICON_KEYS.get(_country(country_code).get("continent"), "CR")


def build_record_payload(
    *,
    event: NotificationEvent,
    record,
    target_url: str,
    test: bool = False,
) -> dict:
    target_url = validate_relative_target_url(target_url)
    events, _countries = _reference_data()
    event_name = events.get(record.event_id, {}).get("short_name") or record.event_name
    competitor_country = _country(record.country_code)
    competitor_country_name = competitor_country.get("display_name") or record.country_code
    level_label = _level_label(record.record_level, record.country_code)
    competition_country_code = _competition_country_code(record)
    competition_country_name = (
        _country(competition_country_code).get("display_name") or competition_country_code
    )
    prefix = "[TEST] " if test else ""
    return {
        "schema_version": 1,
        "notification_event_id": str(event.id),
        "notification_type": event.notification_type,
        "title": (
            f"{prefix}{event_name} {level_label} {record.kind}: {record.formatted_result}"
        ),
        "body": (
            f"By {record.competitor_name} from {competitor_country_name} "
            f"at {record.competition_name} in {competition_country_name}"
        ),
        "target_url": target_url,
        "tag": "record:" + hashlib.sha256(event.deduplication_key.encode("utf-8")).hexdigest()[:32],
        "icon": _notification_icon(record.record_level, record.country_code),
        "badge": "/icons/badge-96.png",
        "record_level": record.record_level,
        "event_id": record.event_id,
        "formatted_result": record.formatted_result,
        "kind": record.kind,
        "competitor_name": record.competitor_name,
        "country_code": record.country_code,
        "competition_name": record.competition_name,
        "competition_country_code": competition_country_code,
        "detected_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
    }
