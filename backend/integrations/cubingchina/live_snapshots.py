import hashlib
import json

from apps.competitors.geography import country_code_from_name

from .live_schemas import CubingChinaSnapshotDiff, NormalizedCubingChinaResult


class CubingChinaPayloadError(ValueError):
    """Raised when a CubingChina live-results payload cannot be normalized safely."""


CONTINENTAL_RECORD_TAGS = {"AFR", "ASR", "ER", "NAR", "OCR", "SAR"}


def normalize_record_tag(value) -> str:
    tag = str(value or "").upper()
    return "CR" if tag in CONTINENTAL_RECORD_TAGS else tag


def _country_code(region: str) -> str:
    if not region:
        return ""
    try:
        return country_code_from_name(region)
    except ValueError:
        return ""


def normalize_result(
    payload: dict,
    users: dict,
    competition_id: int,
    expected_event_id: str | None = None,
    expected_round_id: str | None = None,
) -> NormalizedCubingChinaResult:
    try:
        if int(payload["c"]) != competition_id:
            raise CubingChinaPayloadError("result belongs to an unexpected competition")
        event_id = str(payload["e"])
        round_id = str(payload["r"])
        if expected_event_id is not None and event_id != expected_event_id:
            raise CubingChinaPayloadError("result belongs to an unexpected event")
        if expected_round_id is not None and round_id != expected_round_id:
            raise CubingChinaPayloadError("result belongs to an unexpected round")
        result_id = str(payload["i"])
        competitor_number = int(payload["n"])
        user = users.get(str(competitor_number)) or users.get(competitor_number) or {}
        region = str(user.get("region") or "")
        attempts = tuple(int(value) for value in payload.get("v", []))
        normalized = {
            "result_id": result_id,
            "competition_id": competition_id,
            "competitor_number": competitor_number,
            "competitor_name": str(user.get("name") or ""),
            "competitor_wca_id": str(user.get("wcaid") or "").upper(),
            "region": region,
            "country_code": _country_code(region),
            "event_id": event_id,
            "round_id": round_id,
            "format": str(payload.get("f") or ""),
            "attempts": list(attempts),
            "best": int(payload["b"]) if payload.get("b") is not None else None,
            "average": int(payload["a"]) if payload.get("a") is not None else None,
            "single_record_tag": normalize_record_tag(payload.get("sr")),
            "average_record_tag": normalize_record_tag(payload.get("ar")),
        }
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return NormalizedCubingChinaResult(
            result_id=result_id,
            stable_result_identity=f"cubingchina:{competition_id}:{result_id}",
            competitor_number=competitor_number,
            competitor_name=normalized["competitor_name"],
            competitor_wca_id=normalized["competitor_wca_id"],
            region=region,
            country_code=normalized["country_code"],
            attempts=attempts,
            best=normalized["best"],
            average=normalized["average"],
            single_record_tag=normalized["single_record_tag"],
            average_record_tag=normalized["average_record_tag"],
            meaningful_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            payload=normalized,
        )
    except CubingChinaPayloadError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CubingChinaPayloadError(f"Invalid CubingChina result payload: {exc}") from exc


def normalize_snapshot(
    rows: list[dict],
    users: dict,
    competition_id: int,
    event_id: str,
    round_id: str,
) -> dict[str, NormalizedCubingChinaResult]:
    if not isinstance(rows, list):
        raise CubingChinaPayloadError("CubingChina round snapshot data must be a list")
    normalized = {}
    for row in rows:
        result = normalize_result(
            row,
            users,
            competition_id,
            expected_event_id=event_id,
            expected_round_id=round_id,
        )
        normalized[result.result_id] = result
    return normalized


def diff_snapshots(previous, current) -> CubingChinaSnapshotDiff:
    previous_ids = set(previous)
    current_ids = set(current)
    shared = previous_ids & current_ids
    changes = tuple(
        sorted(
            result_id
            for result_id in shared
            if previous[result_id].meaningful_hash != current[result_id].meaningful_hash
        )
    )
    unchanged = tuple(sorted(shared - set(changes)))
    return CubingChinaSnapshotDiff(
        additions=tuple(sorted(current_ids - previous_ids)),
        changes=changes,
        removals=tuple(sorted(previous_ids - current_ids)),
        unchanged=unchanged,
    )
