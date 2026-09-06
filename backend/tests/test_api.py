from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.records.classification import seed_live_records_from_baseline
from apps.records.classification_work import process_ready_work
from apps.records.models import (
    BaselineMetadata,
    BaselineRecordsSingle,
    CubingChinaCompetitionTarget,
    IngestionWorkerStatus,
    RecentRecordObservation,
)
from apps.records.serializers import RecordCompetitorSerializer
from integrations.wca_live.ingestion import persist_record_candidate
from integrations.wca_live.schemas import RecordCandidate


@pytest.mark.django_db
def test_records_endpoint_returns_normalized_record():
    now = timezone.now()
    BaselineMetadata.objects.create(
        export_generated_at=now,
        downloaded_at=now,
        source_filename="test-export.zip",
        source_version="test",
        rebuilt_at=now,
        is_active=True,
    )
    persist_record_candidate(
        replace(record_candidate(now), country_code="TW"),
        RecentRecordObservation.IngestionMethod.API_POLLING,
        {"api": True},
    )
    process_ready_work("api-test", limit=10)

    response = APIClient().get("/api/records/?level=WR")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"count", "next", "previous", "results"}
    result = payload["results"][0]
    assert set(result) == {
        "id",
        "canonical_result",
        "achievement",
        "competitor",
        "competition",
        "event",
        "round",
        "result",
        "timestamps",
        "validation",
        "sources",
        "notification",
    }
    assert result["canonical_result"]["revision"]["number"] == 1
    assert result["result"] == {
        "kind": "single",
        "raw": 326,
        "formatted": "3.26",
        "valid": True,
        "invalidity_reason": None,
    }
    assert result["achievement"]["level"] == "WR"
    assert set(result["achievement"]) == {
        "level",
        "scope",
        "outcome",
        "recognition",
        "incumbent_value",
        "holding",
    }
    assert result["competitor"] == {
        "name": "Test Cuber",
        "romanized_name": "Test Cuber",
        "native_name": None,
        "wca_id": "2026TEST01",
        "country_code": "TW",
        "country_display_name": "Taiwan",
        "country_wca_name": "Chinese Taipei",
        "continent": "Asia",
    }
    assert result["event"] == {"id": "333", "name": "3x3x3 Cube"}
    assert result["round"] == {"id": "live-round-1", "number": 2, "name": "Final"}
    assert result["competition"]["name"] == "Test Open 2026"
    assert result["competition"]["wca_id"] == "TestOpen2026"
    assert result["competition"]["timezone"] is None
    assert result["validation"] == {
        "status": "verified",
        "reason": "trusted_source_observation",
    }
    assert result["timestamps"]["classified_at"].endswith("Z")
    assert set(result["timestamps"]) == {
        "entered_at",
        "first_observed_at",
        "last_observed_at",
        "classified_at",
    }
    assert result["sources"]["pipelines"] == ["api_polling"]
    assert result["sources"]["url"].startswith("https://live.worldcubeassociation.org/")
    assert set(result["sources"]["claims"][0]) == {
        "pipeline",
        "record_tag",
        "claim_trusted",
        "result_evidence_trusted",
        "timestamps",
    }
    assert result["notification"] == {"eligible": True, "reason": "eligible"}
    assert "record_level" not in result
    # The public category feed keeps the pre-redesign highest-level display
    # policy, while callers can explicitly inspect the complete internal rows.
    assert APIClient().get("/api/records/?level=CR").json()["results"] == []
    history = APIClient().get(
        "/api/records/?level=CR&include_history=true"
    ).json()["results"]
    assert history[0]["achievement"]["outcome"] == "broken"


