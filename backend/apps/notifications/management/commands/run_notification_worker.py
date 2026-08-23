import logging
import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.notifications.providers import get_push_provider
from apps.notifications.services import process_due_batch, worker_identifier
from apps.records.worker_recovery import TRANSIENT_DATABASE_ERRORS, prepare_database_retry

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued notification deliveries using the configured push provider"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one due batch and exit")
        parser.add_argument("--batch-size", type=int, default=settings.PUSH_WORKER_BATCH_SIZE)
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=settings.PUSH_WORKER_POLL_INTERVAL_SECONDS,
        )

    def handle(self, *args, **options):
        stop = threading.Event()

        def request_stop(signum, frame):
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        provider = get_push_provider()
        identifier = worker_identifier()
        database_attempt = 0
        logger.info("notification_worker_started worker=%s", identifier)
        try:
            while not stop.is_set():
                try:
                    processed = process_due_batch(
                        provider,
                        batch_size=max(1, options["batch_size"]),
                        claimed_by=identifier,
                        should_stop=stop.is_set,
                    )
                    database_attempt = 0
                except TRANSIENT_DATABASE_ERRORS as exc:
                    if options["once"]:
                        raise
                    database_attempt += 1
                    stop.wait(
                        prepare_database_retry(
                            database_attempt,
                            error=exc,
                            logger=logger,
                            worker="notification_worker",
                        )
                    )
                    continue
                if options["once"]:
                    break
                if processed == 0:
                    stop.wait(max(0.1, options["poll_interval"]))
        finally:
            logger.info("notification_worker_stopped worker=%s", identifier)
