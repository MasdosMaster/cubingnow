import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.records.models import IngestionRun
from integrations.wca.graphql_client import WCALiveGraphQLClient
from integrations.wca.ingestion import ingest_record
from integrations.wca.queries import RECENT_RECORDS_QUERY


class Command(BaseCommand):
    help = "Fetch WCA Live recent records and persist them idempotently"

    def add_arguments(self, parser):
        parser.add_argument("--endpoint", default="https://live.worldcubeassociation.org/api")
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        while True:
            self._sync_once(options["endpoint"])
            if not options["watch"]:
                break
            time.sleep(max(options["interval"], 10))

    def _sync_once(self, endpoint):
        run = IngestionRun.objects.create(mode=IngestionRun.Mode.RECONCILIATION)
        try:
            data = WCALiveGraphQLClient(endpoint).execute(RECENT_RECORDS_QUERY)
            records = data.get("recentRecords", [])
            for payload in records:
                ingest_record(payload, run)
            run.status = IngestionRun.Status.SUCCEEDED
            self.stdout.write(
                self.style.SUCCESS(
                    f"Fetched {len(records)} records; stored {run.observations_count} new observations"
                )
            )
        except Exception as exc:
            run.status = IngestionRun.Status.FAILED
            run.error = str(exc)
            raise
        finally:
            run.finished_at = timezone.now()
            run.save()
