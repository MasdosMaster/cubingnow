import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.competitions.models import Competition
from apps.competitors.geography import continent_for_country_code, country_code_from_name
from apps.competitors.models import Attendance, AttendanceSyncRun, Competitor
from apps.competitors.weekend import attendance_window, sync_weekend_attendance
from integrations.attendance_types import SourceCompetition
from integrations.cubingchina.attendance_parser import (
    parse_competition_detail as parse_china_detail,
)
from integrations.cubingchina.attendance_parser import (
    parse_competition_index as parse_china_competitions,
)
from integrations.cubingchina.attendance_parser import (
    parse_registrations as parse_china_registrations,
)
from integrations.wca.attendance_parser import parse_competitions as parse_wca_competitions
from integrations.wca.attendance_parser import parse_registrations as parse_wca_registrations

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name):
    return (FIXTURES / name).read_text()


def fixture_json(name):
    return json.loads(fixture_text(name))


class FakeWCAClient:
    def __init__(self, registrations=None):
        self.calls = []
        self.registrations = (
            fixture_json("wca_weekend_registrations.json")
            if registrations is None
            else registrations
        )

    def get_json(self, path, **params):
        self.calls.append((path, params))
        if path == "/api/v0/competitions":
            return fixture_json("wca_weekend_competitions.json")
        if path.endswith("/TestOpen2026/registrations"):
            return self.registrations
        if path.endswith("/ChinaOpen2026/registrations"):
            return []
        raise AssertionError(f"An out-of-window registration page was requested: {path}")


class FakeCubingChinaClient:
    def __init__(self, fail_on_registration=False):
        self.calls = []
        self.fail_on_registration = fail_on_registration

    def get_page(self, path, **params):
        self.calls.append((path, params))
        if path == "/competition":
            return fixture_text("cubingchina_competitions.html")
        if path == "/competition/China-Open-2026":
            return fixture_text("cubingchina_competition_detail.html")
        if path == "/competition/Local-Cube-Festival-2026":
            return fixture_text("cubingchina_non_wca_detail.html")
        if self.fail_on_registration and path.endswith("/competitors"):
            raise RuntimeError("temporary source failure")
        if path == "/competition/China-Open-2026/competitors":
            return fixture_text("cubingchina_competitors.html")
        if path == "/competition/Local-Cube-Festival-2026/competitors":
            return fixture_text("cubingchina_local_competitors.html")
        raise AssertionError(f"An out-of-window page was requested: {path}")


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (date(2026, 8, 5), (date(2026, 8, 5), date(2026, 8, 11))),
        (date(2026, 8, 11), (date(2026, 8, 5), date(2026, 8, 11))),
        (date(2026, 8, 12), (date(2026, 8, 12), date(2026, 8, 18))),
    ],
)
def test_attendance_window_is_wednesday_through_tuesday(as_of, expected):
    assert attendance_window(as_of) == expected


def test_attendance_window_uses_the_configured_timezone_for_datetimes():
    instant = datetime(2026, 8, 4, 22, 30, tzinfo=UTC)
    assert attendance_window(instant, "Europe/Amsterdam") == (
        date(2026, 8, 5),
        date(2026, 8, 11),
    )


def test_competition_overlap_is_inclusive_at_both_edges():
    previous = SourceCompetition(
        source="wca",
        source_id="Previous2026",
        wca_id="Previous2026",
        name="Previous",
        country_code="NL",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 5),
    )
    following = SourceCompetition(
        source="wca",
        source_id="Following2026",
        wca_id="Following2026",
        name="Following",
        country_code="NL",
        start_date=date(2026, 8, 11),
        end_date=date(2026, 8, 12),
    )
    assert previous.overlaps(date(2026, 8, 5), date(2026, 8, 11))
    assert following.overlaps(date(2026, 8, 5), date(2026, 8, 11))


@pytest.mark.parametrize(
    ("country_code", "continent"),
    [
        ("ZA", "Africa"),
        ("CN", "Asia"),
        ("XK", "Europe"),
        ("US", "North America"),
        ("BR", "South America"),
        ("AU", "Oceania"),
    ],
)
def test_country_codes_map_to_supported_continents(country_code, continent):
    assert continent_for_country_code(country_code) == continent


def test_wca_specific_country_names_map_to_iso_codes():
    assert country_code_from_name("Hong Kong, China") == "HK"
    assert country_code_from_name("Chinese Taipei") == "TW"
    assert country_code_from_name("Kosovo") == "XK"


def test_current_source_contract_fixtures_are_parsed():
    wca_competitions = parse_wca_competitions(fixture_json("wca_weekend_competitions.json"))
    wca_registrations = parse_wca_registrations(fixture_json("wca_weekend_registrations.json"))
    china_competitions = parse_china_competitions(fixture_text("cubingchina_competitions.html"))
    china_competition = parse_china_detail(
        fixture_text("cubingchina_competition_detail.html"), china_competitions[1]
    )
    china_registrations = parse_china_registrations(fixture_text("cubingchina_competitors.html"))

    assert wca_competitions[0].wca_id == "TestOpen2026"
    assert [row.wca_id for row in wca_registrations] == ["2020ALPH01", "2019ZETA01"]
    assert china_competition.wca_id == "ChinaOpen2026"
    assert {row.wca_id for row in china_registrations} == {"2016KOLA02", "2020ALPH01"}
    assert {row.continent for row in china_registrations} == {"Europe"}


