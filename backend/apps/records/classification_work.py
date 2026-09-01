import logging
import socket
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .classification import classify_revision
from .models import BaselineMetadata, ClassificationWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedRevision:
    work_id: int
    canonical_result_revision_id: int
    canonical_result_id: int
    revision: int
    action: str


def worker_identity() -> str:
    return f"{socket.gethostname()}:{uuid4().hex[:12]}"


@transaction.atomic
def claim_next_work(worker_id: str, *, lease_seconds: int = 300) -> ClaimedRevision | None:
    """Claim a revision while excluding any result whose earlier work is unsettled."""

    # Migration backfill can enqueue revisions before the initial public-export
    # install. Keep them pending rather than treating an empty baseline as infinity.
    if not BaselineMetadata.objects.filter(is_active=True).exists():
        return None
    now = timezone.now()
    unsettled_earlier = ClassificationWork.objects.filter(
        canonical_result_id=OuterRef("canonical_result_id"),
        revision__lt=OuterRef("revision"),
        status__in=[
            ClassificationWork.Status.PENDING,
            ClassificationWork.Status.PROCESSING,
            ClassificationWork.Status.FAILED,
        ],
    )
    queryset = (
        ClassificationWork.objects.annotate(_has_unsettled_earlier=Exists(unsettled_earlier))
        .filter(_has_unsettled_earlier=False)
        .filter(
            Q(status__in=[ClassificationWork.Status.PENDING, ClassificationWork.Status.FAILED])
            | Q(
                status=ClassificationWork.Status.PROCESSING,
                claim_expires_at__lt=now,
            )
        )
        .order_by("created_at", "pk")
    )
    if connection.features.has_select_for_update_skip_locked:
        queryset = queryset.select_for_update(skip_locked=True)
    else:
        queryset = queryset.select_for_update()
    work = queryset.first()
    if work is None:
        return None
    work.status = ClassificationWork.Status.PROCESSING
    work.attempts += 1
    work.claimed_by = worker_id
    work.claimed_at = now
    work.claim_expires_at = now + timedelta(seconds=max(lease_seconds, 30))
    work.last_error = ""
    work.save(
        update_fields=[
            "status",
            "attempts",
            "claimed_by",
            "claimed_at",
            "claim_expires_at",
            "last_error",
        ]
    )
    return ClaimedRevision(
        work_id=work.pk,
        canonical_result_revision_id=work.canonical_result_revision_id,
        canonical_result_id=work.canonical_result_id,
        revision=work.revision,
        action=work.action,
    )


def process_claimed_work(claim: ClaimedRevision, worker_id: str) -> bool:
    try:
        work = ClassificationWork.objects.select_related(
            "canonical_result_revision", "canonical_result_revision__canonical_result"
        ).get(pk=claim.work_id)
        is_current = classify_revision(work.canonical_result_revision)
        with transaction.atomic():
            locked = ClassificationWork.objects.select_for_update().get(pk=claim.work_id)
            if (
                locked.status != ClassificationWork.Status.PROCESSING
                or locked.claimed_by != worker_id
            ):
                return False
            locked.status = (
                ClassificationWork.Status.COMPLETED
                if is_current
                else ClassificationWork.Status.STALE
            )
            locked.completed_at = timezone.now()
            locked.claimed_by = ""
            locked.claim_expires_at = None
            locked.last_error = ""
            locked.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "claimed_by",
                    "claim_expires_at",
                    "last_error",
                ]
            )
        logger.info(
            "classification_revision_completed canonical_result_id=%s revision=%s stale=%s",
            claim.canonical_result_id,
            claim.revision,
            not is_current,
        )
        return True
    except Exception as exc:
        ClassificationWork.objects.filter(pk=claim.work_id, claimed_by=worker_id).update(
            status=ClassificationWork.Status.FAILED,
            claimed_by="",
            claim_expires_at=None,
            last_error=str(exc),
        )
        logger.exception(
            "classification_revision_failed canonical_result_id=%s revision=%s",
            claim.canonical_result_id,
            claim.revision,
        )
        return False


def process_ready_work(worker_id: str, *, limit: int = 20) -> int:
    processed = 0
    for _index in range(max(limit, 1)):
        claim = claim_next_work(worker_id)
        if claim is None:
            break
        processed += int(process_claimed_work(claim, worker_id))
    return processed
