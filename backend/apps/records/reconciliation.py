import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction

from integrations.wca_live.result_values import format_result

from .domain import NormalizedResultObservation
from .models import (
    CanonicalResult,
    CanonicalResultRevision,
    ClassificationWork,
    ResultIdentityScope,
    ResultObservation,
)
from .source_trust import (
    record_claim_is_trusted,
    result_evidence_is_trusted,
    result_trust_rank,
)

logger = logging.getLogger(__name__)

REVISION_FIELDS = (
    "identity_key",
    "wca_competition_id",
    "competition_name",
    "competition_country_code",
    "competition_start_date",
    "competition_end_date",
    "competition_timezone",
    "competition_local_date",
    "timezone_resolution_status",
    "timezone_resolution_reason",
    "round_id",
    "round_number",
    "round_name",
    "event_id",
    "event_name",
    "competitor_name",
    "competitor_wca_id",
    "country_code",
    "kind",
    "value",
    "formatted_result",
    "entered_at",
    "first_observed_at",
    "source_url",
    "validation_status",
    "validation_reason",
)


def _existing_observation(data: NormalizedResultObservation) -> ResultObservation | None:
    return (
        ResultObservation.objects.select_related("canonical_result")
        .filter(observation_key=data.observation_key)
        .first()
    )


def _same_source_canonical(data: NormalizedResultObservation) -> CanonicalResult | None:
    rows = list(
        ResultObservation.objects.select_related("canonical_result")
        .filter(
            source=data.source,
            source_result_identity=data.source_result_identity,
            kind=data.kind,
        )
        .order_by("pk")
    )
    canonical_ids = {row.canonical_result_id for row in rows}
    return rows[0].canonical_result if len(canonical_ids) == 1 else None


def _find_canonical(
    data: NormalizedResultObservation,
    existing: ResultObservation | None,
) -> CanonicalResult | None:
    natural_key = data.natural_result_prefix
    natural = (
        CanonicalResult.objects.filter(identity_key=natural_key).first() if natural_key else None
    )
    if existing:
        current = existing.canonical_result
        if natural and natural.pk != current.pk:
            return natural
        if natural_key and current.identity_key.startswith("source|"):
            current = CanonicalResult.objects.select_for_update().get(pk=current.pk)
            try:
                with transaction.atomic():
                    current.identity_key = natural_key
                    current.save(update_fields=["identity_key", "updated_at"])
            except IntegrityError:
                return CanonicalResult.objects.get(identity_key=natural_key)
        return current
    if natural:
        return natural
    return _same_source_canonical(data)


def _local_date(data: NormalizedResultObservation):
    if data.competition_local_date is not None:
        return data.competition_local_date
    if not data.competition_timezone:
        return None
    instant = data.entered_at or data.observed_at
    try:
        return instant.astimezone(ZoneInfo(data.competition_timezone)).date()
    except ZoneInfoNotFoundError:
        return None


def _timezone_resolution(data: NormalizedResultObservation) -> tuple[str, str]:
    if not data.competition_timezone:
        return "unresolved", "missing_or_ambiguous_source_timezone"
    if _local_date(data) is None:
        return "unresolved", "invalid_source_timezone"
    return "resolved", "provider_supplied_iana_timezone"