@pytest.mark.django_db
def test_sync_deduplicates_sources_excludes_first_timers_and_is_idempotent(monkeypatch):
    wca = FakeWCAClient()
    china = FakeCubingChinaClient()

    first = sync_weekend_attendance(
        as_of=date(2026, 8, 5), wca_client=wca, cubingchina_client=china
    )
    second = sync_weekend_attendance(
        as_of=date(2026, 8, 5),
        wca_client=FakeWCAClient(),
        cubingchina_client=FakeCubingChinaClient(),
    )

    assert first == second
    assert first["competitions"] == 3
    assert first["competitors"] == 4
    assert Competitor.objects.count() == 4
    assert not Competitor.objects.filter(name="Brand New Cuber").exists()
    assert Competition.objects.count() == 3
    assert Attendance.objects.count() == 6
    assert AttendanceSyncRun.objects.filter(status="succeeded").count() == 2
    assert not any("OutsideOpen2026/registrations" in call[0] for call in wca.calls)
    assert not any("Outside-Open-2026" in call[0] for call in china.calls)

    alice_at_china = Attendance.objects.get(
        competitor__wca_id="2020ALPH01", competition__wca_id="ChinaOpen2026"
    )
    assert alice_at_china.sources == ["cubingchina"]
    assert alice_at_china.competitor.name == "Álice Alpha"
    assert Attendance.objects.filter(competitor__wca_id="2016KOLA02").count() == 2

    china_only_profile = Competitor.objects.get(wca_id="2016KOLA02")
    china_only_profile.name = "Outdated name"
    china_only_profile.country_code = "NL"
    china_only_profile.save(update_fields=["name", "country_code"])
    sync_weekend_attendance(
        as_of=date(2026, 8, 5),
        wca_client=FakeWCAClient(),
        cubingchina_client=FakeCubingChinaClient(),
    )
    china_only_profile.refresh_from_db()
    assert china_only_profile.name == "Tymon Kolasiński"
    assert china_only_profile.country_code == "PL"

    monkeypatch.setattr(
        "apps.competitors.views.attendance_window",
        lambda: (date(2026, 8, 5), date(2026, 8, 11)),
    )
    response = APIClient().get("/api/competing-this-weekend/")
    tymon = next(row for row in response.json()["results"] if row["wca_id"] == "2016KOLA02")
    assert len(tymon["competitions"]) == 2


@pytest.mark.django_db
def test_successful_refresh_marks_removed_registration_unaccepted():
    sync_weekend_attendance(
        as_of=date(2026, 8, 5),
        wca_client=FakeWCAClient(),
        cubingchina_client=FakeCubingChinaClient(),
    )
    sync_weekend_attendance(
        as_of=date(2026, 8, 5),
        wca_client=FakeWCAClient(registrations=[]),
        cubingchina_client=FakeCubingChinaClient(),
    )
    assert not Attendance.objects.get(
        competitor__wca_id="2019ZETA01", competition__wca_id="TestOpen2026"
    ).is_accepted


@pytest.mark.django_db
def test_failed_scrape_does_not_clear_previous_attendance():
    sync_weekend_attendance(
        as_of=date(2026, 8, 5),
        wca_client=FakeWCAClient(),
        cubingchina_client=FakeCubingChinaClient(),
    )
    accepted_before = set(Attendance.objects.filter(is_accepted=True).values_list("pk", flat=True))

    with pytest.raises(RuntimeError, match="temporary source failure"):
        sync_weekend_attendance(
            as_of=date(2026, 8, 5),
            wca_client=FakeWCAClient(registrations=[]),
            cubingchina_client=FakeCubingChinaClient(fail_on_registration=True),
        )

    assert (
        set(Attendance.objects.filter(is_accepted=True).values_list("pk", flat=True))
        == accepted_before
    )
    assert AttendanceSyncRun.objects.filter(status="failed").count() == 1


@pytest.mark.django_db
def test_endpoint_is_unpaginated_ranked_and_filters_continents(monkeypatch):
    monkeypatch.setattr(
        "apps.competitors.views.attendance_window",
        lambda: (date(2026, 8, 5), date(2026, 8, 11)),
    )
    competition = Competition.objects.create(
        wca_id="BigOpen2026",
        name="Big Open 2026",
        country_code="NL",
        city="Utrecht",
        start_date=date(2026, 8, 8),
        end_date=date(2026, 8, 9),
    )
    people = [
        Competitor(
            wca_id=f"2026TEST{i:02d}",
            name=f"Cuber {59 - i:02d}",
            country_code="NL" if i < 55 else "US",
            continent="Europe" if i < 55 else "North America",
        )
        for i in range(60)
    ]
    Competitor.objects.bulk_create(people)
    now = timezone.now()
    Attendance.objects.bulk_create(
        [
            Attendance(
                competitor=person,
                competition=competition,
                observed_at=now,
                sources=["wca"],
            )
            for person in Competitor.objects.all()
        ]
    )
    AttendanceSyncRun.objects.create(
        window_start=date(2026, 8, 5),
        window_end=date(2026, 8, 11),
        status="succeeded",
        finished_at=now,
    )

    response = APIClient().get("/api/competing-this-weekend/")
    assert response.status_code == 200
    assert response.json()["count"] == 60
    assert len(response.json()["results"]) == 60
    assert [row["rank"] for row in response.json()["results"]] == list(range(1, 61))
    assert response.json()["results"][0]["name"] == "Cuber 00"

    europe = APIClient().get("/api/competing-this-weekend/?continent=Europe")
    assert europe.json()["count"] == 55
    assert europe.json()["results"][-1]["rank"] == 55
    assert {row["continent"] for row in europe.json()["results"]} == {"Europe"}

    invalid = APIClient().get("/api/competing-this-weekend/?continent=Atlantis")
    assert invalid.status_code == 400
