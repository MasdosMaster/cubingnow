import hashlib
import json

from django.db import transaction
from django.utils import timezone

from apps.competitions.models import Competition
from apps.competitors.models import Competitor
from apps.records.models import IngestionRun, Record, Result, SourceObservation

from .mappers import map_record


def store_observation(payload: dict, event_type: str, run: IngestionRun) -> SourceObservation:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    observation, _ = SourceObservation.objects.get_or_create(
        payload_hash=payload_hash,
        defaults={
            "event_type": event_type,
            "external_id": str(payload.get("id", "")),
            "observed_at": timezone.now(),
            "payload": payload,
            "ingestion_run": run,
        },
    )
    return observation


@transaction.atomic
def ingest_record(payload: dict, run: IngestionRun) -> Record:
    observation = store_observation(payload, "record", run)
    if observation.processed_at:
        return Result.objects.get(source_id=payload["id"]).record

    try:
        item = map_record(payload, observation.observed_at)
        competition, _ = Competition.objects.update_or_create(
            wca_id=item.competition_id,
            defaults={
                "name": item.competition_name,
                "country_code": item.competition_country_code,
                "city": item.competition_city,
                "timezone": item.competition_timezone,
                "start_date": item.competition_start_date,
                "end_date": item.competition_end_date,
            },
        )
        competitor, _ = Competitor.objects.update_or_create(
            wca_id=item.competitor_wca_id,
            defaults={
                "name": item.competitor_name,
                "country_code": item.competitor_country_code,
            },
        )
        result, _ = Result.objects.update_or_create(
            source_id=item.source_id,
            defaults={
                "competition": competition,
                "competitor": competitor,
                "event_id": item.event_id,
                "event_name": item.event_name,
                "kind": item.result_kind,
                "value": item.result_value,
            },
        )
        record, created = Record.objects.get_or_create(
            result=result,
            defaults={"level": item.record_level, "detected_at": item.observed_at},
        )
        if not created and record.level != item.record_level:
            record.level = item.record_level
            record.save(update_fields=["level"])
        observation.processed_at = timezone.now()
        observation.processing_error = ""
        observation.save(update_fields=["processed_at", "processing_error"])
        run.observations_count += 1
        run.save(update_fields=["observations_count"])
        return record
    except Exception as exc:
        observation.processing_error = str(exc)
        observation.save(update_fields=["processing_error"])
        raise
