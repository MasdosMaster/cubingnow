import logging
import random
import socket
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (
    NotificationDelivery,
    NotificationEndpoint,
    NotificationEvent,
    NotificationPreference,
)
from .payloads import LEVEL_TO_NOTIFICATION_TYPE, build_record_payload
from .policies import DEFAULT_PREFERENCES
from .recipients import eligible_endpoints
from .types import DeliveryOutcome, DeliveryResult

logger = logging.getLogger(__name__)


def attach_endpoint_to_user(endpoint: NotificationEndpoint, user) -> NotificationEndpoint:
    """Future login hook; endpoint and user preferences remain independently resolvable."""

    endpoint.user = user
    endpoint.save(update_fields=["user", "updated_at"])
    return endpoint


def set_endpoint_preferences(endpoint: NotificationEndpoint, values: dict[str, bool]) -> dict:
    for notification_type, enabled in values.items():
        NotificationPreference.objects.update_or_create(
            endpoint=endpoint,
            notification_type=notification_type,
            defaults={"enabled": enabled},
        )
    return {
        notification_type: NotificationPreference.objects.filter(
            endpoint=endpoint,
            notification_type=notification_type,
            enabled=True,
        ).exists()
        for notification_type in DEFAULT_PREFERENCES
    }


def create_deliveries(event: NotificationEvent, endpoints=None) -> int:
    endpoints = endpoints if endpoints is not None else eligible_endpoints(event.notification_type)
    endpoints = endpoints.filter(activated_at__lte=event.created_at)
    endpoint_rows = list(endpoints.values_list("pk", "provider"))
    endpoint_ids = [endpoint_id for endpoint_id, _provider in endpoint_rows]
    existing_ids = set(
        NotificationDelivery.objects.filter(event=event, endpoint_id__in=endpoint_ids).values_list(
            "endpoint_id", flat=True
        )
    )
    deliveries = [
        NotificationDelivery(
            event=event,
            endpoint_id=endpoint_id,
            provider=provider,
        )
        for endpoint_id, provider in endpoint_rows
        if endpoint_id not in existing_ids
    ]
    NotificationDelivery.objects.bulk_create(deliveries, ignore_conflicts=True)
    return len(deliveries)


def _record_domain_values(record) -> dict:
    return {
        "record_level": record.record_level,
        "event_id": record.event_id,
        "formatted_result": record.formatted_result,
        "competitor_name": record.competitor_name,
        "country_code": record.country_code,
        "competition_name": record.competition_name,
        "competition_country_code": record.competition_country_code,
        "kind": record.kind,
    }


def _event_domain_values(payload: dict) -> dict:
    return {
        "record_level": payload.get("record_level"),
        "event_id": payload.get("event_id"),
        "formatted_result": payload.get("formatted_result"),
        "competitor_name": payload.get("competitor_name"),
        "country_code": payload.get("country_code"),
        "competition_name": payload.get("competition_name"),
        "competition_country_code": payload.get("competition_country_code"),
        "kind": payload.get("kind"),
    }


