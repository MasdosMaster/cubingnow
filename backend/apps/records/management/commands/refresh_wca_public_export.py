from django.conf import settings
from django.core.management.base import BaseCommand

from apps.records.baseline_export import refresh_wca_baseline


class Command(BaseCommand):
    help = "Download the WCA v2 SQL export and atomically rebuild record state"

    def add_arguments(self, parser):
        parser.add_argument("--url", default=settings.WCA_PUBLIC_EXPORT_URL)
        parser.add_argument("--timeout", type=float, default=120.0)

    def handle(self, *args, **options):
        metadata = refresh_wca_baseline(url=options["url"], timeout=options["timeout"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Installed WCA export {metadata.source_filename} "
                f"({metadata.source_version[:12]})"
            )
        )
