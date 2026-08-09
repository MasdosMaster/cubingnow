from datetime import date

import pytest

from apps.records.models import (
    IngestionWorkerStatus,
    RecentRecordObservation,
    SubscriptionRound,
)
from integrations.wca_live.schemas import RoundTarget
from integrations.wca_live.subscription_supervisor import SubscriptionSupervisor

METHOD = RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION


def supervisor():
    return SubscriptionSupervisor(
        weekend_start=date(2026, 8, 6),
        weekend_end=date(2026, 8, 10),
        api_endpoint="https://example.test/api",
        websocket_endpoint="wss://example.test/socket",
    )


@pytest.mark.django_db
def test_heartbeat_persists_websocket_diagnostics_in_worker_metadata():
    status = IngestionWorkerStatus.objects.create(
        ingestion_method=METHOD,
        metadata={"rounds_discovered": 363},
    )
    diagnostics = {
        "counters": {"frames_received": 365, "heartbeat_replies": 1},
        "last_frame": {"topic": "phoenix", "event": "phx_reply"},
        "last_unexpected_frame": None,
    }

    SubscriptionSupervisor._heartbeat(diagnostics)

    status.refresh_from_db()
    assert status.metadata["rounds_discovered"] == 363
    assert status.metadata["websocket"] == diagnostics
    assert status.heartbeat_at is not None


@pytest.mark.django_db
def test_discovery_refresh_preserves_websocket_diagnostics():
    diagnostics = {"counters": {"frames_received": 365}}
    status = IngestionWorkerStatus.objects.create(
        ingestion_method=METHOD,
        metadata={"websocket": diagnostics},
    )

    supervisor()._persist_targets([], {"rounds_discovered": 0})

    status.refresh_from_db()
    assert status.metadata["rounds_discovered"] == 0
    assert status.metadata["websocket"] == diagnostics


@pytest.mark.django_db
def test_message_and_successful_snapshot_timestamps_are_recorded_separately():
    status = IngestionWorkerStatus.objects.create(ingestion_method=METHOD)

    SubscriptionSupervisor._record_message_received("round-not-persisted")

    status.refresh_from_db()
    assert status.last_message_at is not None
    assert status.last_successful_snapshot_at is None

    SubscriptionSupervisor._record_snapshot_processed()

    status.refresh_from_db()
    assert status.last_successful_snapshot_at is not None
    assert status.last_successful_snapshot_at >= status.last_message_at


@pytest.mark.django_db
def test_discovery_targets_are_bulk_upserted_and_retired_rows_are_reactivated(
    django_assert_max_num_queries,
):
    targets = [
        RoundTarget(
            round_id=f"round-{index}",
            wca_live_competition_id="competition-1",
            wca_competition_id="TestOpen2026",
            competition_name="Test Open",
            competition_start_date=date(2026, 8, 7),
            competition_end_date=date(2026, 8, 8),
            event_id="333",
            event_name="3x3x3 Cube",
            round_number=index,
        )
        for index in range(1, 51)
    ]

    with django_assert_max_num_queries(8):
        persisted = supervisor()._persist_targets(targets, {"rounds_discovered": 50})

    assert len(persisted) == SubscriptionRound.objects.count() == 50
    retired = SubscriptionRound.objects.get(round_id="round-1")
    retired.active = False
    retired.subscription_status = SubscriptionRound.Status.RETIRED
    retired.save(update_fields=["active", "subscription_status", "updated_at"])

    supervisor()._persist_targets(targets, {"rounds_discovered": 50})
    retired.refresh_from_db()
    assert retired.active is True
    assert retired.subscription_status == SubscriptionRound.Status.DISCOVERED
