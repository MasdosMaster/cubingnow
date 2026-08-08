import base64
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from pywebpush import WebPushException
from rest_framework.test import APIClient

from apps.notifications.models import (
    NotificationDelivery,
    NotificationEndpoint,
    NotificationEvent,
    NotificationPreference,
    NotificationProvider,
    NotificationType,
    WebPushSubscription,
)
from apps.notifications.providers.webpush import WebPushProvider
from apps.notifications.recipients import eligible_endpoints
from apps.notifications.services import (
    attach_endpoint_to_user,
    claim_due_deliveries,
    create_deliveries,
    process_claimed_delivery,
    process_due_batch,
    publish_record_notification,
    set_endpoint_preferences,
)
from apps.notifications.types import DeliveryOutcome, DeliveryResult
from apps.records.models import RecentRecordObservation
from integrations.wca_live.ingestion import persist_record_candidate
from integrations.wca_live.schemas import RecordCandidate

TEST_VAPID_PRIVATE_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE"
TEST_VAPID_PUBLIC_KEY = (
    "BGsX0fLhLEJH-Lzm5WOkQPJ3A32BLeszoPShOUXYmMKWT-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU"
)


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def subscription_keys(seed=1):
    return {
        "p256dh": encoded(bytes([4]) + bytes([seed]) * 64),
        "auth": encoded(bytes([seed]) * 16),
    }


def endpoint(*, preferences=None, active=True):
    item = NotificationEndpoint(provider=NotificationProvider.WEBPUSH, active=active)
    item.issue_management_token()
    item.save()
    WebPushSubscription.objects.create(
        endpoint=item,
        endpoint_url=f"https://push.example.test/{item.id}",
        **subscription_keys(),
    )
    set_endpoint_preferences(
        item,
        preferences
        or {
            NotificationType.RECORD_WR: True,
            NotificationType.RECORD_CR: True,
            NotificationType.RECORD_NR: True,
        },
    )
    return item


def record(*, level="WR", canonical_key="wca|TestOpen2026|2020TEST01|333|1|single|WR"):
    return SimpleNamespace(
        pk=None,
        canonical_key=canonical_key,
        record_level=level,
        event_id="333",
        event_name="3x3x3 Cube",
        formatted_result="3.91",
        competitor_name="Test Cuber",
        country_code="NL",
        competition_name="Test Open 2026",
        kind="single",
        detected_at=timezone.now(),
    )


def record_candidate():
    now = timezone.now()
    return RecordCandidate(
        stable_result_identity="source-result-1",
        wca_live_record_id="recent-record-1",
        wca_live_result_id="source-result-1",
        wca_live_competition_id="live-competition-1",
        wca_competition_id="TestOpen2026",
        competition_name="Test Open 2026",
        competition_start_date=date(2026, 8, 6),
        competition_end_date=date(2026, 8, 7),
        round_id="round-1",
        round_number=1,
        round_name="Final",
        event_id="333",
        event_name="3x3x3 Cube",
        competitor_name="Test Cuber",
        competitor_wca_id="2020TEST01",
        competitor_wca_live_id="person-1",
        country_code="NL",
        kind="single",
        raw_result=391,
        record_level="WR",
        source_url="https://live.worldcubeassociation.org/round-1",
        source_update_timestamp=now,
        observed_at=now,
    )


def event_and_delivery(item=None):
    item = item or endpoint()
    event, _ = publish_record_notification(record())
    return event, NotificationDelivery.objects.get(event=event, endpoint=item)


@pytest.mark.django_db
def test_national_record_notification_uses_nr_and_country_code():
    event, created = publish_record_notification(
        record(level="NR", canonical_key="wca|TestOpen2026|2020TEST01|333|1|single|NR")
    )

    assert created is True
    assert event.payload["title"] == "New 3×3×3 NR (NL)"
    assert event.payload["country_code"] == "NL"


