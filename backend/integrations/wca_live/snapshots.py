import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from django.utils.dateparse import parse_datetime

from .exceptions import WCALivePayloadError
from .schemas import NormalizedRoundResult


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise WCALivePayloadError(f"Invalid timezone-aware WCA Live datetime: {value!r}")
    return parsed


def _int_or_none(value) -> int | None:
    return None if value is None else int(value)


def normalize_round_result(payload: dict) -> NormalizedRoundResult:
    try:
        result_id = str(payload["id"])
        person = payload["person"]
        attempts = tuple(int(attempt["result"]) for attempt in payload.get("attempts", []))
        normalized = {
            "result_id": result_id,
            "competitor_wca_live_id": str(person["id"]),
            "competitor_wca_id": person.get("wcaId") or "",
            "competitor_name": person.get("name") or "",
            "country_code": person.get("country", {}).get("iso2", ""),
            "attempts": list(attempts),
            "best": _int_or_none(payload.get("best")),
            "average": _int_or_none(payload.get("average")),
            "single_record_tag": (payload.get("singleRecordTag") or "").upper(),
            "average_record_tag": (payload.get("averageRecordTag") or "").upper(),
            "entered_at": payload.get("enteredAt"),
        }
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return NormalizedRoundResult(
            result_id=result_id,
            stable_result_identity=result_id,
            competitor_wca_live_id=normalized["competitor_wca_live_id"],
            competitor_wca_id=normalized["competitor_wca_id"],
            competitor_name=normalized["competitor_name"],
            country_code=normalized["country_code"],
            attempts=attempts,
            best=normalized["best"],
            average=normalized["average"],
            single_record_tag=normalized["single_record_tag"],
            average_record_tag=normalized["average_record_tag"],
            entered_at=_datetime(normalized["entered_at"]),
            meaningful_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            payload=normalized,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WCALivePayloadError(f"Invalid WCA Live round result: {exc}") from exc


def normalize_round_snapshot(payload: dict) -> dict[str, NormalizedRoundResult]:
    try:
        rows = payload["results"]
    except (KeyError, TypeError) as exc:
        raise WCALivePayloadError(f"Invalid WCA Live round snapshot: {exc}") from exc
    normalized = {}
    for row in rows:
        result = normalize_round_result(row)
        normalized[result.result_id] = result
    return normalized


@dataclass(frozen=True)
class SnapshotDiff:
    additions: tuple[str, ...]
    changes: tuple[str, ...]
    removals: tuple[str, ...]
    unchanged: tuple[str, ...]


def diff_snapshots(
    previous: dict[str, NormalizedRoundResult], current: dict[str, NormalizedRoundResult]
) -> SnapshotDiff:
    previous_ids = set(previous)
    current_ids = set(current)
    shared = previous_ids & current_ids
    changes = tuple(sorted(key for key in shared if previous[key].meaningful_hash != current[key].meaningful_hash))
    unchanged = tuple(sorted(shared - set(changes)))
    return SnapshotDiff(
        additions=tuple(sorted(current_ids - previous_ids)),
        changes=changes,
        removals=tuple(sorted(previous_ids - current_ids)),
        unchanged=unchanged,
    )
