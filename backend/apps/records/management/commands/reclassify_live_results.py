from django.core.management.base import BaseCommand

from apps.records.classification import reclassify_all


class Command(BaseCommand):
    help = "Replay canonical live results over current record and personal-best baselines"

    def handle(self, *args, **options):
        reclassify_all()
        self.stdout.write(self.style.SUCCESS("Canonical live results reclassified"))
