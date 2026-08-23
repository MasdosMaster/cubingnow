import logging
import time

from django.core.management.base import BaseCommand

from apps.records.classification_work import process_ready_scopes, worker_identity
from apps.records.worker_recovery import TRANSIENT_DATABASE_ERRORS, prepare_database_retry

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process durable event/kind classification work in batched passes"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=1.0)
        parser.add_argument("--batch-size", type=int, default=20)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        worker_id = worker_identity()
        interval = max(options["interval"], 0.1)
        database_attempt = 0
        while True:
            try:
                processed = process_ready_scopes(worker_id, limit=options["batch_size"])
                database_attempt = 0
            except TRANSIENT_DATABASE_ERRORS as exc:
                if options["once"]:
                    raise
                database_attempt += 1
                time.sleep(
                    prepare_database_retry(
                        database_attempt,
                        error=exc,
                        logger=logger,
                        worker="classification_worker",
                    )
                )
                continue
            if options["once"]:
                self.stdout.write(f"Processed {processed} classification scopes")
                return
            if processed == 0:
                time.sleep(interval)
