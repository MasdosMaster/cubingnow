import asyncio
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.records.models import IngestionRun, IngestionWorkerStatus, RecentRecordObservation
from integrations.cubingchina.live_supervisor import CubingChinaLiveSupervisor

METHOD = RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET


class Command(BaseCommand):
    help = "Run the continuously discovering CubingChina live-results WebSocket worker"

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=settings.CUBINGCHINA_BASE_URL)
        parser.add_argument("--websocket-endpoint", default=settings.CUBINGCHINA_WS_URL)
        parser.add_argument("--wca-base-url", default=settings.WCA_PUBLIC_BASE_URL)
        parser.add_argument(
            "--discovery-interval",
            type=int,
            default=settings.CUBINGCHINA_DISCOVERY_INTERVAL_SECONDS,
        )
        parser.add_argument(
            "--lookback-days", type=int, default=settings.CUBINGCHINA_LOOKBACK_DAYS
        )
        parser.add_argument(
            "--lookahead-days", type=int, default=settings.CUBINGCHINA_LOOKAHEAD_DAYS
        )
        parser.add_argument(
            "--completion-grace-minutes",
            type=int,
            default=settings.CUBINGCHINA_COMPLETION_GRACE_MINUTES,
        )
        parser.add_argument(
            "--max-connections", type=int, default=settings.CUBINGCHINA_MAX_CONNECTIONS
        )
        parser.add_argument(
            "--retry-base-seconds",
            type=float,
            default=settings.CUBINGCHINA_RETRY_BASE_SECONDS,
        )
        parser.add_argument(
            "--retry-max-seconds",
            type=float,
            default=settings.CUBINGCHINA_RETRY_MAX_SECONDS,
        )
        parser.add_argument(
            "--keepalive-seconds",
            type=float,
            default=settings.CUBINGCHINA_KEEPALIVE_SECONDS,
        )

    def handle(self, *args, **options):
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
                "metadata": {},
            },
        )
        run = IngestionRun.objects.create(mode=IngestionRun.Mode.CUBINGCHINA_WEBSOCKET)
        supervisor = CubingChinaLiveSupervisor(
            base_url=options["base_url"],
            websocket_endpoint=options["websocket_endpoint"],
            wca_base_url=options["wca_base_url"],
            discovery_interval=options["discovery_interval"],
            lookback_days=options["lookback_days"],
            lookahead_days=options["lookahead_days"],
            completion_grace_minutes=options["completion_grace_minutes"],
            max_connections=options["max_connections"],
            retry_base_seconds=options["retry_base_seconds"],
            retry_max_seconds=options["retry_max_seconds"],
            keepalive_seconds=options["keepalive_seconds"],
            run=run,
        )
        try:
            self.stdout.write(self.style.SUCCESS("Starting CubingChina live-results worker"))
            asyncio.run(supervisor.run_forever())
            run.status = IngestionRun.Status.SUCCEEDED
        except KeyboardInterrupt:
            supervisor.stop()
            run.status = IngestionRun.Status.SUCCEEDED
            self.stdout.write("CubingChina worker stopped")
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
                heartbeat_at=timezone.now(),
                last_stopped_at=timezone.now(),
            )
