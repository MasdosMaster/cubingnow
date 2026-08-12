from datetime import UTC, date, datetime

import pytest

from integrations.weekend_window import resolve_weekend_window, rolling_weekend_window


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (date(2026, 8, 12), (date(2026, 8, 12), date(2026, 8, 18))),
        (date(2026, 8, 15), (date(2026, 8, 12), date(2026, 8, 18))),
        (date(2026, 8, 18), (date(2026, 8, 12), date(2026, 8, 18))),
        (date(2026, 8, 19), (date(2026, 8, 19), date(2026, 8, 25))),
    ],
)
def test_rolling_weekend_window(as_of, expected):
    assert rolling_weekend_window(as_of) == expected


def test_rolling_weekend_window_uses_configured_timezone():
    instant = datetime(2026, 8, 11, 22, 30, tzinfo=UTC)

    assert rolling_weekend_window(instant, "Europe/Amsterdam") == (
        date(2026, 8, 12),
        date(2026, 8, 18),
    )


def test_explicit_weekend_override_is_preserved():
    assert resolve_weekend_window("2026-08-06", "2026-08-10") == (
        date(2026, 8, 6),
        date(2026, 8, 10),
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-08-06", ""),
        ("", "2026-08-10"),
        ("not-a-date", "2026-08-10"),
        ("2026-08-11", "2026-08-10"),
    ],
)
def test_invalid_explicit_weekend_override_is_rejected(start, end):
    with pytest.raises(ValueError):
        resolve_weekend_window(start, end)
