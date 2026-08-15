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
    QualificationDecision,
    RecordBenchmark,
    RecordValidation,
    ResultIdentityScope,
    ResultObservation,
)
from .qualification import DISPLAY_PRECEDENCE, NOTIFIABLE_TYPES

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


def _set_if_changed(instance, field: str, value) -> bool:
    if getattr(instance, field) == value:
        return False
    setattr(instance, field, value)
    return True


def _persist_achievements(
    results: list[CanonicalResult],
    desired: dict[tuple[int, str], dict],
    event_id: str,
    kind: str,
    now,
) -> list[Achievement]:
    existing = list(
        Achievement.objects.filter(
            result__event_id=event_id,
            result__kind=kind,
        ).order_by("pk")
    )
    existing_by_key = {(row.result_id, row.type): row for row in existing}
    results_by_id = {result.pk: result for result in results}
    creates = []
    updates = []
    for key, spec in desired.items():
        achievement = existing_by_key.get(key)
        if achievement is None:
            creates.append(
                Achievement(
                    result_id=key[0],
                    type=key[1],
                    status=Achievement.Status.ACTIVE,
                    classification_reason=spec["reason"],
                    source_claim_supported=spec["source_claim_supported"],
                    benchmark_value=spec["benchmark_value"],
                    classified_at=_result_time(results_by_id[key[0]]),
                    invalidated_at=None,
                    details={},
                )
            )
            continue
        changed = False
        changed |= _set_if_changed(achievement, "status", Achievement.Status.ACTIVE)
        changed |= _set_if_changed(achievement, "classification_reason", spec["reason"])
        changed |= _set_if_changed(
            achievement,
            "source_claim_supported",
            spec["source_claim_supported"],
        )
        changed |= _set_if_changed(achievement, "benchmark_value", spec["benchmark_value"])
        changed |= _set_if_changed(
            achievement,
            "classified_at",
            _result_time(results_by_id[key[0]]),
        )
        changed |= _set_if_changed(achievement, "invalidated_at", None)
        changed |= _set_if_changed(achievement, "details", {})
        if changed:
            achievement.updated_at = now
            updates.append(achievement)

    for achievement in existing:
        if (
            achievement.result_id,
            achievement.type,
        ) not in desired and achievement.status != Achievement.Status.WITHDRAWN:
            achievement.status = Achievement.Status.WITHDRAWN
            achievement.invalidated_at = now
            achievement.updated_at = now
            updates.append(achievement)

    if creates:
        Achievement.objects.bulk_create(creates)
    if updates:
        Achievement.objects.bulk_update(
            updates,
            [
                "status",
                "classification_reason",
                "source_claim_supported",
                "benchmark_value",
                "classified_at",
                "invalidated_at",
                "details",
                "updated_at",
            ],
        )
    return list(
        Achievement.objects.filter(
            result__event_id=event_id,
            result__kind=kind,
        ).order_by("pk")
    )


