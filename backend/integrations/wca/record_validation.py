import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
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


def _changed_record_scopes(
    previous: dict[str, int], current: dict[str, int]
) -> set[tuple[str, str]]:
    """Return event/kind scopes affected by added, changed, or removed records."""

    changed = set()
    for key in previous.keys() | current.keys():
        if previous.get(key) == current.get(key):
            continue
        _level, event_id, kind, _region = key.split("|", 3)
        changed.add((event_id, kind))
    return changed


def _relevant_changed_scopes(scopes: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Limit refresh work to changed scopes with results or stored validations."""

    if not scopes:
        return set()
    scope_filter = Q()
    for event_id, kind in scopes:
        scope_filter |= Q(event_id=event_id, kind=kind)
    active_cubingchina = ResultObservation.objects.filter(
        canonical_result_id=OuterRef("pk"),
        source=ResultObservation.Source.CUBINGCHINA,
        status=ResultObservation.Status.ACTIVE,
    )
    existing_wca_validation = RecordValidation.objects.filter(
        result_id=OuterRef("pk"),
        validator=RecordValidation.Validator.WCA_RECORDS_API,
    )
    return set(
        CanonicalResult.objects.filter(scope_filter)
        .annotate(
            has_active_cubingchina=Exists(active_cubingchina),
            has_wca_validation=Exists(existing_wca_validation),
        )
        .filter(Q(has_active_cubingchina=True) | Q(has_wca_validation=True))
        .order_by()
        .values_list("event_id", "kind")
        .distinct()
    )


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
            parsed_events = _record_values(event_records, context=f"{source_key}/{region}")
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
    # Some records-API keys differ from both the WCA registration name and the
    # public display name (for example "USA", "Korea", and "Cote d_Ivoire").
    return (
        country.get("wca_records_name")
        or country.get("display_name")
        or country.get("wca_name", "")
    )


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
        status = RecordValidation.Status.VERIFIED if matches else RecordValidation.Status.REJECTED
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
    result.record_validations.filter(validator=RecordValidation.Validator.WCA_RECORDS_API).exclude(
        level__in=present_levels
    ).delete()

    if not trusted and result.validation_reason != "trusted_source_disagreement":
        claims = set(
            result.observations.filter(
                source=ResultObservation.Source.CUBINGCHINA,
                status=ResultObservation.Status.ACTIVE,
                value=result.value,
            )
            .exclude(source_record_tag="")
            .values_list("source_record_tag", flat=True)
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


def validate_scope_against_latest_snapshot(event_id: str, kind: str) -> int:
    """Refresh stored WCA validation for relevant results in one dirty scope.

    This never performs an HTTP request. The official endpoint is fetched by the
    separate snapshot refresh job; classification only reads its latest stored row.
    """

    snapshot = WCARecordSnapshot.objects.order_by("-last_fetched_at", "-pk").first()
    if snapshot is None:
        return 0
    active_cubingchina = ResultObservation.objects.filter(
        canonical_result_id=OuterRef("pk"),
        source=ResultObservation.Source.CUBINGCHINA,
        status=ResultObservation.Status.ACTIVE,
    )
    existing_wca_validation = RecordValidation.objects.filter(
        result_id=OuterRef("pk"),
        validator=RecordValidation.Validator.WCA_RECORDS_API,
    )
    relevant_observations = (
        ResultObservation.objects.filter(status=ResultObservation.Status.ACTIVE)
        .filter(
            Q(source=ResultObservation.Source.CUBINGCHINA)
            | Q(result_evidence_trusted=True)
        )
        .order_by()
    )
    results = list(
        CanonicalResult.objects.filter(event_id=event_id, kind=kind)
        .annotate(
            has_active_cubingchina=Exists(active_cubingchina),
            has_wca_validation=Exists(existing_wca_validation),
        )
        .filter(Q(has_active_cubingchina=True) | Q(has_wca_validation=True))
        .prefetch_related(
            Prefetch(
                "observations",
                queryset=relevant_observations,
                to_attr="validation_observations",
            )
        )
        .order_by("pk")
    )
    if not results:
        return 0

    checked_at = timezone.now()
    result_ids = [result.pk for result in results]
    existing = list(
        RecordValidation.objects.filter(
            result_id__in=result_ids,
            validator=RecordValidation.Validator.WCA_RECORDS_API,
        )
    )
    existing_by_key = {(row.result_id, row.level): row for row in existing}
    desired_keys = set()
    creates = []
    updates = []
    result_updates = []

    for result in results:
        observations = result.validation_observations
        cubingchina_current = [
            row
            for row in observations
            if row.source == ResultObservation.Source.CUBINGCHINA
            and row.status == ResultObservation.Status.ACTIVE
            and row.value == result.value
        ]
        if not cubingchina_current or not is_complete(result.value):
            continue
        trusted = any(
            row.status == ResultObservation.Status.ACTIVE
            and row.result_evidence_trusted
            and row.value == result.value
            for row in observations
        )
        claims = {row.source_record_tag for row in cubingchina_current if row.source_record_tag}
        verified = set()
        comparisons = {}
        for level, api_region in _validation_regions(result):
            benchmark = snapshot.records.get(_key(level, result.event_id, result.kind, api_region))
            if benchmark is None:
                continue
            matches = comparison_key(result.event_id, result.value) <= comparison_key(
                result.event_id, benchmark
            )
            status = (
                RecordValidation.Status.VERIFIED if matches else RecordValidation.Status.REJECTED
            )
            if matches:
                verified.add(level)
            comparisons[level] = status
            desired_keys.add((result.pk, level))
            values = {
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
            }
            validation = existing_by_key.get((result.pk, level))
            if validation is None:
                creates.append(
                    RecordValidation(
                        result=result,
                        validator=RecordValidation.Validator.WCA_RECORDS_API,
                        level=level,
                        **values,
                    )
                )
            else:
                for field, value in values.items():
                    setattr(validation, field, value)
                validation.updated_at = checked_at
                updates.append(validation)

        if not trusted and result.validation_reason != "trusted_source_disagreement":
            if verified:
                status = CanonicalResult.ValidationStatus.VERIFIED
                reason = "wca_records_api_record_match"
            elif claims.intersection(comparisons):
                status = CanonicalResult.ValidationStatus.REJECTED
                reason = "wca_records_api_record_mismatch"
            else:
                status = CanonicalResult.ValidationStatus.PENDING
                reason = "wca_records_api_no_record_match"
            if result.validation_status != status or result.validation_reason != reason:
                result.validation_status = status
                result.validation_reason = reason
                result.updated_at = checked_at
                result_updates.append(result)

    stale_ids = [row.pk for row in existing if (row.result_id, row.level) not in desired_keys]
    if stale_ids:
        RecordValidation.objects.filter(pk__in=stale_ids).delete()
    if creates:
        RecordValidation.objects.bulk_create(creates)
    if updates:
        RecordValidation.objects.bulk_update(
            updates,
            [
                "snapshot",
                "region_code",
                "result_value",
                "benchmark_value",
                "status",
                "reason",
                "checked_at",
                "details",
                "updated_at",
            ],
        )
    if result_updates:
        CanonicalResult.objects.bulk_update(
            result_updates,
            ["validation_status", "validation_reason", "updated_at"],
        )
    return len(results)


@transaction.atomic
def refresh_wca_record_validations(payload: dict, *, source_url: str, fetched_at=None):
    from apps.records.classification_work import mark_classification_scopes_dirty

    previous = WCARecordSnapshot.objects.order_by("-last_fetched_at", "-pk").first()
    snapshot = store_wca_record_snapshot(payload, source_url=source_url, fetched_at=fetched_at)
    scopes = _relevant_changed_scopes(
        _changed_record_scopes(previous.records if previous else {}, snapshot.records)
    )
    mark_classification_scopes_dirty(
        scopes,
        observed_at=fetched_at or timezone.now(),
    )
    return snapshot
