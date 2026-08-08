from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.wca.record_validation import (
    RECORDS_PATH,
    fetch_wca_records,
    refresh_wca_record_validations,
)


class Command(BaseCommand):
    help = "Refresh official WCA record snapshots and revalidate CubingChina results"

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=settings.WCA_PUBLIC_BASE_URL)

    def handle(self, *args, **options):
        base_url = options["base_url"].rstrip("/")
        snapshot = refresh_wca_record_validations(
            fetch_wca_records(base_url),
            source_url=f"{base_url}{RECORDS_PATH}",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Validated against WCA snapshot {snapshot.pk} ({snapshot.record_count} records)"
            )
        )
