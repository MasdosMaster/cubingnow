import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.competitions.models import Competition
from apps.competitors.models import Competitor
from apps.records.models import Record, Result


@pytest.mark.django_db
def test_records_endpoint_returns_normalized_record():
    competition = Competition.objects.create(
        wca_id="TestOpen2026",
        name="Test Open 2026",
        country_code="NL",
        city="Utrecht",
        start_date="2026-08-01",
        end_date="2026-08-02",
    )
    competitor = Competitor.objects.create(
        wca_id="2026TEST01", name="Test Cuber", country_code="NL"
    )
    result = Result.objects.create(
        source_id="result-1",
        competition=competition,
        competitor=competitor,
        event_id="333",
        event_name="3x3x3 Cube",
        kind=Result.Kind.SINGLE,
        value=326,
    )
    Record.objects.create(result=result, level=Record.Level.WORLD, detected_at=timezone.now())

    response = APIClient().get("/api/records/")

    assert response.status_code == 200
    assert response.json()["results"][0]["display_value"] == "326"
