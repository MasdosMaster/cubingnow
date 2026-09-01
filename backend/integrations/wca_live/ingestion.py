import hashlib
import json
import logging
from dataclasses import replace

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.records.domain import NormalizedResultObservation, finalized_observation_key
from apps.records.finalization import (
    RoundFinalizationRule,
    all_expected_attempts_are_entered,
    round_result_is_finalized,
)
from apps.records.models import (
    IngestionRun,
    RecentRecordObservation,
    SourceObservation,
    SubscriptionRound,
)
from apps.records.reconciliation import (
    reconcile_result_observation,
    retract_result_observation,
)

from .mappers import map_record
from .result_values import format_result, is_complete
from .schemas import RecordCandidate

logger = logging.getLogger(__name__)
RECORD_LEVELS = {"WR", "CR", "NR"}


def candidate_is_finalized(candidate: RecordCandidate) -> bool:
    if not round_result_is_finalized(
        candidate.attempts,
        RoundFinalizationRule(
            expected_attempts=candidate.expected_attempts or 0,
            cutoff_attempts=candidate.cutoff_attempts,
            cutoff_value=candidate.cutoff_value,
        ),
        event_id=candidate.event_id,
    ):
        return False
    if candidate.kind == "average" and not all_expected_attempts_are_entered(
        candidate.attempts, candidate.expected_attempts or 0
    ):
        return False
    final_value = candidate.final_best if candidate.kind == "single" else candidate.final_average
    return final_value not in (None, 0) and candidate.raw_result == final_value


def canonical_comparison_key(candidate: RecordCandidate) -> str:
    if (
        candidate.wca_competition_id
        and candidate.competitor_wca_id
        and candidate.round_number is not None
    ):
        return "|".join(
            [
                "wca",
                candidate.wca_competition_id.upper(),
                candidate.competitor_wca_id.upper(),
                candidate.event_id,
                str(candidate.round_number),
                candidate.kind,
                candidate.record_level,
            ]
        )
    source_id = candidate.source_result_id or candidate.wca_live_result_id
    return (
        f"{candidate.source}:{source_id or candidate.stable_result_identity}"
        f"|{candidate.kind}|{candidate.record_level}"
    )


def store_observation(
    payload: dict,
    event_type: str,
    run: IngestionRun | None,
    ingestion_method: str,
    observed_at=None,
    source: str = "wca_live",
) -> tuple[SourceObservation, bool]:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    observation, created = SourceObservation.objects.get_or_create(
        source=source,
        ingestion_method=ingestion_method,
        payload_hash=payload_hash,
        defaults={
            "event_type": event_type,
            "external_id": str(payload.get("id") or payload.get("i") or ""),
            "observed_at": observed_at or timezone.now(),
            "payload": payload,
            "ingestion_run": run,
        },
    )
    return observation, created


