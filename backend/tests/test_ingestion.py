import pytest

from apps.records.models import IngestionRun, RecentRecordObservation, SourceObservation
from integrations.wca_live.ingestion import ingest_api_record, ingest_record


@pytest.mark.django_db
def test_ingestion_is_idempotent():
    payload = {
        "id": "result-1-single",
        "type": "single",
        "tag": "WR",
        "attemptResult": 326,
        "result": {"id": "result-1", "best": 326, "average": 450, "enteredAt": "2026-08-04T12:00:00Z",
            "person": {"wcaId": "2026TEST01", "name": "Test Cuber", "country": {"iso2": "NL"}},
            "round": {"competitionEvent": {
                "event": {"id": "333", "name": "3x3x3 Cube"},
                "competition": {"wcaId": "TestOpen2026", "name": "Test Open 2026",
                    "startDate": "2026-08-01", "endDate": "2026-08-02",
                    "venues": [{"name": "Test Venue", "timezone": "Europe/Amsterdam", "country": {"iso2": "NL"}}]}
            }}}
    }
    run = IngestionRun.objects.create(mode=IngestionRun.Mode.RECONCILIATION)

    first = ingest_record(payload, run)
    second = ingest_record(payload, run)

    assert first.pk == second.pk
    assert SourceObservation.objects.count() == 1


@pytest.mark.django_db
def test_api_ingestion_never_populates_subscription_collection():
    payload = {
        "id": "result-api-single",
        "type": "single",
        "tag": "NR",
        "attemptResult": 450,
        "result": {
            "id": "result-api",
            "enteredAt": "2026-08-05T12:00:00Z",
            "person": {
                "id": "person-api",
                "wcaId": "2026TEST02",
                "name": "API Cuber",
                "country": {"iso2": "NL"},
            },
            "round": {
                "id": "round-api",
                "name": "Final",
                "competitionEvent": {
                    "event": {"id": "333", "name": "3x3x3 Cube"},
                    "competition": {
                        "id": "competition-api",
                        "wcaId": "APIOpen2026",
                        "name": "API Open 2026",
                        "startDate": "2026-08-06",
                        "endDate": "2026-08-07",
                    },
                },
            },
        },
    }
    record, created = ingest_api_record(payload)
    assert created
    assert record.ingestion_method == "api_polling"
    assert RecentRecordObservation.objects.filter(ingestion_method="api_polling").count() == 1
    assert RecentRecordObservation.objects.filter(
        ingestion_method="graphql_subscription"
    ).count() == 0
