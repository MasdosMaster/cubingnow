import asyncio
import logging
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.records.models import IngestionRun, IngestionWorkerStatus, RecentRecordObservation
from apps.records.worker_recovery import (
    TRANSIENT_DATABASE_ERRORS,
    best_effort_database_write,
    prepare_database_retry,
)
from integrations.wca_live.subscription_supervisor import SubscriptionSupervisor
from integrations.weekend_window import resolve_weekend_window

METHOD = RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the independent supervised WCA Live GraphQL subscription worker"

    def add_arguments(self, parser):
        parser.add_argument("--start", default=settings.WCA_WEEKEND_START)
        parser.add_argument("--end", default=settings.WCA_WEEKEND_END)
        parser.add_argument("--api-endpoint", default=settings.WCA_LIVE_API_URL)
        parser.add_argument("--websocket-endpoint", default=settings.WCA_LIVE_WS_URL)
        parser.add_argument(
            "--lookback-days", type=int, default=settings.WCA_COMPETITION_LOOKBACK_DAYS
        )
        parser.add_argument(
            "--discovery-interval",
            type=int,
            default=settings.WCA_ROUND_DISCOVERY_INTERVAL_SECONDS,
        )
        parser.add_argument(
            "--catchup-minutes",
            type=int,
            default=settings.WCA_SUBSCRIPTION_CATCHUP_MINUTES,
        )

    def handle(self, *args, **options):
        try:
            weekend_start, weekend_end = resolve_weekend_window(
                options["start"],
                options["end"],
                timezone_name=settings.WCA_WEEKEND_TIME_ZONE,
            )
        except ValueError as exc:
            raise CommandError(
                "--start and --end must both be ISO dates (YYYY-MM-DD), with end on or after start"
            ) from exc

        database_attempt = 0
        while True:
            try:
                self._run_session(options, weekend_start, weekend_end)
                return
            except TRANSIENT_DATABASE_ERRORS as exc:
                database_attempt += 1
                time.sleep(
                    prepare_database_retry(
                        database_attempt,
                        error=exc,
                        logger=logger,
                        worker="subscription_worker",
                    )
                )

    def _run_session(self, options, weekend_start, weekend_end):
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
                "metadata": {
                    "weekend_start": weekend_start.isoformat(),
                    "weekend_end": weekend_end.isoformat(),
                },
            },
        )
        run = IngestionRun.objects.create(mode=IngestionRun.Mode.GRAPHQL_SUBSCRIPTION)
        supervisor = SubscriptionSupervisor(
            weekend_start=weekend_start,
            weekend_end=weekend_end,
            api_endpoint=options["api_endpoint"],
            websocket_endpoint=options["websocket_endpoint"],
            lookback_days=options["lookback_days"],
            discovery_interval=options["discovery_interval"],
            catchup_minutes=options["catchup_minutes"],
            run=run,
        )
        try:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Starting WCA Live subscriptions for {weekend_start} through {weekend_end}"
                )
            )
            asyncio.run(supervisor.run_forever())
            run.status = IngestionRun.Status.SUCCEEDED
        except KeyboardInterrupt:
            supervisor.stop()
            run.status = IngestionRun.Status.SUCCEEDED
            self.stdout.write("Subscription worker stopped")
        except Exception as exc:
            run.status = IngestionRun.Status.FAILED
            run.error = str(exc)
            raise
        finally:
            run.finished_at = timezone.now()
            best_effort_database_write(
                lambda: run.save(update_fields=["status", "error", "finished_at"]),
                logger=logger,
                action="subscription_run_finished",
            )
            best_effort_database_write(
                lambda: IngestionWorkerStatus.objects.filter(
                    ingestion_method=METHOD
                ).update(
                    is_running=False,
                    connected=False,
                    subscribed_round_count=0,
                    heartbeat_at=timezone.now(),
                    last_stopped_at=timezone.now(),
                ),
                logger=logger,
                action="subscription_worker_stopped",
            )
