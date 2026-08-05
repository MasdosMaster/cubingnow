import asyncio
import os
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.records.models import IngestionRun, IngestionWorkerStatus, RecentRecordObservation
from integrations.wca_live.subscription_supervisor import SubscriptionSupervisor

METHOD = RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION


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
            weekend_start = date.fromisoformat(options["start"])
            weekend_end = date.fromisoformat(options["end"])
        except ValueError as exc:
            raise CommandError("--start and --end must be ISO dates (YYYY-MM-DD)") from exc
        if weekend_end < weekend_start:
            raise CommandError("--end must be on or after --start")

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
            run.save(update_fields=["status", "error", "finished_at"])
            IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
                is_running=False,
                connected=False,
                subscribed_round_count=0,
                heartbeat_at=timezone.now(),
                last_stopped_at=timezone.now(),
            )