def _persist_qualifications(
    results: list[CanonicalResult],
    achievements: list[Achievement],
    now,
) -> set[int]:
    results_by_id = {result.pk: result for result in results}
    achievements_by_result: dict[int, list[Achievement]] = {}
    for achievement in achievements:
        achievements_by_result.setdefault(achievement.result_id, []).append(achievement)

    existing = list(
        QualificationDecision.objects.filter(achievement_id__in=[row.pk for row in achievements])
    )
    existing_by_achievement = {row.achievement_id: row for row in existing}
    previously_eligible = {row.achievement_id for row in existing if row.notification_eligible}
    creates = []
    updates = []
    currently_eligible: set[int] = set()

    for result_id, result_achievements in achievements_by_result.items():
        result = results_by_id.get(result_id)
        trusted_result = bool(
            result
            and result.validation_status == CanonicalResult.ValidationStatus.VERIFIED
            and result.validation_reason == "trusted_source_observation"
        )
        independently_validated = (
            {
                validation.level
                for validation in result.record_validations.all()
                if validation.validator == RecordValidation.Validator.WCA_RECORDS_API
                and validation.result_value == result.value
                and validation.status == RecordValidation.Status.VERIFIED
            }
            if result
            else set()
        )
        verified_active = [
            achievement
            for achievement in result_achievements
            if achievement.status == Achievement.Status.ACTIVE
            and (trusted_result or achievement.type in independently_validated)
        ]
        visible = min(
            verified_active,
            key=lambda item: DISPLAY_PRECEDENCE[item.type],
            default=None,
        )
        for achievement in result_achievements:
            verified = trusted_result or achievement.type in independently_validated
            is_active = achievement.status == Achievement.Status.ACTIVE
            show = is_active and verified and achievement.pk == getattr(visible, "pk", None)
            notify = show and achievement.type in NOTIFIABLE_TYPES
            if not is_active:
                homepage_reason = notification_reason = "achievement_withdrawn"
            elif not verified:
                homepage_reason = notification_reason = (
                    result.validation_reason if result else "result_not_verified"
                ) or "result_not_verified"
            elif not show:
                homepage_reason = notification_reason = "superseded_by_higher_achievement"
            else:
                homepage_reason = "eligible"
                notification_reason = "eligible" if notify else "notification_type_not_supported"
            values = {
                "show_on_homepage": show,
                "homepage_category": achievement.type if show else "",
                "notification_eligible": notify,
                "homepage_reason": homepage_reason,
                "notification_reason": notification_reason,
                "evaluated_at": now,
            }
            decision = existing_by_achievement.get(achievement.pk)
            if decision is None:
                creates.append(QualificationDecision(achievement=achievement, **values))
            else:
                for field, value in values.items():
                    setattr(decision, field, value)
                decision.updated_at = now
                updates.append(decision)
            if notify:
                currently_eligible.add(achievement.pk)

    if creates:
        QualificationDecision.objects.bulk_create(creates)
    if updates:
        QualificationDecision.objects.bulk_update(
            updates,
            [
                "show_on_homepage",
                "homepage_category",
                "notification_eligible",
                "homepage_reason",
                "notification_reason",
                "evaluated_at",
                "updated_at",
            ],
        )
    return currently_eligible - previously_eligible


@transaction.atomic
def reclassify_scope(
    event_id: str,
    kind: str,
    *,
    publish_notifications: bool = True,
) -> set[int]:
    """Compute one scope in memory and persist its complete diff in bulk."""

    lock, _created = ResultIdentityScope.objects.get_or_create(
        key=f"classification|{event_id}|{kind}"
    )
    ResultIdentityScope.objects.select_for_update().get(pk=lock.pk)
    results = list(
        CanonicalResult.objects.filter(
            event_id=event_id,
            kind=kind,
            status__in=[CanonicalResult.Status.ACTIVE, CanonicalResult.Status.CORRECTED],
        ).prefetch_related("observations", "record_validations")
    )
    results.sort(key=lambda result: (_result_time(result), result.pk))
    now = timezone.now()

    benchmarks = {
        (row.level, row.region_code): row.value
        for row in RecordBenchmark.objects.filter(event_id=event_id, kind=kind)
    }
    effective = dict(benchmarks)
    desired: dict[tuple[int, str], dict] = {}

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
                validation is not None and validation.status == RecordValidation.Status.VERIFIED
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
            desired[(result.pk, level)] = {
                "reason": (
                    "trusted_source_claim"
                    if source_supported
                    else (
                        "wca_records_api_validation"
                        if validation_supported
                        else "effective_live_benchmark"
                    )
                ),
                "source_claim_supported": source_supported,
                "benchmark_value": (
                    validation.benchmark_value if validation_supported else incumbent
                ),
            }
            if incumbent is None or is_better(event_id, result.value, incumbent):
                effective[key] = result.value

    personal_bests = {
        row.competitor_wca_id.upper(): row.value
        for row in PersonalBestBaseline.objects.filter(event_id=event_id, kind=kind)
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
            desired[(result.pk, Achievement.Type.PERSONAL)] = {
                "reason": "effective_personal_best",
                "source_claim_supported": False,
                "benchmark_value": incumbent,
            }
            personal_bests[competitor_id] = result.value

    achievements = _persist_achievements(results, desired, event_id, kind, now)
    newly_eligible_ids = _persist_qualifications(results, achievements, now)

    if newly_eligible_ids and publish_notifications:
        from apps.notifications.services import publish_achievements_after_commit

        publish_achievements_after_commit(newly_eligible_ids)
    return newly_eligible_ids


def reclassify_all(*, publish_notifications: bool = True) -> None:
    scopes = CanonicalResult.objects.values_list("event_id", "kind").distinct()
    for event_id, kind in scopes:
        reclassify_scope(
            event_id,
            kind,
            publish_notifications=publish_notifications,
        )