@transaction.atomic
def publish_record_notification(
    record,
    *,
    test: bool = False,
    endpoints=None,
) -> tuple[NotificationEvent, bool]:
    notification_type = LEVEL_TO_NOTIFICATION_TYPE[record.record_level]
    prefix = "test" if test else "record"
    deduplication_key = f"{prefix}:{record.canonical_key}"
    target_url = "/"
    candidate = NotificationEvent(
        notification_type=notification_type,
        deduplication_key=deduplication_key,
        target_url=target_url,
        occurred_at=record.detected_at,
        source_record=record if getattr(record, "pk", None) and not test else None,
    )
    candidate.payload = build_record_payload(
        event=candidate,
        record=record,
        target_url=target_url,
        test=test,
    )
    event, created = NotificationEvent.objects.get_or_create(
        deduplication_key=deduplication_key,
        defaults={
            "id": candidate.id,
            "notification_type": candidate.notification_type,
            "payload": candidate.payload,
            "target_url": candidate.target_url,
            "occurred_at": candidate.occurred_at,
            "source_record": candidate.source_record,
        },
    )
    if created:
        recipients = endpoints if endpoints is not None else eligible_endpoints(notification_type)
        recipients = recipients.filter(activated_at__lte=event.created_at)
        recipient_count = recipients.count()
        delivery_count = create_deliveries(event, recipients)
        logger.info(
            "notification_event_published notification_type=%s event_id=%s deduplicated=false eligible_endpoints=%d deliveries_created=%d",
            notification_type,
            event.id,
            recipient_count,
            delivery_count,
        )
        return event, True

    if _event_domain_values(event.payload) != _record_domain_values(record):
        if event.deliveries.filter(status=NotificationDelivery.Status.SENT).exists():
            event.correction_count += 1
            event.last_correction_at = timezone.now()
            event.payload = build_record_payload(
                event=event,
                record=record,
                target_url=event.target_url,
                test=test,
            )
            event.source_record = record if getattr(record, "pk", None) and not test else None
            event.save(
                update_fields=[
                    "payload",
                    "source_record",
                    "correction_count",
                    "last_correction_at",
                ]
            )
            logger.warning(
                "notification_post_send_correction event_id=%s notification_type=%s correction_count=%d",
                event.id,
                event.notification_type,
                event.correction_count,
            )
        else:
            event.payload = build_record_payload(
                event=event,
                record=record,
                target_url=event.target_url,
                test=test,
            )
            event.source_record = record if getattr(record, "pk", None) and not test else None
            event.save(update_fields=["payload", "source_record"])
            logger.info(
                "notification_pending_event_corrected event_id=%s notification_type=%s",
                event.id,
                event.notification_type,
            )
    logger.info(
        "notification_event_published notification_type=%s event_id=%s deduplicated=true eligible_endpoints=0 deliveries_created=0",
        notification_type,
        event.id,
    )
    return event, False


@transaction.atomic
def publish_processed_result_notification(record_level):
    """Publish one policy-selected processed record level idempotently."""

    from apps.records.models import (
        CanonicalResult,
        ProcessedResultRecordLevel,
        RecordValidation,
    )

    row = ProcessedResultRecordLevel.objects.select_related(
        "processed_result", "processed_result__canonical_result"
    ).get(pk=record_level.pk)
    result = row.processed_result
    head = CanonicalResult.objects.select_for_update().get(pk=result.canonical_result_id)
    trusted_result = (
        result.validation_status == CanonicalResult.ValidationStatus.VERIFIED
        and result.validation_reason == "trusted_source_observation"
    )
    independently_verified = RecordValidation.objects.filter(
        result_id=result.canonical_result_id,
        level=row.record_level,
        result_value=result.value,
        status=RecordValidation.Status.VERIFIED,
    ).exists()
    if (
        not result.is_valid_result
        or not (trusted_result or independently_verified)
        or head.revision != result.canonical_revision
        or row.record_level not in LEVEL_TO_NOTIFICATION_TYPE
        or row.classification_outcome == "none"
        or row.recognition_status != "recognized"
    ):
        return None, False
    proxy = SimpleNamespace(
        pk=None,
        canonical_key=(
            f"processed:{result.canonical_result_id}:{result.canonical_revision}:"
            f"{row.record_level}"
        ),
        record_level=row.record_level,
        event_id=result.event_id,
        event_name=result.event_name,
        formatted_result=result.formatted_result,
        competitor_name=result.competitor_name,
        country_code=result.country_code,
        competition_country_code=result.competition_country_code,
        competition_name=result.competition_name,
        kind=result.kind,
        detected_at=result.classification_at,
        source_payload={},
    )
    event, created = publish_record_notification(proxy)
    if event.processed_record_level_id != row.pk:
        event.processed_record_level = row
        event.save(update_fields=["processed_record_level"])
    return event, created


def publish_processed_result_after_commit(processed_result_id: int) -> None:
    def publish_safely():
        from apps.records.models import ProcessedResultRecordLevel

        rows = (
            ProcessedResultRecordLevel.objects.select_related("processed_result")
            .filter(
                processed_result_id=processed_result_id,
                record_level__in=LEVEL_TO_NOTIFICATION_TYPE,
                classification_outcome__in=["broken", "tied"],
                recognition_status="recognized",
            )
            .order_by(
                models.Case(
                    models.When(record_level="WR", then=0),
                    models.When(record_level="CR", then=1),
                    models.When(record_level="NR", then=2),
                    default=3,
                )
            )
            [:3]
        )
        for row in rows:
            try:
                event, _created = publish_processed_result_notification(row)
                if event is not None:
                    return
            except Exception:
                logger.exception(
                    "processed_result_notification_publication_failed processed_result_id=%s",
                    processed_result_id,
                )
                return

    transaction.on_commit(publish_safely)