@pytest.mark.parametrize(
    ("name", "romanized_name", "native_name"),
    [
        ("Max Kwok U Sam (郭愉琛)", "Max Kwok U Sam", "郭愉琛"),
        ("Stanisław Snopczyk", "Stanisław Snopczyk", None),
    ],
)
def test_record_competitor_serializer_exposes_split_names(
    name, romanized_name, native_name
):
    competitor = SimpleNamespace(
        competitor_name=name,
        competitor_wca_id="2026TEST01",
        country_code="NL",
    )

    payload = RecordCompetitorSerializer(competitor).data

    assert payload["name"] == name
    assert payload["romanized_name"] == romanized_name
    assert payload["native_name"] == native_name


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "value"),
    [
        ("CR", 150),
        ("NR", 250),
        ("PR", 350),
    ],
)
def test_records_endpoint_shows_highest_qualifying_non_world_level(level, value):
    now = timezone.now()
    BaselineMetadata.objects.create(
        export_generated_at=now,
        downloaded_at=now,
        source_filename="test-export.zip",
        source_version="test",
        rebuilt_at=now,
        is_active=True,
    )
    BaselineRecordsSingle.objects.create(
        record_holder="World", record_type="WR", event_333=100
    )
    BaselineRecordsSingle.objects.create(
        record_holder="Europe", record_type="CR", event_333=200
    )
    BaselineRecordsSingle.objects.create(
        record_holder="Netherlands", record_type="NR", event_333=300
    )
    BaselineRecordsSingle.objects.create(
        record_holder="2026TEST01", record_type="PR", event_333=400
    )
    seed_live_records_from_baseline()
    candidate = replace(
        record_candidate(now),
        raw_result=value,
        final_best=value,
    )
    persist_record_candidate(
        candidate,
        RecentRecordObservation.IngestionMethod.API_POLLING,
        {"api": True},
    )
    process_ready_work("api-test", limit=10)

    response = APIClient().get(f"/api/records/?level={level}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["results"][0]["achievement"]["level"] == level
    assert payload["results"][0]["result"]["raw"] == value


def record_candidate(observed_at):
    return RecordCandidate(
        stable_result_identity="live-result-1",
        wca_live_record_id="recent-record-1",
        wca_live_result_id="live-result-1",
        wca_live_competition_id="live-competition-1",
        wca_competition_id="TestOpen2026",
        competition_name="Test Open 2026",
        competition_start_date=date(2026, 8, 6),
        competition_end_date=date(2026, 8, 7),
        round_id="live-round-1",
        round_name="Final",
        event_id="333",
        event_name="3x3x3 Cube",
        competitor_name="Test Cuber",
        competitor_wca_id="2026TEST01",
        competitor_wca_live_id="live-person-1",
        country_code="NL",
        kind="single",
        raw_result=326,
        record_level="WR",
        source_url="https://live.worldcubeassociation.org/competitions/live-competition-1/rounds/live-round-1",
        source_update_timestamp=observed_at,
        observed_at=observed_at,
        round_number=2,
        attempts=(326, 410, 430),
        final_best=326,
        final_average=410,
        expected_attempts=3,
    )


@pytest.mark.django_db
def test_recent_record_endpoints_return_independent_lists_and_match_metadata():
    observed_at = timezone.now()
    api_candidate = record_candidate(observed_at)
    subscription_candidate = replace(api_candidate, observed_at=observed_at + timedelta(seconds=12))
    persist_record_candidate(
        api_candidate, RecentRecordObservation.IngestionMethod.API_POLLING, {"api": True}
    )
    persist_record_candidate(
        subscription_candidate,
        RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION,
        {"subscription": True},
    )

    client = APIClient()
    api_response = client.get("/api/recent-records/?source=api_polling")
    subscription_response = client.get(
        "/api/recent-records/?source=graphql_subscription"
    )

    assert api_response.status_code == subscription_response.status_code == 200
    assert [row["ingestion_method"] for row in api_response.json()["results"]] == [
        "api_polling"
    ]
    subscription_row = subscription_response.json()["results"][0]
    assert subscription_row["ingestion_method"] == "graphql_subscription"
    assert subscription_row["matched_in_other_pipeline"] is True
    assert subscription_row["detection_time_difference_seconds"] == 12.0
    assert subscription_row["detected_at"].endswith("Z")

    comparison = client.get("/api/recent-records/comparison/")
    assert comparison.status_code == 200
    assert comparison.json()["results"][0]["matched"] is True


@pytest.mark.django_db
def test_recent_record_endpoint_hides_withdrawn_observations_by_default():
    observed_at = timezone.now()
    observation, _created = persist_record_candidate(
        record_candidate(observed_at),
        RecentRecordObservation.IngestionMethod.API_POLLING,
        {"api": True},
    )
    observation.status = RecentRecordObservation.Status.WITHDRAWN
    observation.withdrawn_at = observed_at + timedelta(seconds=5)
    observation.save(update_fields=["status", "withdrawn_at"])

    client = APIClient()
    active_response = client.get("/api/recent-records/?source=api_polling")
    withdrawn_response = client.get(
        "/api/recent-records/?source=api_polling&status=withdrawn"
    )

    assert active_response.status_code == withdrawn_response.status_code == 200
    assert active_response.json()["results"] == []
    assert [row["id"] for row in withdrawn_response.json()["results"]] == [
        observation.pk
    ]


@pytest.mark.django_db
def test_ingestion_status_is_available_before_workers_start():
    response = APIClient().get("/api/ingestion-status/")
    assert response.status_code == 200
    assert response.json()["api_polling"]["status"] == "unknown"
    assert response.json()["graphql_subscription"]["status"] == "unknown"
    assert response.json()["websocket_queues"]["wca_live"]["message_queue_size"] == 0
    assert response.json()["notifications"]["queued_count"] == 0
    assert response.json()["record_pipeline"]["canonical_result_count"] == 0


@pytest.mark.django_db
def test_ingestion_status_aggregates_live_websocket_queues():
    now = timezone.now()
    IngestionWorkerStatus.objects.create(
        ingestion_method=RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION,
        is_running=True,
        connected=True,
        heartbeat_at=now,
        metadata={
            "websocket": {
                "message_queue_size": 3,
                "peak_message_queue_size": 8,
                "queue_capacity": None,
                "captured_at": now.isoformat(),
                "counters": {"frames_received": 21},
            }
        },
    )
    IngestionWorkerStatus.objects.create(
        ingestion_method=RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET,
        is_running=True,
        connected=True,
        heartbeat_at=now,
    )
    for index, queue_size in enumerate((2, 5), start=1):
        CubingChinaCompetitionTarget.objects.create(
            slug=f"competition-{index}",
            cubingchina_id=index,
            wca_competition_id=f"TestOpen{index}",
            competition_name=f"Test Open {index}",
            competition_start_date=now.date(),
            competition_end_date=now.date(),
            status=CubingChinaCompetitionTarget.Status.ACTIVE,
            active=True,
            connected=True,
            websocket_diagnostics={
                "message_queue_size": queue_size,
                "peak_message_queue_size": queue_size + 2,
                "captured_at": now.isoformat(),
                "counters": {"frames_received": queue_size * 10},
            },
        )

    payload = APIClient().get("/api/ingestion-status/").json()

    assert payload["websocket_queues"]["wca_live"]["message_queue_size"] == 3
    assert payload["websocket_queues"]["cubingchina"]["message_queue_size"] == 7
    assert payload["websocket_queues"]["cubingchina"]["peak_message_queue_size"] == 7
    assert payload["websocket_queues"]["cubingchina"]["counters"]["frames_received"] == 70
    competition = payload["cubingchina_websocket"]["metadata"]["competitions"][0]
    assert competition["competition_start_date"] == now.date().isoformat()
    assert competition["competition_end_date"] == now.date().isoformat()
    assert competition["websocket"]
