from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def rolling_weekend_window(
    as_of: date | datetime | None = None,
    timezone_name: str = "Europe/Amsterdam",
) -> tuple[date, date]:
    """Return the current Wednesday-through-Tuesday competition window."""
    zone = ZoneInfo(timezone_name)
    if as_of is None:
        local_date = datetime.now(zone).date()
    elif isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=zone)
        local_date = as_of.astimezone(zone).date()
    else:
        local_date = as_of
    start = local_date - timedelta(days=(local_date.weekday() - 2) % 7)
    return start, start + timedelta(days=6)


def resolve_weekend_window(
    start_value: str = "",
    end_value: str = "",
    *,
    as_of: date | datetime | None = None,
    timezone_name: str = "Europe/Amsterdam",
) -> tuple[date, date]:
    """Resolve an explicit override pair or calculate the rolling window."""
    if bool(start_value) != bool(end_value):
        raise ValueError("weekend start and end must both be provided")
    if not start_value:
        return rolling_weekend_window(as_of, timezone_name)

    start = date.fromisoformat(start_value)
    end = date.fromisoformat(end_value)
    if end < start:
        raise ValueError("weekend end must be on or after start")
    return start, end