def record_source_is_enabled(ingestion_method: str) -> bool:
    configured = settings.PUSH_RECORD_EVENT_SOURCE
    return configured == ingestion_method or configured == "all"


def publish_record_after_commit(record_id: int, ingestion_method: str) -> None:
    # Provider-tag projections are retained for diagnostics only.  Notifications
    # are published exclusively from ProcessedResultRecordLevel classification.
    return None


def worker_identifier() -> str:
    return settings.PUSH_WORKER_IDENTIFIER or f"{socket.gethostname()}:{id(connection)}"


def claim_due_deliveries(*, batch_size: int | None = None, claimed_by: str | None = None):
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.PUSH_WORKER_CLAIM_TIMEOUT_SECONDS)
    due = (
        Q(status__in=[NotificationDelivery.Status.PENDING, NotificationDelivery.Status.RETRY])
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    ) | Q(
        status=NotificationDelivery.Status.PROCESSING,
        last_attempt_at__lte=cutoff,
    )
    batch_size = batch_size or settings.PUSH_WORKER_BATCH_SIZE
    claimed_by = claimed_by or worker_identifier()
    with transaction.atomic():
        queryset = NotificationDelivery.objects.filter(
            due,
            provider=settings.PUSH_NOTIFICATION_PROVIDER,
        ).order_by("created_at", "pk")
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        deliveries = list(queryset[:batch_size])
        for delivery in deliveries:
            delivery.status = NotificationDelivery.Status.PROCESSING
            delivery.attempt_count += 1
            delivery.last_attempt_at = now
            delivery.claimed_by = claimed_by
            delivery.save(
                update_fields=[
                    "status",
                    "attempt_count",
                    "last_attempt_at",
                    "claimed_by",
                    "updated_at",
                ]
            )
    logger.info("notification_batch_claimed count=%d worker=%s", len(deliveries), claimed_by)
    return [delivery.pk for delivery in deliveries]


def _retry_delay(attempt_count: int) -> timedelta:
    schedule = settings.PUSH_WORKER_RETRY_SCHEDULE_SECONDS or (60,)
    seconds = schedule[min(max(attempt_count - 1, 0), len(schedule) - 1)]
    return timedelta(seconds=max(1, seconds * random.uniform(0.8, 1.2)))


def _schedule_retry(delivery: NotificationDelivery, result: DeliveryResult) -> None:
    now = timezone.now()
    delivery.last_error_code = result.error_code
    delivery.last_error_message = result.error_message[:255]
    delivery.claimed_by = ""
    if delivery.attempt_count >= settings.PUSH_WORKER_MAX_ATTEMPTS:
        delivery.status = NotificationDelivery.Status.PERMANENTLY_FAILED
        delivery.next_attempt_at = None
        delivery.last_error_code = "maximum_attempts"
        delivery.last_error_message = "Maximum delivery attempts reached"
        log_message = "notification_delivery_max_attempts"
    else:
        delivery.status = NotificationDelivery.Status.RETRY
        delivery.next_attempt_at = result.retry_at or now + _retry_delay(delivery.attempt_count)
        log_message = "notification_transient_failure_scheduled"
    delivery.save(
        update_fields=[
            "status",
            "next_attempt_at",
            "last_error_code",
            "last_error_message",
            "claimed_by",
            "updated_at",
        ]
    )
    logger.warning(
        "%s delivery_id=%s endpoint_id=%s attempt=%d error_code=%s",
        log_message,
        delivery.pk,
        delivery.endpoint_id,
        delivery.attempt_count,
        delivery.last_error_code,
    )


