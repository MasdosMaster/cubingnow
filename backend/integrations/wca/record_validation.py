import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.records.models import (
    Achievement,
    CanonicalResult,
    RecordValidation,
    ResultObservation,
    WCARecordSnapshot,
)
from integrations.wca_live.result_values import comparison_key, is_complete

from .api_client import WCAAPIClient

RECORDS_PATH = "/api/v0/records"
VALID_KINDS = {CanonicalResult.Kind.SINGLE, CanonicalResult.Kind.AVERAGE}


@dataclass(frozen=True)
class ParsedWCARecords:
    records: dict[str, int]

    @property
    def count(self) -> int:
        return len(self.records)


def _key(level: str, event_id: str, kind: str, region: str = "") -> str:
    return f"{level}|{event_id}|{kind}|{region}"


def _record_values(container, *, context: str) -> dict[str, dict[str, int]]:
    if not isinstance(container, dict):
        raise TypeError(f"WCA records payload {context} must be an object")
    parsed = {}
    for event_id, values in container.items():
        if not isinstance(event_id, str) or not isinstance(values, dict):
            raise TypeError(f"WCA records payload has an invalid {context} event")
        event_values = {}
        for kind, value in values.items():
            if kind not in VALID_KINDS or not isinstance(value, int) or value <= 0:
                raise ValueError(
                    f"WCA records payload has invalid {context} value for {event_id}/{kind}"
                )
            event_values[kind] = value
        parsed[event_id] = event_values
    return parsed


def parse_wca_records(payload: dict) -> ParsedWCARecords:
    if not isinstance(payload, dict):
        raise TypeError("WCA records payload must be an object")
    required = {"world_records", "continental_records", "national_records"}
    if not required.issubset(payload):
        raise ValueError("WCA records payload is missing a required record map")

    records = {}
    for event_id, values in _record_values(
        payload["world_records"], context="world_records"
    ).items():
        for kind, value in values.items():
            records[_key(Achievement.Type.WORLD, event_id, kind)] = value

    for source_key, level in (
        ("continental_records", Achievement.Type.CONTINENTAL),
        ("national_records", Achievement.Type.NATIONAL),
    ):
        regions = payload[source_key]
        if not isinstance(regions, dict):
            raise TypeError(f"WCA records payload {source_key} must be an object")
        for region, event_records in regions.items():
            if not isinstance(region, str) or not region:
                raise ValueError(f"WCA records payload has an invalid {source_key} region")
            parsed_events = _record_values(
                event_records, context=f"{source_key}/{region}"
            )
            for event_id, values in parsed_events.items():
                for kind, value in values.items():
                    records[_key(level, event_id, kind, region)] = value
    return ParsedWCARecords(records=records)


def fetch_wca_records(base_url: str, timeout: float = 30.0) -> dict:
    with WCAAPIClient(base_url=base_url, timeout=timeout) as client:
        return client.get_json(RECORDS_PATH)


@transaction.atomic
def store_wca_record_snapshot(payload: dict, *, source_url: str, fetched_at=None):
    fetched_at = fetched_at or timezone.now()
    parsed = parse_wca_records(payload)
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot, created = WCARecordSnapshot.objects.get_or_create(
        payload_hash=payload_hash,
        defaults={
            "source_url": source_url,
            "records": parsed.records,
            "record_count": parsed.count,
            "first_fetched_at": fetched_at,
            "last_fetched_at": fetched_at,
        },
    )
    if not created:
        snapshot.last_fetched_at = fetched_at
        snapshot.save(update_fields=["last_fetched_at"])
    return snapshot


def _country_name(result: CanonicalResult) -> str:
    from apps.records.classification import _countries

    country = _countries().get((result.country_code or "").upper(), {})
    return country.get("wca_name", "")


def _validation_regions(result: CanonicalResult):
    from apps.records.classification import _region_for

    yield Achievement.Type.WORLD, ""
    continent = _region_for(result, Achievement.Type.CONTINENTAL)
    if continent:
        yield Achievement.Type.CONTINENTAL, f"_{continent}"
    country = _country_name(result)
    if country:
        yield Achievement.Type.NATIONAL, country


