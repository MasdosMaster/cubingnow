from django.core.management.base import BaseCommand

from apps.records.classification import rebuild_classification_from_scratch


class Command(BaseCommand):
    help = "Rebuild revision classification from the active WCA export baseline"

    def add_arguments(self, parser):
        parser.add_argument(
            "--suppress-notifications",
            action="store_true",
            help="Rebuild processed revisions without publishing deliveries",
        )

    def handle(self, *args, **options):
        rebuild_classification_from_scratch(
            publish_notifications=not options["suppress_notifications"]
        )
        self.stdout.write(self.style.SUCCESS("Revision classification rebuilt"))