def _create_canonical(
    data: NormalizedResultObservation, identity_scope: ResultIdentityScope | None
) -> CanonicalResult:
    timezone_status, timezone_reason = _timezone_resolution(data)
    defaults = {
        "identity_scope": identity_scope,
        "wca_competition_id": data.wca_competition_id,
        "competition_name": data.competition_name,
        "competition_country_code": data.competition_country_code,
        "competition_start_date": data.competition_start_date,
        "competition_end_date": data.competition_end_date,
        "competition_timezone": data.competition_timezone,
        "competition_local_date": _local_date(data),
        "timezone_resolution_status": timezone_status,
        "timezone_resolution_reason": timezone_reason,
        "round_id": data.round_id,
        "round_number": data.round_number,
        "round_name": data.round_name,
        "event_id": data.event_id,
        "event_name": data.event_name,
        "competitor_name": data.competitor_name,
        "competitor_wca_id": data.competitor_wca_id,
        "country_code": data.country_code,
        "kind": data.kind,
        "attempt_number": None,
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
    if data.competition_timezone:
        result.competition_timezone = data.competition_timezone
        result.competition_local_date = _local_date(data)
    timezone_status, timezone_reason = _timezone_resolution(data)
    if timezone_status == "resolved" or not result.competition_timezone:
        result.timezone_resolution_status = timezone_status
        result.timezone_resolution_reason = timezone_reason


def _refresh_canonical_values(result: CanonicalResult) -> bool:
    observations = list(
        result.observations.filter(status=ResultObservation.Status.ACTIVE).order_by("pk")
    )
    if not observations:
        result.validation_status = CanonicalResult.ValidationStatus.PENDING
        result.validation_reason = "all_source_observations_retracted"
        result.current_observation = None
        return False

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
    result.current_observation = chosen
    result.value = chosen.value
    result.formatted_result = format_result(result.event_id, result.kind, chosen.value)
    result.entered_at = chosen.entered_at or result.entered_at
    result.first_observed_at = min(item.first_observed_at for item in observations)
    result.last_observed_at = max(item.last_observed_at for item in observations)
    if len(trusted_values) > 1:
        result.validation_status = CanonicalResult.ValidationStatus.REJECTED
        result.validation_reason = "trusted_source_disagreement"
    elif trusted:
        result.validation_status = CanonicalResult.ValidationStatus.VERIFIED
        result.validation_reason = "trusted_source_observation"
    else:
        result.validation_status = CanonicalResult.ValidationStatus.PENDING
        result.validation_reason = "untrusted_source_only"
    return True


def _revision_values(result: CanonicalResult) -> dict:
    values = {field: getattr(result, field) for field in REVISION_FIELDS}
    values.update(
        {
            "canonical_status": result.status,
            "last_observed_at": result.last_observed_at,
        }
    )
    return values


def _revision_changed(result: CanonicalResult, latest: CanonicalResultRevision | None) -> bool:
    if latest is None:
        return True
    return any(getattr(latest, field) != getattr(result, field) for field in REVISION_FIELDS)


def _snapshot_and_enqueue(result: CanonicalResult) -> CanonicalResultRevision | None:
    """Persist a new immutable snapshot only for a classifier-relevant change."""

    latest = result.revisions.order_by("-revision").first()
    has_active_evidence = result.current_observation_id is not None
    changed = _revision_changed(result, latest)
    if latest is None:
        action = CanonicalResultRevision.Action.ACTIVE
        result.status = CanonicalResult.Status.ACTIVE
    elif not has_active_evidence:
        if latest.canonical_status == CanonicalResult.Status.RETRACTED:
            result.status = latest.canonical_status
            result.save()
            return None
        action = CanonicalResultRevision.Action.RETRACTED
        result.status = CanonicalResult.Status.RETRACTED
    elif latest.canonical_status == CanonicalResult.Status.RETRACTED:
        action = CanonicalResultRevision.Action.ACTIVE
        result.status = CanonicalResult.Status.ACTIVE
    elif changed:
        action = CanonicalResultRevision.Action.CORRECTED
        result.status = CanonicalResult.Status.CORRECTED
    else:
        # Observation timestamps and duplicate raw evidence may update the mutable
        # head without creating classifier work.
        result.status = latest.canonical_status
        result.save()
        return None

    result.revision = 1 if latest is None else latest.revision + 1
    result.save()
    revision = CanonicalResultRevision.objects.create(
        canonical_result=result,
        revision=result.revision,
        action=action,
        **_revision_values(result),
    )
    ClassificationWork.objects.get_or_create(
        canonical_result_revision=revision,
        defaults={
            "canonical_result": result,
            "revision": revision.revision,
            "action": action,
        },
    )
    return revision


def _refresh_and_enqueue(result: CanonicalResult) -> CanonicalResultRevision | None:
    _refresh_canonical_values(result)
    result.save()
    # Validate CubingChina evidence against the already stored WCA snapshot before
    # freezing the classifier input. This performs no network I/O.
    from integrations.wca.record_validation import validate_result_against_latest_snapshot

    validate_result_against_latest_snapshot(result, enqueue_revision=False)
    result.refresh_from_db()
    return _snapshot_and_enqueue(result)


@transaction.atomic
def enqueue_current_canonical_state(result_id: int) -> CanonicalResultRevision | None:
    """Snapshot a classifier-relevant head change made by an upstream service."""

    result = CanonicalResult.objects.select_for_update().get(pk=result_id)
    return _snapshot_and_enqueue(result)


@transaction.atomic
def reconcile_result_observation(
    data: NormalizedResultObservation,
    *,
    defer_classification: bool = False,
) -> ResultObservation:
    """Reconcile evidence and transactionally enqueue its immutable revision.

    ``defer_classification`` remains accepted at ingestion boundaries for a safe
    rolling deployment; classification is always asynchronous now.
    """

    identity_scope = None
    if data.natural_result_prefix:
        scope, _created = ResultIdentityScope.objects.get_or_create(key=data.natural_result_prefix)
        identity_scope = ResultIdentityScope.objects.select_for_update().get(pk=scope.pk)
    existing = _existing_observation(data)
    previous_result = existing.canonical_result if existing else None
    result = _find_canonical(data, existing) or _create_canonical(data, identity_scope)
    result = CanonicalResult.objects.select_for_update().get(pk=result.pk)
    natural_key = data.natural_result_prefix
    if natural_key and result.identity_key != natural_key:
        natural = (
            CanonicalResult.objects.select_for_update().filter(identity_key=natural_key).first()
        )
        if natural is not None:
            result = natural
        elif result.identity_key.startswith("source|"):
            try:
                with transaction.atomic():
                    result.identity_key = natural_key
                    result.save(update_fields=["identity_key", "updated_at"])
            except IntegrityError:
                result = CanonicalResult.objects.select_for_update().get(identity_key=natural_key)
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
        observation.canonical_result = result

    changed = not observation_created and (
        observation.value != data.value
        or observation.status != ResultObservation.Status.ACTIVE
        or observation.entered_at != data.entered_at
    )
    observation.raw_observation_id = data.raw_observation_id
    observation.source = data.source
    observation.ingestion_method = data.ingestion_method
    observation.source_result_identity = data.source_result_identity
    observation.source_competition_id = data.source_competition_id
    observation.source_competitor_id = data.source_competitor_id
    observation.kind = data.kind
    observation.attempt_number = None
    observation.value = data.value
    # Provider tags are retained only as source audit data.  They never enter the
    # revision fingerprint or classifier.
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

    revision = _refresh_and_enqueue(result)
    if previous_result and previous_result.pk != result.pk:
        previous_result = CanonicalResult.objects.select_for_update().get(pk=previous_result.pk)
        _refresh_and_enqueue(previous_result)
    logger.info(
        "result_observation_reconciled source=%s method=%s observation_id=%s "
        "canonical_result_id=%s canonical_revision=%s work_enqueued=%s",
        data.source,
        data.ingestion_method,
        observation.pk,
        result.pk,
        result.revision,
        bool(revision),
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
    result = CanonicalResult.objects.select_for_update().get(pk=observation.canonical_result_id)
    _refresh_and_enqueue(result)
    return True
