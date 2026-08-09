import logging

from django.db import IntegrityError, transaction

from integrations.wca.record_validation import validate_result_against_latest_snapshot
from integrations.wca_live.result_values import format_result

from .classification import reclassify_scope
from .domain import NormalizedResultObservation
from .models import CanonicalResult, ResultIdentityScope, ResultObservation
from .source_trust import (
    record_claim_is_trusted,
    result_evidence_is_trusted,
    result_trust_rank,
)

logger = logging.getLogger(__name__)


def _natural_candidates(data: NormalizedResultObservation):
    if data.natural_result_prefix is None:
        return CanonicalResult.objects.none()
    return CanonicalResult.objects.filter(
        wca_competition_id__iexact=data.wca_competition_id,
        competitor_wca_id__iexact=data.competitor_wca_id,
        event_id=data.event_id,
        round_number=data.round_number,
        kind=data.kind,
    )


def _find_canonical(data: NormalizedResultObservation) -> CanonicalResult | None:
    existing = (
        ResultObservation.objects.select_related("canonical_result")
        .filter(observation_key=data.observation_key)
        .first()
    )
    if existing:
        return existing.canonical_result

    proposed = CanonicalResult.objects.filter(identity_key=data.proposed_identity_key).first()
    if proposed:
        return proposed

    def slot_available(result: CanonicalResult) -> bool:
        if data.attempt_number is None:
            return True
        return (
            not result.observations.filter(
                ingestion_method=data.ingestion_method,
                kind=data.kind,
                attempt_number__isnull=False,
            )
            .exclude(attempt_number=data.attempt_number)
            .exists()
        )

    same_source_rows = list(
        ResultObservation.objects.select_related("canonical_result")
        .filter(
            source=data.source,
            source_result_identity=data.source_result_identity,
            kind=data.kind,
            status=ResultObservation.Status.ACTIVE,
        )
        .order_by("attempt_number", "pk")
    )
    same_value = next(
        (
            row
            for row in same_source_rows
            if row.value == data.value and slot_available(row.canonical_result)
        ),
        None,
    )
    if same_value:
        return same_value.canonical_result
    canonical_ids = {row.canonical_result_id for row in same_source_rows}
    if len(canonical_ids) == 1 and slot_available(same_source_rows[0].canonical_result):
        return same_source_rows[0].canonical_result

    # This value match is the provider-neutral bridge when provider result IDs and
    # round IDs differ. Attempt position still wins whenever both sources expose it.
    candidates = _natural_candidates(data).filter(value=data.value)
    if data.attempt_number is not None:
        exact = candidates.filter(attempt_number=data.attempt_number).first()
        if exact:
            return exact
    return next(
        (
            result
            for result in candidates.order_by("attempt_number", "pk")
            if slot_available(result)
        ),
        None,
    )


def _create_canonical(
    data: NormalizedResultObservation, identity_scope: ResultIdentityScope | None
) -> CanonicalResult:
    defaults = {
        "identity_scope": identity_scope,
        "wca_competition_id": data.wca_competition_id,
        "competition_name": data.competition_name,
        "competition_country_code": data.competition_country_code,
        "competition_start_date": data.competition_start_date,
        "competition_end_date": data.competition_end_date,
        "round_id": data.round_id,
        "round_number": data.round_number,
        "round_name": data.round_name,
        "event_id": data.event_id,
        "event_name": data.event_name,
        "competitor_name": data.competitor_name,
        "competitor_wca_id": data.competitor_wca_id,
        "country_code": data.country_code,
        "kind": data.kind,
        "attempt_number": data.attempt_number,
        "value": data.value,
        "formatted_result": format_result(data.event_id, data.kind, data.value),
        "entered_at": data.entered_at,
        "first_observed_at": data.observed_at,
        "last_observed_at": data.observed_at,
        "source_url": data.source_url,
    }
    try:
        with transaction.atomic():
            return CanonicalResult.objects.create(
                identity_key=data.proposed_identity_key, **defaults
            )
    except IntegrityError:
        return CanonicalResult.objects.get(identity_key=data.proposed_identity_key)


def _update_canonical_context(result: CanonicalResult, data: NormalizedResultObservation) -> None:
    result.wca_competition_id = data.wca_competition_id or result.wca_competition_id
    result.competition_name = data.competition_name or result.competition_name
    result.competition_country_code = (
        data.competition_country_code or result.competition_country_code
    )
    result.competition_start_date = data.competition_start_date or result.competition_start_date
    result.competition_end_date = data.competition_end_date or result.competition_end_date
    result.round_id = data.round_id or result.round_id
    result.round_number = (
        data.round_number if data.round_number is not None else result.round_number
    )
    result.round_name = data.round_name or result.round_name
    result.event_name = data.event_name or result.event_name
    result.competitor_name = data.competitor_name or result.competitor_name
    result.competitor_wca_id = data.competitor_wca_id or result.competitor_wca_id
    result.country_code = data.country_code or result.country_code
    result.source_url = data.source_url or result.source_url


