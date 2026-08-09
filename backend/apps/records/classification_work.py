import logging
import socket
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import Case, DateTimeField, F, Q, Value, When
from django.utils import timezone

from integrations.wca.record_validation import validate_scope_against_latest_snapshot

from .classification import reclassify_scope
from .models import CanonicalResult, ClassificationScopeWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedScope:
    work_id: int
    event_id: str
    kind: str
    target_version: int
    dirty_since: object
    oldest_observed_at: object


class ScopeAdvanced(Exception):
    """A newer fact arrived while a scope was being classified."""


def worker_identity() -> str:
    return f"{socket.gethostname()}:{uuid4().hex[:12]}"


def mark_classification_scopes_dirty(
    scopes: set[tuple[str, str]],
    *,
    observed_at=None,
    debounce_seconds: float = 1.0,
) -> int:
    """Version each affected scope once per committed ingestion batch.

    The debounce applies only when a clean scope first becomes dirty. Additional
    arrivals increment its version without continually postponing the work.
    """

    scopes = {(event_id, kind) for event_id, kind in scopes if event_id and kind}
    if not scopes:
        return 0
    now = timezone.now()
    observed_at = observed_at or now
    ready_at = now + timedelta(seconds=max(debounce_seconds, 0))
    for event_id, kind in sorted(scopes):
        work, _created = ClassificationScopeWork.objects.get_or_create(
            event_id=event_id,
            kind=kind,
        )
        ClassificationScopeWork.objects.filter(pk=work.pk).update(
            requested_version=F("requested_version") + 1,
            dirty_since=Case(
                When(dirty_since__isnull=True, then=Value(now)),
                default=F("dirty_since"),
                output_field=DateTimeField(),
            ),
            oldest_observed_at=Case(
                When(oldest_observed_at__isnull=True, then=Value(observed_at)),
                When(oldest_observed_at__gt=observed_at, then=Value(observed_at)),
                default=F("oldest_observed_at"),
                output_field=DateTimeField(),
            ),
            not_before=Case(
                When(dirty_since__isnull=True, then=Value(ready_at)),
                default=F("not_before"),
                output_field=DateTimeField(),
            ),
            updated_at=now,
        )
    return len(scopes)


@transaction.atomic
def claim_next_scope(worker_id: str, *, lease_seconds: int = 300) -> ClaimedScope | None:
    now = timezone.now()
    queryset = ClassificationScopeWork.objects.filter(
        requested_version__gt=F("processed_version"),
        not_before__lte=now,
    ).filter(Q(claim_expires_at__isnull=True) | Q(claim_expires_at__lt=now))
    queryset = queryset.order_by("dirty_since", "pk")
    if connection.features.has_select_for_update_skip_locked:
        queryset = queryset.select_for_update(skip_locked=True)
    else:
        queryset = queryset.select_for_update()
    work = queryset.first()
    if work is None:
        return None
    work.claimed_by = worker_id
    work.claim_expires_at = now + timedelta(seconds=max(lease_seconds, 30))
    work.last_started_at = now
    work.last_error = ""
    work.save(
        update_fields=[
            "claimed_by",
            "claim_expires_at",
            "last_started_at",
            "last_error",
            "updated_at",
        ]
    )
    return ClaimedScope(
        work_id=work.pk,
        event_id=work.event_id,
        kind=work.kind,
        target_version=work.requested_version,
        dirty_since=work.dirty_since,
        oldest_observed_at=work.oldest_observed_at,
    )


def process_claimed_scope(claim: ClaimedScope, worker_id: str) -> bool:
    started = monotonic()
    try:
        with transaction.atomic():
            validate_scope_against_latest_snapshot(claim.event_id, claim.kind)
            reclassify_scope(claim.event_id, claim.kind)
            result_count = CanonicalResult.objects.filter(
                event_id=claim.event_id,
                kind=claim.kind,
                status__in=[
                    CanonicalResult.Status.ACTIVE,
                    CanonicalResult.Status.CORRECTED,
                ],
            ).count()
            work = ClassificationScopeWork.objects.select_for_update().get(pk=claim.work_id)
            if work.requested_version != claim.target_version or work.claimed_by != worker_id:
                raise ScopeAdvanced
            completed_at = timezone.now()
            duration_ms = round((monotonic() - started) * 1000)
            work.processed_version = claim.target_version
            work.dirty_since = None
            work.oldest_observed_at = None
            work.not_before = None
            work.claimed_by = ""
            work.claim_expires_at = None
            work.last_completed_at = completed_at
            work.last_duration_ms = duration_ms
            work.last_result_count = result_count
            work.last_error = ""
            work.save(
                update_fields=[
                    "processed_version",
                    "dirty_since",
                    "oldest_observed_at",
                    "not_before",
                    "claimed_by",
                    "claim_expires_at",
                    "last_completed_at",
                    "last_duration_ms",
                    "last_result_count",
                    "last_error",
                    "updated_at",
                ]
            )
        lag_seconds = (
            (timezone.now() - claim.oldest_observed_at).total_seconds()
            if claim.oldest_observed_at
            else 0
        )
        logger.info(
            "classification_scope_completed event_id=%s kind=%s version=%d results=%d duration_ms=%d lag_seconds=%.3f",
            claim.event_id,
            claim.kind,
            claim.target_version,
            result_count,
            duration_ms,
            lag_seconds,
        )
        return True
    except ScopeAdvanced:
        ClassificationScopeWork.objects.filter(pk=claim.work_id, claimed_by=worker_id).update(
            claimed_by="",
            claim_expires_at=None,
            not_before=timezone.now(),
            updated_at=timezone.now(),
        )
        logger.info(
            "classification_scope_restarted_for_new_version event_id=%s kind=%s attempted_version=%d",
            claim.event_id,
            claim.kind,
            claim.target_version,
        )
        return False
    except Exception as exc:
        now = timezone.now()
        ClassificationScopeWork.objects.filter(pk=claim.work_id, claimed_by=worker_id).update(
            claimed_by="",
            claim_expires_at=None,
            not_before=now + timedelta(seconds=5),
            last_duration_ms=round((monotonic() - started) * 1000),
            last_error=str(exc),
            updated_at=now,
        )
        logger.exception(
            "classification_scope_failed event_id=%s kind=%s version=%d",
            claim.event_id,
            claim.kind,
            claim.target_version,
        )
        return False


def process_ready_scopes(worker_id: str, *, limit: int = 20) -> int:
    processed = 0
    for _index in range(max(limit, 1)):
        claim = claim_next_scope(worker_id)
        if claim is None:
            break
        processed += int(process_claimed_scope(claim, worker_id))
    return processed
