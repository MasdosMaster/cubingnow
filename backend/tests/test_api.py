from dataclasses import replace
from datetime import date, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.records.models import RecentRecordObservation
from integrations.wca_live.ingestion import persist_record_candidate
from integrations.wca_live.schemas import RecordCandidate


@pytest.mark.django_db
def test_records_endpoint_returns_normalized_record():
    persist_record_candidate(
        record_candidate(timezone.now()),
        RecentRecordObservation.IngestionMethod.API_POLLING,
        {"api": True},
    )

    response = APIClient().get("/api/records/")

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["formatted_result"] == "3.26"
    assert result["record_level"] == "WR"
    assert result["validation_status"] == "verified"


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
