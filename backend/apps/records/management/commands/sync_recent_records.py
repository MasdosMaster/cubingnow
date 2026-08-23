import logging
import os
import random
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.records.models import IngestionRun, IngestionWorkerStatus, RecentRecordObservation
from apps.records.retention import purge_expired_source_observations
from apps.records.worker_recovery import (
    TRANSIENT_DATABASE_ERRORS,
    best_effort_database_write,
    prepare_database_retry,
)
from integrations.wca_live.api_polling import poll_recent_records

logger = logging.getLogger(__name__)
METHOD = RecentRecordObservation.IngestionMethod.API_POLLING


class Command(BaseCommand):
    help = "Fetch WCA Live recent records into the isolated API-polling collection"

    def add_arguments(self, parser):
        parser.add_argument("--endpoint", default=settings.WCA_LIVE_API_URL)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument(
            "--interval", type=int, default=settings.WCA_API_POLL_INTERVAL_SECONDS
        )
        parser.add_argument("--retry-attempts", type=int, default=settings.WCA_RETRY_MAX_ATTEMPTS)

    def handle(self, *args, **options):
        database_attempt = 0
        while True:
            try:
                self._mark_started()
                break
            except TRANSIENT_DATABASE_ERRORS as exc:
                if not options["watch"]:
                    raise
                database_attempt += 1
                time.sleep(
                    prepare_database_retry(
                        database_attempt,
                        error=exc,
                        logger=logger,
                        worker="api_polling_worker",
                    )
                )
        database_attempt = 0
        next_retention_at = time.monotonic() + min(
            max(settings.SOURCE_OBSERVATION_RETENTION_INTERVAL_SECONDS, 60), 600
        )
        try:
            while True:
                try:
                    succeeded = self._poll_with_retries(options)
                    database_attempt = 0
                    if time.monotonic() >= next_retention_at:
                        purge_expired_source_observations(
                            retention_days=settings.SOURCE_OBSERVATION_RETENTION_DAYS
                        )
                        next_retention_at = time.monotonic() + max(
                            settings.SOURCE_OBSERVATION_RETENTION_INTERVAL_SECONDS, 60
                        )
                except TRANSIENT_DATABASE_ERRORS as exc:
                    if not options["watch"]:
                        raise
                    database_attempt += 1
                    time.sleep(
                        prepare_database_retry(
                            database_attempt,
                            error=exc,
                            logger=logger,
                            worker="api_polling_worker",
                        )
                    )
                    continue
                if not options["watch"]:
                    if not succeeded:
                        raise CommandError("WCA Live API polling failed after all retries")
                    return
                time.sleep(max(options["interval"], 10))
        finally:
            best_effort_database_write(
                self._mark_stopped,
                logger=logger,
                action="api_polling_worker_stopped",
            )

    def _poll_with_retries(self, options) -> bool:
        attempts = max(options["retry_attempts"], 1)
        for attempt in range(1, attempts + 1):
            run = None
            try:
                run = IngestionRun.objects.create(mode=IngestionRun.Mode.API_POLLING)
                now = timezone.now()
                IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
                    heartbeat_at=now,
                    last_poll_started_at=now,
                    last_error="",
                )
                stats = poll_recent_records(options["endpoint"], run)
                run.status = IngestionRun.Status.SUCCEEDED
                run.finished_at = timezone.now()
                run.save(update_fields=["status", "finished_at"])
                IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
                    heartbeat_at=timezone.now(),
                    last_successful_poll_at=timezone.now(),
                    last_error="",
                    metadata=stats,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"API poll found {stats['records_found']} records; "
                        f"stored {stats['new_observations']} new observations"
                    )
                )
                return True
            except TRANSIENT_DATABASE_ERRORS:
                raise
            except Exception as exc:
                error = str(exc)
                if run is not None:
                    run.status = IngestionRun.Status.FAILED
                    run.error = error
                    run.finished_at = timezone.now()
                    best_effort_database_write(
                        lambda run=run: run.save(
                            update_fields=["status", "error", "finished_at"]
                        ),
                        logger=logger,
                        action="api_polling_run_failed",
                    )
                best_effort_database_write(
                    lambda error=error: IngestionWorkerStatus.objects.filter(
                        ingestion_method=METHOD
                    ).update(heartbeat_at=timezone.now(), last_error=error),
                    logger=logger,
                    action="api_polling_worker_error",
                )
                logger.exception(
                    "api_poll_failed attempt=%d max_attempts=%d", attempt, attempts
                )
                if attempt < attempts:
                    ceiling = min(
                        settings.WCA_RETRY_MAX_SECONDS,
                        settings.WCA_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    )
                    delay = random.uniform(0, max(ceiling, 0))
                    logger.warning(
                        "api_poll_retry_scheduled attempt=%d delay_seconds=%.2f", attempt + 1, delay
                    )
                    time.sleep(delay)
        return False

    def _mark_started(self):
        now = timezone.now()
        IngestionWorkerStatus.objects.update_or_create(
            ingestion_method=METHOD,
            defaults={
                "is_running": True,
                "connected": False,
                "process_id": os.getpid(),
                "heartbeat_at": now,
                "last_started_at": now,
                "last_error": "",
            },
        )

    def _mark_stopped(self):
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            is_running=False,
            connected=False,
            heartbeat_at=timezone.now(),
            last_stopped_at=timezone.now(),
        )
