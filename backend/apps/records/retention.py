import logging
from datetime import timedelta

from django.utils import timezone

from .models import SourceObservation

logger = logging.getLogger(__name__)


def purge_expired_source_observations(*, retention_days: int, batch_size: int = 1_000) -> int:
    """Delete expired raw frames in small transactions.

    Normalized ResultObservation rows survive because their raw-observation foreign
    key uses SET_NULL. Keeping batches small avoids a single large delete and its
    associated lock and memory pressure.
    """

    if retention_days <= 0:
        return 0

    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_total = 0
    batch_size = max(batch_size, 1)
    while True:
        expired_ids = list(
            SourceObservation.objects.filter(observed_at__lt=cutoff)
            .order_by("pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        if not expired_ids:
            break
        deleted, _details = SourceObservation.objects.filter(pk__in=expired_ids).delete()
        deleted_total += deleted
    logger.info(
        "source_observation_retention_completed retention_days=%d deleted=%d",
        retention_days,
        deleted_total,
    )
    return deleted_total