@transaction.atomic
def persist_record_candidate(
    candidate: RecordCandidate,
    ingestion_method: str,
    source_payload: dict,
    *,
    raw_observation: SourceObservation | None = None,
    reconcile: bool = True,
    defer_classification: bool = False,
    dirty_scopes: set[tuple[str, str]] | None = None,
) -> tuple[RecentRecordObservation, bool]:
    if candidate.record_level not in RECORD_LEVELS:
        raise ValueError(f"Unsupported WCA record level: {candidate.record_level!r}")
    if not is_complete(candidate.raw_result):
        raise ValueError("A record observation must contain a complete positive result")

    observation, created = RecentRecordObservation.objects.get_or_create(
        stable_result_identity=candidate.stable_result_identity,
        kind=candidate.kind,
        record_level=candidate.record_level,
        ingestion_method=ingestion_method,
        defaults={
            "canonical_key": canonical_comparison_key(candidate),
            "source": candidate.source,
            "source_result_id": candidate.source_result_id or candidate.wca_live_result_id,
            "source_competition_id": (
                candidate.source_competition_id or candidate.wca_live_competition_id
            ),
            "source_competitor_id": (
                candidate.source_competitor_id or candidate.competitor_wca_live_id
            ),
            "wca_live_record_id": candidate.wca_live_record_id,
            "wca_live_result_id": candidate.wca_live_result_id,
            "wca_live_competition_id": candidate.wca_live_competition_id,
            "wca_competition_id": candidate.wca_competition_id,
            "competition_name": candidate.competition_name,
            "competition_country_code": candidate.competition_country_code,
            "competition_start_date": candidate.competition_start_date,
            "competition_end_date": candidate.competition_end_date,
            "round_id": candidate.round_id,
            "round_number": candidate.round_number,
            "round_name": candidate.round_name,
            "event_id": candidate.event_id,
            "event_name": candidate.event_name,
            "competitor_name": candidate.competitor_name,
            "competitor_wca_id": candidate.competitor_wca_id,
            "competitor_wca_live_id": candidate.competitor_wca_live_id,
            "country_code": candidate.country_code,
            "raw_result": candidate.raw_result,
            "formatted_result": format_result(
                candidate.event_id, candidate.kind, candidate.raw_result
            ),
            "status": RecentRecordObservation.Status.ACTIVE,
            "source_url": candidate.source_url,
            "source_update_timestamp": candidate.source_update_timestamp,
            "first_observed_at": candidate.observed_at,
            "detected_at": candidate.observed_at,
            "last_observed_at": candidate.observed_at,
            "source_payload": source_payload,
        },
    )
    if not created:
        observation.canonical_key = canonical_comparison_key(candidate)
        observation.source = candidate.source
        observation.source_result_id = candidate.source_result_id or candidate.wca_live_result_id
        observation.source_competition_id = (
            candidate.source_competition_id or candidate.wca_live_competition_id
        )
        observation.source_competitor_id = (
            candidate.source_competitor_id or candidate.competitor_wca_live_id
        )
        observation.wca_live_record_id = candidate.wca_live_record_id
        observation.wca_live_result_id = candidate.wca_live_result_id
        observation.wca_live_competition_id = candidate.wca_live_competition_id
        observation.wca_competition_id = candidate.wca_competition_id
        observation.competition_name = candidate.competition_name
        observation.competition_country_code = candidate.competition_country_code
        observation.competition_start_date = candidate.competition_start_date
        observation.competition_end_date = candidate.competition_end_date
        observation.round_id = candidate.round_id
        observation.round_number = candidate.round_number
        observation.round_name = candidate.round_name
        observation.event_id = candidate.event_id
        observation.event_name = candidate.event_name
        observation.competitor_name = candidate.competitor_name
        observation.competitor_wca_id = candidate.competitor_wca_id
        observation.competitor_wca_live_id = candidate.competitor_wca_live_id
        observation.country_code = candidate.country_code
        observation.raw_result = candidate.raw_result
        observation.formatted_result = format_result(
            candidate.event_id, candidate.kind, candidate.raw_result
        )
        observation.status = RecentRecordObservation.Status.ACTIVE
        observation.withdrawn_at = None
        observation.source_url = candidate.source_url
        observation.source_update_timestamp = candidate.source_update_timestamp
        observation.last_observed_at = candidate.observed_at
        observation.source_payload = source_payload
        observation.save()
    if reconcile:
        observation_key = finalized_observation_key(
            candidate.source,
            ingestion_method,
            candidate.stable_result_identity,
            candidate.kind,
        )
        if not candidate_is_finalized(candidate):
            retracted = retract_result_observation(
                observation_key,
                candidate.observed_at,
                defer_classification=defer_classification,
            )
            if retracted and defer_classification and dirty_scopes is not None:
                dirty_scopes.add((candidate.event_id, candidate.kind))
            if observation.canonical_result_id is not None:
                observation.canonical_result = None
                observation.save(update_fields=["canonical_result"])
            return observation, created
        normalized = NormalizedResultObservation(
            source=candidate.source,
            ingestion_method=ingestion_method,
            source_result_identity=candidate.stable_result_identity,
            source_competition_id=(
                candidate.source_competition_id or candidate.wca_live_competition_id
            ),
            source_competitor_id=(
                candidate.source_competitor_id or candidate.competitor_wca_live_id
            ),
            wca_competition_id=candidate.wca_competition_id,
            competition_name=candidate.competition_name,
            competition_country_code=candidate.competition_country_code,
            competition_start_date=candidate.competition_start_date,
            competition_end_date=candidate.competition_end_date,
            competition_timezone=candidate.competition_timezone,
            round_id=candidate.round_id,
            round_number=candidate.round_number,
            round_name=candidate.round_name,
            event_id=candidate.event_id,
            event_name=candidate.event_name,
            competitor_name=candidate.competitor_name,
            competitor_wca_id=candidate.competitor_wca_id,
            country_code=candidate.country_code,
            kind=candidate.kind,
            value=candidate.raw_result,
            source_record_tag=candidate.record_level,
            entered_at=candidate.source_update_timestamp,
            observed_at=candidate.observed_at,
            source_url=candidate.source_url,
            normalized_payload=source_payload,
            raw_observation_id=getattr(raw_observation, "pk", None),
        )
        result_observation = reconcile_result_observation(
            normalized,
            defer_classification=defer_classification,
        )
        if defer_classification and dirty_scopes is not None:
            dirty_scopes.add((candidate.event_id, candidate.kind))
        if observation.canonical_result_id != result_observation.canonical_result_id:
            observation.canonical_result = result_observation.canonical_result
            observation.save(update_fields=["canonical_result"])
    return observation, created


