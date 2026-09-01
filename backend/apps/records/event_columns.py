"""Fixed WCA event-to-column mapping for the wide record tables.

The database column names match WCA event IDs.  Callers must always go through
this mapping instead of constructing field or SQL identifiers from provider data.
"""

SINGLE_EVENT_IDS = (
    "222",
    "333",
    "444",
    "555",
    "666",
    "777",
    "333bf",
    "333fm",
    "333oh",
    "clock",
    "minx",
    "pyram",
    "skewb",
    "sq1",
    "444bf",
    "555bf",
    "333mbf",
    "333mbo",
    "333ft",
    "magic",
    "mmagic",
)

# Multi-blind has no official average ranking.  ``333mbo`` is the export ID for
# the historical event called ``mbo`` in older WCA data/documentation.
AVERAGE_EVENT_IDS = tuple(
    event_id for event_id in SINGLE_EVENT_IDS if event_id not in {"333mbf", "333mbo"}
)

EVENT_FIELD_BY_ID = {event_id: f"event_{event_id}" for event_id in SINGLE_EVENT_IDS}


def event_field(event_id: str, kind: str) -> str:
    """Return a trusted Django field name for a supported event/kind pair."""

    allowed = SINGLE_EVENT_IDS if kind == "single" else AVERAGE_EVENT_IDS
    if event_id not in allowed:
        raise ValueError(f"Unsupported WCA event {event_id!r} for {kind!r} records")
    return EVENT_FIELD_BY_ID[event_id]
