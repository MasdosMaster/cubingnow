from django.core.management.base import BaseCommand

from apps.records.classification import reclassify_all


class Command(BaseCommand):
    help = "Replay canonical live results over current record and personal-best baselines"

    def add_arguments(self, parser):
        parser.add_argument(
            "--suppress-notifications",
            action="store_true",
            help="Rebuild achievements and qualifications without publishing deliveries",
        )

    def handle(self, *args, **options):
        reclassify_all(publish_notifications=not options["suppress_notifications"])
        self.stdout.write(self.style.SUCCESS("Canonical live results reclassified"))