@transaction.atomic
def ingest_api_record(
    payload: dict,
    run: IngestionRun | None = None,
    observed_at=None,
    *,
    defer_classification: bool = False,
    dirty_scopes: set[tuple[str, str]] | None = None,
) -> tuple[RecentRecordObservation, bool]:
    observed_at = observed_at or timezone.now()
    observation, raw_created = store_observation(
        payload,
        "recent_record",
        run,
        RecentRecordObservation.IngestionMethod.API_POLLING,
        observed_at,
    )
    try:
        item = map_record(payload, observed_at)
        if not item.competition_timezone:
            stored_timezone = (
                SubscriptionRound.objects.filter(
                    round_id=item.round_id,
                    wca_competition_id=item.wca_competition_id,
                )
                .exclude(competition_timezone="")
                .values_list("competition_timezone", flat=True)
                .first()
            )
            if stored_timezone:
                item = replace(item, competition_timezone=stored_timezone)
        if observation.processed_at:
            existing = RecentRecordObservation.objects.filter(
                stable_result_identity=item.stable_result_identity,
                kind=item.kind,
                record_level=item.record_level,
                ingestion_method=RecentRecordObservation.IngestionMethod.API_POLLING,
            ).first()
            if existing is not None:
                logger.info(
                    "api_record_duplicate_ignored result_id=%s record_level=%s",
                    item.wca_live_result_id,
                    item.record_level,
                )
                return existing, False
        RecentRecordObservation.objects.filter(
            stable_result_identity=item.stable_result_identity,
            kind=item.kind,
            ingestion_method=RecentRecordObservation.IngestionMethod.API_POLLING,
            status=RecentRecordObservation.Status.ACTIVE,
        ).exclude(record_level=item.record_level).update(
            status=RecentRecordObservation.Status.WITHDRAWN,
            withdrawn_at=observed_at,
            last_observed_at=observed_at,
        )
        record, record_created = persist_record_candidate(
            item,
            RecentRecordObservation.IngestionMethod.API_POLLING,
            payload,
            raw_observation=observation,
            defer_classification=defer_classification,
            dirty_scopes=dirty_scopes,
        )
        if not observation.processed_at:
            observation.processed_at = timezone.now()
            observation.processing_error = ""
            observation.save(update_fields=["processed_at", "processing_error"])
            if run:
                IngestionRun.objects.filter(pk=run.pk).update(
                    observations_count=F("observations_count") + 1
                )
                run.refresh_from_db(fields=["observations_count"])
        if not raw_created or not record_created:
            logger.info(
                "api_record_duplicate_ignored result_id=%s record_level=%s",
                item.wca_live_result_id,
                item.record_level,
            )
        return record, record_created
    except Exception as exc:
        observation.processing_error = str(exc)
        observation.save(update_fields=["processing_error"])
        raise


def ingest_record(payload: dict, run: IngestionRun):
    """Compatibility wrapper for the original API reconciliation entry point."""

    record, _created = ingest_api_record(payload, run)
    return record