@transaction.atomic
def _record_delivery_result(delivery_id: int, result: DeliveryResult) -> None:
    delivery = (
        NotificationDelivery.objects.select_for_update()
        .select_related("endpoint")
        .get(pk=delivery_id)
    )
    if delivery.status != NotificationDelivery.Status.PROCESSING:
        return
    endpoint = delivery.endpoint
    now = timezone.now()

    if result.outcome == DeliveryOutcome.SUCCESS:
        delivery.status = NotificationDelivery.Status.SENT
        delivery.sent_at = now
        delivery.next_attempt_at = None
        delivery.claimed_by = ""
        delivery.last_error_code = ""
        delivery.last_error_message = ""
        delivery.provider_message_id = result.provider_message_id
        delivery.save()
        endpoint.last_success_at = now
        endpoint.consecutive_failure_count = 0
        endpoint.save(update_fields=["last_success_at", "consecutive_failure_count", "updated_at"])
        logger.info(
            "notification_delivery_succeeded delivery_id=%s event_id=%s endpoint_id=%s",
            delivery.pk,
            delivery.event_id,
            endpoint.id,
        )
        return

    endpoint.last_failure_at = now
    endpoint.consecutive_failure_count += 1
    endpoint.save(update_fields=["last_failure_at", "consecutive_failure_count", "updated_at"])
    if result.outcome == DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE:
        endpoint.active = False
        endpoint.deactivated_at = now
        endpoint.deactivation_reason = result.error_code or "invalid_endpoint"
        endpoint.save(
            update_fields=[
                "active",
                "deactivated_at",
                "deactivation_reason",
                "updated_at",
            ]
        )
        NotificationDelivery.objects.filter(endpoint=endpoint).filter(
            status__in=[
                NotificationDelivery.Status.PENDING,
                NotificationDelivery.Status.RETRY,
                NotificationDelivery.Status.PROCESSING,
            ]
        ).update(
            status=NotificationDelivery.Status.PERMANENTLY_FAILED,
            next_attempt_at=None,
            claimed_by="",
            last_error_code=result.error_code,
            last_error_message=result.error_message[:255],
            updated_at=now,
        )
        logger.warning(
            "notification_endpoint_deactivated endpoint_id=%s error_code=%s",
            endpoint.id,
            result.error_code,
        )
        return

    _schedule_retry(delivery, result)


def process_claimed_delivery(delivery_id: int, provider) -> None:
    try:
        delivery = NotificationDelivery.objects.select_related(
            "event", "endpoint", "endpoint__webpush_subscription"
        ).get(pk=delivery_id)
    except NotificationDelivery.DoesNotExist:
        return
    if delivery.status != NotificationDelivery.Status.PROCESSING:
        return
    if not delivery.endpoint.active:
        NotificationDelivery.objects.filter(
            pk=delivery_id, status=NotificationDelivery.Status.PROCESSING
        ).update(
            status=NotificationDelivery.Status.CANCELLED,
            next_attempt_at=None,
            claimed_by="",
            last_error_code="endpoint_inactive",
            last_error_message="Endpoint was inactive before delivery",
            updated_at=timezone.now(),
        )
        return
    try:
        result = provider.send(endpoint=delivery.endpoint, payload=delivery.event.payload)
    except Exception:  # noqa: BLE001 - provider bugs must not stop the worker loop
        logger.error(
            "notification_provider_unexpected_failure delivery_id=%s endpoint_id=%s",
            delivery.pk,
            delivery.endpoint_id,
        )
        result = DeliveryResult(
            DeliveryOutcome.APPLICATION_FAILURE,
            "provider_internal_error",
            "Notification provider failed unexpectedly",
        )
    _record_delivery_result(delivery_id, result)


def release_claimed_deliveries(delivery_ids, *, claimed_by: str) -> int:
    if not delivery_ids:
        return 0
    return NotificationDelivery.objects.filter(
        pk__in=delivery_ids,
        status=NotificationDelivery.Status.PROCESSING,
        claimed_by=claimed_by,
    ).update(
        status=NotificationDelivery.Status.RETRY,
        attempt_count=F("attempt_count") - 1,
        next_attempt_at=timezone.now(),
        last_attempt_at=None,
        claimed_by="",
        updated_at=timezone.now(),
    )


def process_due_batch(
    provider,
    *,
    batch_size: int | None = None,
    claimed_by: str | None = None,
    should_stop=None,
):
    claimed_by = claimed_by or worker_identifier()
    delivery_ids = claim_due_deliveries(batch_size=batch_size, claimed_by=claimed_by)
    processed = 0
    for index, delivery_id in enumerate(delivery_ids):
        if should_stop and should_stop():
            released = release_claimed_deliveries(
                delivery_ids[index:],
                claimed_by=claimed_by,
            )
            logger.info(
                "notification_worker_claims_released count=%d worker=%s",
                released,
                claimed_by,
            )
            break
        process_claimed_delivery(delivery_id, provider)
        processed += 1
    return processed
