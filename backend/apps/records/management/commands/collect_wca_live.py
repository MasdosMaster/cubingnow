import asyncio
import os

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.records.models import IngestionRun
from integrations.wca.ingestion import ingest_record
from integrations.wca.subscriptions import WCALiveSubscriptionClient


class Command(BaseCommand):
    help = "Maintain a WCA Live GraphQL subscription and ingest record observations"

    def handle(self, *args, **options):
        endpoint = os.getenv("WCA_LIVE_WS_URL")
        query = os.getenv("WCA_LIVE_SUBSCRIPTION_QUERY")
        if not endpoint or not query:
            raise CommandError("WCA_LIVE_WS_URL and WCA_LIVE_SUBSCRIPTION_QUERY are required")
        asyncio.run(self._run(endpoint, query))

    async def _run(self, endpoint, query):
        run = await sync_to_async(IngestionRun.objects.create)(
            mode=IngestionRun.Mode.SUBSCRIPTION
        )
        client = WCALiveSubscriptionClient(endpoint)
        try:
            async for payload in client.subscribe(query):
                await sync_to_async(ingest_record, thread_sensitive=True)(payload, run)
        except Exception as exc:
            run.status = IngestionRun.Status.FAILED
            run.error = str(exc)
            raise
        finally:
            run.finished_at = timezone.now()
            if not run.error:
                run.status = IngestionRun.Status.SUCCEEDED
            await sync_to_async(run.save)()

