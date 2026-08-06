import uuid
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.notifications.payloads import LEVEL_TO_NOTIFICATION_TYPE
from apps.notifications.recipients import eligible_endpoints
from apps.notifications.services import publish_record_notification


class Command(BaseCommand):
    help = "Queue an obvious synthetic record notification without changing record data"

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--endpoint", type=uuid.UUID, help="Public-safe endpoint UUID")
        target.add_argument("--all-active", action="store_true")
        parser.add_argument("--allow-bulk", action="store_true")
        parser.add_argument("--level", choices=["WR", "CR", "NR"], default="WR")

    def handle(self, *args, **options):
        if options["all_active"] and (not settings.DEBUG or not options["allow_bulk"]):
            raise CommandError("Bulk test sends require DEBUG and --allow-bulk")

        notification_type = LEVEL_TO_NOTIFICATION_TYPE[options["level"]]
        endpoints = eligible_endpoints(notification_type)
        if options["endpoint"]:
            endpoints = endpoints.filter(pk=options["endpoint"])
        if not endpoints.exists():
            raise CommandError("No active eligible endpoint matched the requested target")

        now = timezone.now()
        synthetic_id = uuid.uuid4()
        record = SimpleNamespace(
            pk=None,
            canonical_key=f"manual:{synthetic_id}",
            record_level=options["level"],
            event_id="333",
            event_name="3x3x3 Cube",
            formatted_result="3.99",
            competitor_name="CubingNow Test Cuber",
            competition_name="CubingNow Test Event",
            kind="single",
            detected_at=now,
        )
        event, _created = publish_record_notification(record, test=True, endpoints=endpoints)
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued test event {event.id} with {event.deliveries.count()} delivery row(s)"
            )
        )