@pytest.mark.django_db
def test_webpush_url_and_one_to_one_are_unique():
    first = endpoint()
    second = NotificationEndpoint()
    second.issue_management_token()
    second.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        WebPushSubscription.objects.create(
            endpoint=second,
            endpoint_url=first.webpush_subscription.endpoint_url,
            **subscription_keys(2),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        WebPushSubscription.objects.create(
            endpoint=first,
            endpoint_url="https://push.example.test/second",
            **subscription_keys(2),
        )


@pytest.mark.django_db
def test_guest_endpoint_can_later_attach_to_user():
    item = endpoint()
    assert item.user is None
    user = get_user_model().objects.create_user(username="future-user")
    attach_endpoint_to_user(item, user)
    item.refresh_from_db()
    assert item.user == user


@pytest.mark.django_db
def test_preference_requires_exactly_one_owner_and_is_unique():
    item = endpoint()
    user = get_user_model().objects.create_user(username="preference-user")
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationPreference.objects.create(
            notification_type=NotificationType.RECORD_WR,
            enabled=True,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationPreference.objects.create(
            endpoint=item,
            user=user,
            notification_type=NotificationType.RECORD_WR,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationPreference.objects.create(
            endpoint=item,
            notification_type=NotificationType.RECORD_WR,
        )
    NotificationPreference.objects.create(
        user=user,
        notification_type=NotificationType.RECORD_WR,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationPreference.objects.create(
            user=user,
            notification_type=NotificationType.RECORD_WR,
        )


@pytest.mark.django_db
def test_event_and_delivery_deduplication_constraints():
    item = endpoint()
    event, _ = publish_record_notification(record())
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationEvent.objects.create(
            notification_type=NotificationType.RECORD_WR,
            deduplication_key=event.deduplication_key,
            payload={},
            target_url="/",
            occurred_at=timezone.now(),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationDelivery.objects.create(
            event=event,
            endpoint=item,
            provider=NotificationProvider.WEBPUSH,
        )


def csrf_client():
    client = APIClient(enforce_csrf_checks=True)
    response = client.get("/api/notifications/config/")
    return client, response.json()["csrf_token"]


def subscription_payload(**changes):
    payload = {
        "subscription": {
            "endpoint": "https://push.example.test/browser-capability",
            "expirationTime": None,
            "keys": subscription_keys(),
        },
        "preferences": {
            "record_wr": True,
            "record_cr": False,
            "record_nr": True,
        },
    }
    payload.update(changes)
    return payload


@pytest.mark.django_db
@override_settings(
    WEB_PUSH_VAPID_PUBLIC_KEY=TEST_VAPID_PUBLIC_KEY,
    WEB_PUSH_VAPID_PRIVATE_KEY=TEST_VAPID_PRIVATE_KEY,
)
def test_config_api_exposes_only_public_push_configuration():
    response = APIClient().get("/api/notifications/config/")
    assert response.status_code == 200
    assert response.json()["web_push_configured"] is True
    assert response.json()["vapid_public_key"] == TEST_VAPID_PUBLIC_KEY
    serialized = str(response.json())
    assert TEST_VAPID_PRIVATE_KEY not in serialized
    assert "record_wr" in serialized and "record_cr" in serialized and "record_nr" in serialized


@pytest.mark.django_db
@override_settings(
    WEB_PUSH_VAPID_PUBLIC_KEY=TEST_VAPID_PUBLIC_KEY,
    WEB_PUSH_VAPID_PRIVATE_KEY=TEST_VAPID_PRIVATE_KEY,
)
def test_guest_subscription_api_is_idempotent_updates_keys_and_hides_secrets():
    client, csrf = csrf_client()
    response = client.post(
        "/api/notifications/subscriptions/",
        subscription_payload(),
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["active"] is True
    assert body["preferences"]["record_cr"] is False
    assert "endpoint" not in body and "p256dh" not in body and "auth" not in body
    assert "management_token" in body

    updated = subscription_payload(management_token=body["management_token"])
    updated["subscription"]["keys"] = subscription_keys(2)
    response = client.post(
        "/api/notifications/subscriptions/",
        updated,
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    assert NotificationEndpoint.objects.count() == 1
    stored = WebPushSubscription.objects.get()
    assert (stored.p256dh, stored.auth) == tuple(subscription_keys(2).values())


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {"subscription": {"keys": {"p256dh": "key", "auth": "auth"}}},
        subscription_payload(
            subscription={
                "endpoint": "https://push.example.test/no-p256dh",
                "keys": {"auth": "auth"},
            }
        ),
        subscription_payload(
            subscription={
                "endpoint": "https://push.example.test/no-auth",
                "keys": {"p256dh": "key"},
            }
        ),
        subscription_payload(
            subscription={
                "endpoint": "https://push.example.test/oversized",
                "keys": {"p256dh": "x" * 513, "auth": "auth"},
            }
        ),
        subscription_payload(preferences={"record_unknown": True}),
    ],
)
def test_subscription_api_rejects_invalid_payloads(payload):
    client, csrf = csrf_client()
    response = client.post(
        "/api/notifications/subscriptions/",
        payload,
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_subscription_mutations_require_csrf():
    client = APIClient(enforce_csrf_checks=True)
    response = client.post(
        "/api/notifications/subscriptions/", subscription_payload(), format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_preferences_can_change_and_subscription_can_be_disabled():
    client, csrf = csrf_client()
    created = client.post(
        "/api/notifications/subscriptions/",
        subscription_payload(),
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    ).json()
    credentials = {
        "endpoint_id": created["endpoint_id"],
        "management_token": created["management_token"],
    }
    response = client.patch(
        "/api/notifications/preferences/",
        {**credentials, "preferences": {"record_cr": True, "record_nr": False}},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    assert response.json()["preferences"] == {
        "record_wr": True,
        "record_cr": True,
        "record_nr": False,
    }
    response = client.delete(
        "/api/notifications/subscriptions/",
        credentials,
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 204
    assert NotificationEndpoint.objects.get().active is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "notification_type"),
    [
        ("WR", NotificationType.RECORD_WR),
        ("CR", NotificationType.RECORD_CR),
        ("NR", NotificationType.RECORD_NR),
    ],
)
def test_record_levels_publish_one_event(level, notification_type):
    event, created = publish_record_notification(
        record(level=level, canonical_key=f"canonical-{level}")
    )
    assert created and event.notification_type == notification_type


@pytest.mark.django_db
def test_canonical_event_deduplicates_sources_and_deliveries():
    item = endpoint()
    first, created = publish_record_notification(record())
    second, created_again = publish_record_notification(record())
    assert first == second
    assert created and not created_again
    assert NotificationEvent.objects.count() == 1
    assert NotificationDelivery.objects.filter(event=first, endpoint=item).count() == 1


@pytest.mark.django_db
@override_settings(PUSH_RECORD_EVENT_SOURCE="all")
def test_api_and_graphql_observations_publish_one_event(django_capture_on_commit_callbacks):
    endpoint()
    item = record_candidate()
    with django_capture_on_commit_callbacks(execute=True):
        persist_record_candidate(
            item,
            RecentRecordObservation.IngestionMethod.API_POLLING,
            {"source": "api"},
        )
        persist_record_candidate(
            item,
            RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION,
            {"source": "subscription"},
        )
    assert RecentRecordObservation.objects.count() == 2
    assert NotificationEvent.objects.count() == 1
    assert NotificationDelivery.objects.count() == 1


@pytest.mark.django_db
@override_settings(PUSH_RECORD_EVENT_SOURCE="api_polling")
def test_record_rollback_creates_no_event(django_capture_on_commit_callbacks):
    with (
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        persist_record_candidate(
            record_candidate(),
            RecentRecordObservation.IngestionMethod.API_POLLING,
            {"source": "api"},
        )
        raise RuntimeError("rollback")
    assert NotificationEvent.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "enabled", "disabled"),
    [
        ("WR", "record_wr", "record_cr"),
        ("CR", "record_cr", "record_nr"),
        ("NR", "record_nr", "record_wr"),
    ],
)
def test_recipient_selection_uses_level_preference(level, enabled, disabled):
    selected = endpoint(preferences={enabled: True, disabled: False})
    inactive = endpoint(preferences={enabled: True}, active=False)
    not_selected = endpoint(preferences={enabled: False})
    event, _ = publish_record_notification(record(level=level, canonical_key=f"preference-{level}"))
    assert set(event.deliveries.values_list("endpoint_id", flat=True)) == {selected.id}
    assert inactive.id not in eligible_endpoints(event.notification_type).values_list(
        "id", flat=True
    )
    assert not_selected.id not in event.deliveries.values_list("endpoint_id", flat=True)


@pytest.mark.django_db
def test_later_subscriber_gets_no_historical_delivery():
    event, _ = publish_record_notification(record())
    item = endpoint()
    publish_record_notification(record())
    assert create_deliveries(event) == 0
    assert not NotificationDelivery.objects.filter(event=event, endpoint=item).exists()


@pytest.mark.django_db
def test_reactivated_endpoint_gets_no_event_published_while_inactive():
    item = endpoint(active=False)
    event, _ = publish_record_notification(record())
    item.active = True
    item.activated_at = timezone.now()
    item.save(update_fields=["active", "activated_at", "updated_at"])
    assert create_deliveries(event) == 0
    assert not NotificationDelivery.objects.filter(event=event, endpoint=item).exists()


@pytest.mark.django_db
def test_post_send_correction_is_persisted_once_without_another_delivery():
    item = endpoint()
    event, _ = publish_record_notification(record())
    delivery = NotificationDelivery.objects.get(event=event, endpoint=item)
    delivery.status = NotificationDelivery.Status.SENT
    delivery.sent_at = timezone.now()
    delivery.save()
    corrected = record()
    corrected.formatted_result = "3.90"

    publish_record_notification(corrected)
    publish_record_notification(corrected)

    event.refresh_from_db()
    assert event.payload["formatted_result"] == "3.90"
    assert event.correction_count == 1
    assert event.deliveries.count() == 1


class FakeProvider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def send(self, **kwargs):
        self.calls += 1
        return self.result


@pytest.mark.django_db
def test_worker_claims_once_and_restart_does_not_repeat_sent_delivery():
    _event, delivery = event_and_delivery()
    first_claim = claim_due_deliveries(claimed_by="worker-one")
    second_claim = claim_due_deliveries(claimed_by="worker-two")
    assert first_claim == [delivery.pk]
    assert second_claim == []
    provider = FakeProvider(DeliveryResult(DeliveryOutcome.SUCCESS))
    process_claimed_delivery(delivery.pk, provider)
    assert provider.calls == 1
    assert claim_due_deliveries(claimed_by="restarted-worker") == []
    delivery.refresh_from_db()
    assert delivery.status == NotificationDelivery.Status.SENT


@pytest.mark.django_db
def test_worker_skips_future_retry_and_retries_when_due():
    _event, delivery = event_and_delivery()
    delivery.status = NotificationDelivery.Status.RETRY
    delivery.next_attempt_at = timezone.now() + timedelta(minutes=5)
    delivery.save()
    assert claim_due_deliveries() == []
    delivery.next_attempt_at = timezone.now() - timedelta(seconds=1)
    delivery.save(update_fields=["next_attempt_at", "updated_at"])
    assert claim_due_deliveries() == [delivery.pk]


@pytest.mark.django_db
def test_worker_stop_releases_unattempted_claims():
    endpoint()
    endpoint()
    publish_record_notification(record())
    provider = FakeProvider(DeliveryResult(DeliveryOutcome.SUCCESS))
    should_stop = Mock(side_effect=[False, True])

    processed = process_due_batch(
        provider,
        batch_size=2,
        claimed_by="stopping-worker",
        should_stop=should_stop,
    )

    assert processed == 1
    assert provider.calls == 1
    assert NotificationDelivery.objects.filter(status=NotificationDelivery.Status.SENT).count() == 1
    released = NotificationDelivery.objects.get(status=NotificationDelivery.Status.RETRY)
    assert released.attempt_count == 0
    assert released.claimed_by == ""


@pytest.mark.django_db
def test_permanent_endpoint_failure_deactivates_and_fails_outstanding_deliveries():
    item = endpoint()
    _event, delivery = event_and_delivery(item)
    other_event, _ = publish_record_notification(record(canonical_key="another-canonical-record"))
    other = NotificationDelivery.objects.get(event=other_event, endpoint=item)
    claim_due_deliveries(batch_size=1)
    provider = FakeProvider(
        DeliveryResult(
            DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE,
            "http_410",
            "Push endpoint is no longer valid",
        )
    )
    process_claimed_delivery(delivery.pk, provider)
    item.refresh_from_db()
    other.refresh_from_db()
    assert item.active is False
    assert other.status == NotificationDelivery.Status.PERMANENTLY_FAILED


@pytest.mark.django_db
@override_settings(PUSH_WORKER_MAX_ATTEMPTS=1)
def test_application_failure_reaches_max_attempts_without_deactivating_endpoint():
    item = endpoint()
    _event, delivery = event_and_delivery(item)
    claim_due_deliveries()
    provider = FakeProvider(
        DeliveryResult(
            DeliveryOutcome.APPLICATION_FAILURE,
            "webpush_configuration",
            "Web Push configuration is invalid",
        )
    )
    process_claimed_delivery(delivery.pk, provider)
    item.refresh_from_db()
    delivery.refresh_from_db()
    assert item.active is True
    assert delivery.status == NotificationDelivery.Status.PERMANENTLY_FAILED


def webpush_response(status_code, retry_after=None):
    response = Mock(status_code=status_code)
    response.headers = {"Retry-After": retry_after} if retry_after else {}
    response.text = "provider response without capabilities"
    return response


@pytest.mark.django_db
@override_settings(
    WEB_PUSH_VAPID_PRIVATE_KEY=TEST_VAPID_PRIVATE_KEY,
    WEB_PUSH_VAPID_SUBJECT="mailto:test@example.com",
)
@pytest.mark.parametrize(
    ("status_code", "outcome"),
    [
        (404, DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE),
        (410, DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE),
        (429, DeliveryOutcome.TRANSIENT_FAILURE),
        (503, DeliveryOutcome.TRANSIENT_FAILURE),
        (403, DeliveryOutcome.APPLICATION_FAILURE),
    ],
)
def test_webpush_provider_classifies_http_failures(status_code, outcome):
    item = endpoint()
    with patch(
        "apps.notifications.providers.webpush.webpush",
        side_effect=WebPushException(
            "secret endpoint omitted", webpush_response(status_code, "120")
        ),
    ):
        result = WebPushProvider().send(endpoint=item, payload={"title": "Safe"})
    assert result.outcome == outcome
    assert "push.example" not in result.error_message
    if status_code == 429:
        assert result.retry_at > timezone.now()


@pytest.mark.django_db
@override_settings(
    WEB_PUSH_VAPID_PRIVATE_KEY=TEST_VAPID_PRIVATE_KEY,
    WEB_PUSH_VAPID_SUBJECT="mailto:test@example.com",
)
def test_webpush_provider_success_and_timeout():
    item = endpoint()
    response = Mock(headers={"Location": "safe-message-id"})
    with patch("apps.notifications.providers.webpush.webpush", return_value=response):
        success = WebPushProvider().send(endpoint=item, payload={"title": "Safe"})
    assert success.outcome == DeliveryOutcome.SUCCESS
    assert success.provider_message_id == "safe-message-id"

    from requests.exceptions import Timeout

    with patch("apps.notifications.providers.webpush.webpush", side_effect=Timeout()):
        timeout = WebPushProvider().send(endpoint=item, payload={"title": "Safe"})
    assert timeout.outcome == DeliveryOutcome.TRANSIENT_FAILURE
