from django.conf import settings
from django.core.management.base import BaseCommand

from apps.records.retention import purge_expired_source_observations


class Command(BaseCommand):
    help = "Delete raw source observations older than the configured retention window"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=settings.SOURCE_OBSERVATION_RETENTION_DAYS,
        )
        parser.add_argument("--batch-size", type=int, default=1_000)

    def handle(self, *args, **options):
        deleted = purge_expired_source_observations(
            retention_days=options["days"],
            batch_size=options["batch_size"],
        )
        self.stdout.write(f"Deleted {deleted} expired source observations")
