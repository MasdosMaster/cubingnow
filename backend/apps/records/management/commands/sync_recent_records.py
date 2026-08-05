import logging
import os
import random
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.records.models import IngestionRun, IngestionWorkerStatus, RecentRecordObservation
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
        self._mark_started()
        try:
            while True:
                succeeded = self._poll_with_retries(options)
                if not options["watch"]:
                    if not succeeded:
                        raise CommandError("WCA Live API polling failed after all retries")
                    return
                time.sleep(max(options["interval"], 10))
        finally:
            self._mark_stopped()

    def _poll_with_retries(self, options) -> bool:
        attempts = max(options["retry_attempts"], 1)
        for attempt in range(1, attempts + 1):
            run = IngestionRun.objects.create(mode=IngestionRun.Mode.API_POLLING)
            now = timezone.now()
            IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
                heartbeat_at=now,
                last_poll_started_at=now,
                last_error="",
            )
            try:
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
            except Exception as exc:
                run.status = IngestionRun.Status.FAILED
                run.error = str(exc)
                run.finished_at = timezone.now()
                run.save(update_fields=["status", "error", "finished_at"])
                IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
                    heartbeat_at=timezone.now(), last_error=str(exc)
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