def _has_current_cubingchina_evidence(result: CanonicalResult) -> bool:
    return result.observations.filter(
        source=ResultObservation.Source.CUBINGCHINA,
        status=ResultObservation.Status.ACTIVE,
        value=result.value,
    ).exists()


@transaction.atomic
def validate_result_against_snapshot(
    result: CanonicalResult, snapshot: WCARecordSnapshot, *, checked_at=None
) -> set[str]:
    checked_at = checked_at or timezone.now()
    result = CanonicalResult.objects.select_for_update().get(pk=result.pk)
    trusted = result.observations.filter(
        status=ResultObservation.Status.ACTIVE,
        result_evidence_trusted=True,
        value=result.value,
    ).exists()
    if not _has_current_cubingchina_evidence(result) or not is_complete(result.value):
        result.record_validations.all().delete()
        return set()

    verified = set()
    present_levels = set()
    comparisons = {}
    for level, api_region in _validation_regions(result):
        benchmark = snapshot.records.get(_key(level, result.event_id, result.kind, api_region))
        if benchmark is None:
            continue
        present_levels.add(level)
        matches = comparison_key(result.event_id, result.value) <= comparison_key(
            result.event_id, benchmark
        )
        status = (
            RecordValidation.Status.VERIFIED
            if matches
            else RecordValidation.Status.REJECTED
        )
        if matches:
            verified.add(level)
        comparisons[level] = status
        RecordValidation.objects.update_or_create(
            result=result,
            validator=RecordValidation.Validator.WCA_RECORDS_API,
            level=level,
            defaults={
                "snapshot": snapshot,
                "region_code": api_region,
                "result_value": result.value,
                "benchmark_value": benchmark,
                "status": status,
                "reason": (
                    "meets_official_wca_record_benchmark"
                    if matches
                    else "does_not_meet_official_wca_record_benchmark"
                ),
                "checked_at": checked_at,
                "details": {"event_id": result.event_id, "kind": result.kind},
            },
        )
    result.record_validations.filter(
        validator=RecordValidation.Validator.WCA_RECORDS_API
    ).exclude(level__in=present_levels).delete()

    if not trusted and result.validation_reason != "trusted_source_disagreement":
        claims = set(
            result.observations.filter(
                source=ResultObservation.Source.CUBINGCHINA,
                status=ResultObservation.Status.ACTIVE,
                value=result.value,
            ).exclude(source_record_tag="").values_list("source_record_tag", flat=True)
        )
        if verified:
            result.validation_status = CanonicalResult.ValidationStatus.VERIFIED
            result.validation_reason = "wca_records_api_record_match"
        elif claims.intersection(comparisons):
            result.validation_status = CanonicalResult.ValidationStatus.REJECTED
            result.validation_reason = "wca_records_api_record_mismatch"
        else:
            result.validation_status = CanonicalResult.ValidationStatus.PENDING
            result.validation_reason = "wca_records_api_no_record_match"
        result.save(update_fields=["validation_status", "validation_reason", "updated_at"])
    return verified


def validate_result_against_latest_snapshot(result: CanonicalResult) -> set[str]:
    snapshot = WCARecordSnapshot.objects.order_by("-last_fetched_at", "-pk").first()
    if snapshot is None:
        return set()
    return validate_result_against_snapshot(result, snapshot)


def refresh_wca_record_validations(payload: dict, *, source_url: str, fetched_at=None):
    from apps.records.classification import reclassify_scope

    snapshot = store_wca_record_snapshot(
        payload, source_url=source_url, fetched_at=fetched_at
    )
    results = CanonicalResult.objects.filter(
        observations__source=ResultObservation.Source.CUBINGCHINA,
        observations__status=ResultObservation.Status.ACTIVE,
    ).distinct()
    scopes = set()
    for result in results.iterator():
        validate_result_against_snapshot(result, snapshot, checked_at=fetched_at)
        scopes.add((result.event_id, result.kind))
    for event_id, kind in scopes:
        reclassify_scope(event_id, kind)
    return snapshot
