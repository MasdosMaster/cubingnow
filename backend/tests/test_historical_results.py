from datetime import date

import pytest
from django.db import connection

from apps.records import baseline_export
from apps.records.baseline_export import historical_results_insert_sql
from apps.records.models import HistoricalResult


def test_projection_sql_uses_v2_attempts_and_not_results_best():
    singles, averages = historical_results_insert_sql(has_round_id=True)

    assert '"wca_export"."result_attempts"' in singles
    assert 'JOIN "wca_export"."results" r ON r.id = ra.result_id' in singles
    assert "ra.value::bigint BETWEEN 1 AND 2147483647" in singles
    assert "ra.attempt_number ~ '^[1-5]$'" in singles
    assert "r.average::bigint BETWEEN 1 AND 2147483647" in averages
    assert "r.person_country_id" in singles
    assert "r.person_country_id" in averages
    assert "r.round_id" in singles
    assert "make_date(c.end_year::integer, c.end_month::integer, c.end_day::integer)" in singles
    assert "r.best" not in singles
    assert "r.best" not in averages


def test_projection_keeps_nullable_round_provenance_when_v2_omits_round_id():
    singles, averages = historical_results_insert_sql(has_round_id=False)

    assert "NULL::varchar(64)" in singles
    assert "NULL::varchar(64)" in averages
    assert "r.round_type_id" in singles
    assert "r.round_type_id" in averages


def test_historical_result_schema_has_query_indexes_and_null_safe_uniqueness():
    constraints = {constraint.name: constraint for constraint in HistoricalResult._meta.constraints}
    indexes = {index.name: index for index in HistoricalResult._meta.indexes}

    assert constraints["hist_unique_single_attempt"].fields == (
        "result_id",
        "attempt_number",
    )
    assert constraints["hist_unique_average"].fields == ("result_id",)
    assert indexes["hist_person_event_date_idx"].fields == [
        "person_id",
        "event_id",
        "kind",
        "achieved_date",
    ]
    assert indexes["hist_event_kind_date_value_idx"].fields == [
        "event_id",
        "kind",
        "achieved_date",
        "value",
    ]


@pytest.mark.django_db(transaction=True)
def test_postgresql_rebuild_generates_only_valid_historical_rows():
    if connection.vendor != "postgresql":
        pytest.skip("Historical rebuild uses PostgreSQL bulk SQL")

    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS wca_export CASCADE")
        cursor.execute("CREATE SCHEMA wca_export")
        cursor.execute(
            """
            CREATE TABLE wca_export.competitions (
                id text, end_year text, end_month text, end_day text
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE wca_export.results (
                id text, best text, average text, competition_id text,
                person_country_id text, person_id text, event_id text,
                round_id text, round_type_id text
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE wca_export.result_attempts (
                value text, attempt_number text, result_id text
            )
            """
        )
        cursor.execute(
            "INSERT INTO wca_export.competitions VALUES ('IncludedOpen2026', '2026', '8', '30')"
        )
        cursor.execute(
            """
            INSERT INTO wca_export.results VALUES
                ('1', '900', '1100', 'IncludedOpen2026', 'Argentina',
                 '2020TEST01', '333', '987', 'f'),
                ('2', '-1', '0', 'IncludedOpen2026', 'Netherlands',
                 '2020TIEE01', '333', '988', '1')
            """
        )
        cursor.execute(
            """
            INSERT INTO wca_export.result_attempts VALUES
                ('900', '1', '1'), ('1000', '2', '1'), ('1100', '3', '1'),
                ('1200', '4', '1'), ('1300', '5', '1'),
                ('-1', '1', '2'), ('-2', '2', '2'), ('0', '3', '2'),
                (NULL, '4', '2')
            """
        )

    assert baseline_export.rebuild_historical_results() == (5, 1, 6)

    rows = list(HistoricalResult.objects.filter(result_id=1).order_by("kind", "attempt_number"))
    assert len(rows) == 6  # results.best did not create a seventh row.
    singles = [row for row in rows if row.kind == HistoricalResult.Kind.SINGLE]
    average = next(row for row in rows if row.kind == HistoricalResult.Kind.AVERAGE)
    assert [row.attempt_number for row in singles] == [1, 2, 3, 4, 5]
    assert average.attempt_number is None
    assert average.value == 1100
    assert average.person_id == "2020TEST01"
    assert average.event_id == "333"
    assert average.country_id == "Argentina"
    assert average.competition_id == "IncludedOpen2026"
    assert average.round_id == "987"
    assert average.round_type_id == "f"
    assert average.achieved_date == date(2026, 8, 30)
    assert not HistoricalResult.objects.filter(result_id=2).exists()


@pytest.mark.django_db(transaction=True)
def test_postgresql_failed_build_preserves_previous_live_table(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Historical rebuild uses PostgreSQL table swapping")

    previous = HistoricalResult.objects.create(
        result_id=99,
        kind=HistoricalResult.Kind.AVERAGE,
        attempt_number=None,
        value=1234,
        person_id="2020TEST01",
        event_id="333",
        country_id="Netherlands",
        competition_id="PreviousOpen2026",
        round_type_id="f",
        achieved_date=date(2026, 8, 1),
    )

    def fail_population(cursor, *, has_round_id):
        raise RuntimeError("simulated replacement failure")

    monkeypatch.setattr(baseline_export, "_populate_historical_results", fail_population)

    with pytest.raises(RuntimeError, match="simulated replacement failure"):
        baseline_export.rebuild_historical_results()

    assert HistoricalResult.objects.filter(pk=previous.pk, result_id=99).exists()
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.historical_results_next')")
        assert cursor.fetchone()[0] is None
