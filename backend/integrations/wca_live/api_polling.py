import logging
from time import monotonic

from django.utils import timezone

from apps.records.models import IngestionRun

from .api_client import WCALiveAPIClient
from .ingestion import ingest_api_record
from .queries import RECENT_RECORDS_QUERY

logger = logging.getLogger(__name__)


def poll_recent_records(endpoint: str, run: IngestionRun | None = None) -> dict:
    started = monotonic()
    observed_at = timezone.now()
    logger.info("api_poll_started endpoint=%s", endpoint)
    data = WCALiveAPIClient(endpoint).execute(RECENT_RECORDS_QUERY)
    records = data.get("recentRecords", [])
    if not isinstance(records, list):
        raise TypeError("WCA Live recentRecords response was not a list")
    logger.info("api_poll_response_received records_found=%d", len(records))
    created = 0
    duplicates = 0
    malformed = 0
    for index, payload in enumerate(records):
        try:
            _record, was_created = ingest_api_record(payload, run, observed_at=observed_at)
            created += int(was_created)
            duplicates += int(not was_created)
        except Exception:
            malformed += 1
            logger.exception("api_record_processing_failed record_index=%d", index)
    duration = monotonic() - started
    logger.info(
        "api_poll_completed records_found=%d new_observations=%d duplicates_ignored=%d malformed=%d duration_seconds=%.3f",
        len(records),
        created,
        duplicates,
        malformed,
        duration,
    )
    return {
        "records_found": len(records),
        "new_observations": created,
        "duplicates_ignored": duplicates,
        "malformed": malformed,
        "duration_seconds": duration,
    }
