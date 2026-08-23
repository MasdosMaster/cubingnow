import logging
import random

from django.conf import settings
from django.db import InterfaceError, OperationalError, close_old_connections

TRANSIENT_DATABASE_ERRORS = (InterfaceError, OperationalError)


def prepare_database_retry(
    attempt: int,
    *,
    error: BaseException,
    logger: logging.Logger,
    worker: str,
) -> float:
    """Discard broken connections and return a jittered exponential retry delay."""

    close_old_connections()
    ceiling = min(
        settings.WCA_RETRY_MAX_SECONDS,
        settings.WCA_RETRY_BASE_SECONDS * (2 ** min(max(attempt - 1, 0), 10)),
    )
    delay = random.uniform(0, max(ceiling, 0))
    logger.warning(
        "%s_database_retry_scheduled attempt=%d delay_seconds=%.2f error=%s",
        worker,
        attempt,
        delay,
        error,
    )
    return delay


def best_effort_database_write(callback, *, logger: logging.Logger, action: str) -> None:
    """Do not let telemetry or shutdown bookkeeping terminate a worker."""

    try:
        callback()
    except TRANSIENT_DATABASE_ERRORS:
        close_old_connections()
        logger.warning("database_write_skipped action=%s", action, exc_info=True)
