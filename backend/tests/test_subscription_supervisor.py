from datetime import date

import pytest

from apps.records.models import IngestionWorkerStatus, RecentRecordObservation
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