def _refresh_canonical(result: CanonicalResult) -> None:
    observations = list(
        result.observations.filter(status=ResultObservation.Status.ACTIVE).order_by("pk")
    )
    if not observations:
        result.status = CanonicalResult.Status.RETRACTED
        result.validation_status = CanonicalResult.ValidationStatus.PENDING
        result.validation_reason = "all_source_observations_retracted"
        result.current_observation = None
        result.save()
        return

    trusted = [item for item in observations if item.result_evidence_trusted]
    trusted_values = {item.value for item in trusted}
    chosen = max(
        observations,
        key=lambda item: (
            result_trust_rank(item.ingestion_method),
            item.last_observed_at,
            item.pk,
        ),
    )
    value_changed = result.current_observation_id is not None and result.value != chosen.value
    result.current_observation = chosen
    result.value = chosen.value
    result.formatted_result = format_result(result.event_id, result.kind, chosen.value)
    result.entered_at = chosen.entered_at or result.entered_at
    result.first_observed_at = min(item.first_observed_at for item in observations)
    result.last_observed_at = max(item.last_observed_at for item in observations)
    if value_changed:
        result.revision += 1
    result.status = (
        CanonicalResult.Status.CORRECTED if result.revision > 1 else CanonicalResult.Status.ACTIVE
    )
    if len(trusted_values) > 1:
        result.validation_status = CanonicalResult.ValidationStatus.REJECTED
        result.validation_reason = "trusted_source_disagreement"
    elif trusted:
        result.validation_status = CanonicalResult.ValidationStatus.VERIFIED
        result.validation_reason = "trusted_source_observation"
    else:
        result.validation_status = CanonicalResult.ValidationStatus.PENDING
        result.validation_reason = "untrusted_source_only"
    result.save()


@transaction.atomic
def reconcile_result_observation(
    data: NormalizedResultObservation,
    *,
    defer_classification: bool = False,
) -> ResultObservation:
    identity_scope = None
    if data.natural_result_prefix:
        scope, _created = ResultIdentityScope.objects.get_or_create(key=data.natural_result_prefix)
        identity_scope = ResultIdentityScope.objects.select_for_update().get(pk=scope.pk)
    result = _find_canonical(data) or _create_canonical(data, identity_scope)
    result = CanonicalResult.objects.select_for_update().get(pk=result.pk)
    if identity_scope and result.identity_scope_id is None:
        result.identity_scope = identity_scope
    _update_canonical_context(result, data)
    result.save()

    observation, observation_created = ResultObservation.objects.get_or_create(
        observation_key=data.observation_key,
        defaults={
            "canonical_result": result,
            "source": data.source,
            "ingestion_method": data.ingestion_method,
            "source_result_identity": data.source_result_identity,
            "kind": data.kind,
            "value": data.value,
            "first_observed_at": data.observed_at,
            "last_observed_at": data.observed_at,
        },
    )
    if not observation_created:
        observation = ResultObservation.objects.select_for_update().get(pk=observation.pk)
    if observation.canonical_result_id != result.pk:
        # Once a source slot has a canonical identity, corrections stay attached to
        # that identity instead of becoming unrelated solves.
        result = CanonicalResult.objects.select_for_update().get(pk=observation.canonical_result_id)
        _update_canonical_context(result, data)
        result.save()

    changed = not observation_created and (
        observation.value != data.value
        or observation.source_record_tag != data.source_record_tag
        or observation.status != ResultObservation.Status.ACTIVE
    )
    observation.raw_observation_id = data.raw_observation_id
    observation.source = data.source
    observation.ingestion_method = data.ingestion_method
    observation.source_result_identity = data.source_result_identity
    observation.source_competition_id = data.source_competition_id
    observation.source_competitor_id = data.source_competitor_id
    observation.kind = data.kind
    observation.attempt_number = data.attempt_number
    observation.value = data.value
    observation.source_record_tag = data.source_record_tag
    observation.source_claim_trusted = record_claim_is_trusted(data.ingestion_method)
    observation.result_evidence_trusted = result_evidence_is_trusted(data.ingestion_method)
    observation.entered_at = data.entered_at
    observation.last_observed_at = data.observed_at
    observation.status = ResultObservation.Status.ACTIVE
    observation.normalized_payload = data.normalized_payload
    if changed:
        observation.revision += 1
    observation.save()

    _refresh_canonical(result)
    if not defer_classification:
        validate_result_against_latest_snapshot(result)
        reclassify_scope(result.event_id, result.kind)
    logger.info(
        "result_observation_reconciled source=%s method=%s observation_id=%s canonical_result_id=%s revision=%s",
        data.source,
        data.ingestion_method,
        observation.pk,
        result.pk,
        observation.revision,
    )
    return observation


@transaction.atomic
def retract_result_observation(
    observation_key: str,
    observed_at,
    *,
    defer_classification: bool = False,
) -> bool:
    observation = (
        ResultObservation.objects.select_for_update()
        .select_related("canonical_result")
        .filter(observation_key=observation_key)
        .first()
    )
    if observation is None or observation.status == ResultObservation.Status.RETRACTED:
        return False
    observation.status = ResultObservation.Status.RETRACTED
    observation.last_observed_at = observed_at
    observation.revision += 1
    observation.save(update_fields=["status", "last_observed_at", "revision", "updated_at"])
    result = observation.canonical_result
    _refresh_canonical(result)
    if not defer_classification:
        validate_result_against_latest_snapshot(result)
        reclassify_scope(result.event_id, result.kind)
    return True
