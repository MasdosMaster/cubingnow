import pytest

from apps.records.models import IngestionRun, SourceObservation
from integrations.wca.ingestion import ingest_record


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
