import hashlib
import json
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.records.models import (
    IngestionRun,
    RecentRecordObservation,
    SourceObservation,
)

from .mappers import map_record
from .result_values import format_result, is_complete
from .schemas import RecordCandidate

logger = logging.getLogger(__name__)
RECORD_LEVELS = {"WR", "CR", "NR"}


def canonical_comparison_key(candidate: RecordCandidate) -> str:
    return f"{candidate.wca_live_result_id}|{candidate.kind}|{candidate.record_level}"


def store_observation(
    payload: dict,
    event_type: str,
    run: IngestionRun | None,
    ingestion_method: str,
    observed_at=None,
) -> tuple[SourceObservation, bool]:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    observation, created = SourceObservation.objects.get_or_create(
        source="wca_live",
        ingestion_method=ingestion_method,
        payload_hash=payload_hash,
        defaults={
            "event_type": event_type,
            "external_id": str(payload.get("id", "")),
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
            "wca_live_record_id": candidate.wca_live_record_id,
            "wca_live_result_id": candidate.wca_live_result_id,
            "wca_live_competition_id": candidate.wca_live_competition_id,
            "wca_competition_id": candidate.wca_competition_id,
            "competition_name": candidate.competition_name,
            "competition_start_date": candidate.competition_start_date,
            "competition_end_date": candidate.competition_end_date,
            "round_id": candidate.round_id,
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
        observation.wca_live_record_id = candidate.wca_live_record_id
        observation.wca_live_result_id = candidate.wca_live_result_id
        observation.wca_live_competition_id = candidate.wca_live_competition_id
        observation.wca_competition_id = candidate.wca_competition_id
        observation.competition_name = candidate.competition_name
        observation.competition_start_date = candidate.competition_start_date
        observation.competition_end_date = candidate.competition_end_date
        observation.round_id = candidate.round_id
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
    return observation, created


@transaction.atomic
def ingest_api_record(
    payload: dict, run: IngestionRun | None = None, observed_at=None
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
