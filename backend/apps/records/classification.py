import json
from functools import lru_cache
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from integrations.wca_live.result_values import is_better, is_complete

from .models import (
    Achievement,
    CanonicalResult,
    PersonalBestBaseline,
    RecordBenchmark,
    RecordValidation,
    ResultIdentityScope,
    ResultObservation,
)
from .qualification import evaluate_result_qualifications

REFERENCE_DATA = Path(__file__).resolve().parents[2] / "reference_data"
RECORD_LEVELS = (
    Achievement.Type.WORLD,
    Achievement.Type.CONTINENTAL,
    Achievement.Type.NATIONAL,
)


@lru_cache(maxsize=1)
def _countries() -> dict:
    with (REFERENCE_DATA / "countries.json").open(encoding="utf-8") as file:
        return json.load(file)["countries"]


def _region_for(result: CanonicalResult, level: str) -> str:
    if level == Achievement.Type.WORLD:
        return ""
    if level == Achievement.Type.NATIONAL:
        return (result.country_code or "").upper()
    return _countries().get((result.country_code or "").upper(), {}).get("continent", "")


def _result_time(result: CanonicalResult):
    return result.entered_at or result.first_observed_at


def _trusted_claims(result: CanonicalResult) -> set[str]:
    return {
        observation.source_record_tag
        for observation in result.observations.all()
        if observation.status == ResultObservation.Status.ACTIVE
        and observation.source_claim_trusted
        and observation.value == result.value
        and observation.source_record_tag in RECORD_LEVELS
    }


def _record_validations(result: CanonicalResult) -> dict[str, RecordValidation]:
    return {
        validation.level: validation
        for validation in result.record_validations.all()
        if validation.validator == RecordValidation.Validator.WCA_RECORDS_API
        and validation.result_value == result.value
    }


def _set_achievement(
    result: CanonicalResult,
    achievement_type: str,
    *,
    reason: str,
    source_claim_supported: bool,
    benchmark_value: int | None,
) -> Achievement:
    achievement, _created = Achievement.objects.update_or_create(
        result=result,
        type=achievement_type,
        defaults={
            "status": Achievement.Status.ACTIVE,
            "classification_reason": reason,
            "source_claim_supported": source_claim_supported,
            "benchmark_value": benchmark_value,
            "classified_at": _result_time(result),
            "invalidated_at": None,
            "details": {},
        },
    )
    return achievement


def _withdraw_missing(result: CanonicalResult, desired: set[str], now) -> None:
    result.achievements.filter(status=Achievement.Status.ACTIVE).exclude(type__in=desired).update(
        status=Achievement.Status.WITHDRAWN, invalidated_at=now
    )


@transaction.atomic
def reclassify_scope(event_id: str, kind: str) -> set[int]:
    """Replay one event/kind from baselines so corrections remain reversible."""

    lock, _created = ResultIdentityScope.objects.get_or_create(
        key=f"classification|{event_id}|{kind}"
    )
    ResultIdentityScope.objects.select_for_update().get(pk=lock.pk)
    results = list(
        CanonicalResult.objects.select_for_update()
        .filter(
            event_id=event_id,
            kind=kind,
            status__in=[CanonicalResult.Status.ACTIVE, CanonicalResult.Status.CORRECTED],
        )
        .prefetch_related("observations", "record_validations")
    )
    results.sort(key=lambda result: (_result_time(result), result.pk))
    now = timezone.now()

    benchmarks = {
        (row.level, row.region_code): row.value
        for row in RecordBenchmark.objects.select_for_update().filter(event_id=event_id, kind=kind)
    }
    effective = dict(benchmarks)
    desired_by_result: dict[int, set[str]] = {result.pk: set() for result in results}

    for result in results:
        if not is_complete(result.value):
            continue
        claims = _trusted_claims(result)
        validations = _record_validations(result)
        if result.validation_status == CanonicalResult.ValidationStatus.REJECTED:
            continue
        for level in RECORD_LEVELS:
            region = _region_for(result, level)
            if level != Achievement.Type.WORLD and not region:
                continue
            key = (level, region)
            incumbent = effective.get(key)
            source_supported = level in claims
            validation = validations.get(level)
            validation_verified = (
                validation is not None
                and validation.status == RecordValidation.Status.VERIFIED
            )
            mathematically_qualified = (
                (validation is None or validation_verified)
                and incumbent is not None
                and is_better(event_id, result.value, incumbent)
            )
            validation_supported = validation_verified and (
                incumbent is None
                or is_better(event_id, result.value, incumbent)
                or result.value == incumbent
            )
            if not source_supported and not mathematically_qualified and not validation_supported:
                continue
            desired_by_result[result.pk].add(level)
            _set_achievement(
                result,
                level,
                reason=(
                    "trusted_source_claim"
                    if source_supported
                    else (
                        "wca_records_api_validation"
                        if validation_supported
                        else "effective_live_benchmark"
                    )
                ),
                source_claim_supported=source_supported,
                benchmark_value=(
                    validation.benchmark_value if validation_supported else incumbent
                ),
            )
            if incumbent is None or is_better(event_id, result.value, incumbent):
                effective[key] = result.value

    personal_bests = {
        row.competitor_wca_id.upper(): row.value
        for row in PersonalBestBaseline.objects.select_for_update().filter(
            event_id=event_id, kind=kind
        )
    }
    for result in results:
        competitor_id = (result.competitor_wca_id or "").upper()
        if (
            not competitor_id
            or not is_complete(result.value)
            or result.validation_status == CanonicalResult.ValidationStatus.REJECTED
        ):
            continue
        incumbent = personal_bests.get(competitor_id)
        if incumbent is not None and is_better(event_id, result.value, incumbent):
            desired_by_result[result.pk].add(Achievement.Type.PERSONAL)
            _set_achievement(
                result,
                Achievement.Type.PERSONAL,
                reason="effective_personal_best",
                source_claim_supported=False,
                benchmark_value=incumbent,
            )
            personal_bests[competitor_id] = result.value

    eligible_ids: set[int] = set()
    for result in results:
        _withdraw_missing(result, desired_by_result[result.pk], now)
        eligible_ids.update(
            achievement.pk for achievement in evaluate_result_qualifications(result)
        )

    if eligible_ids:
        from apps.notifications.services import publish_achievements_after_commit

        publish_achievements_after_commit(eligible_ids)
    return eligible_ids


def reclassify_all() -> None:
    scopes = CanonicalResult.objects.values_list("event_id", "kind").distinct()
    for event_id, kind in scopes:
        reclassify_scope(event_id, kind)
