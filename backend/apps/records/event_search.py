EVENT_OTHER_SEARCH_TERMS = {
    "222": ("small cubes", "nxnxn"),
    "333": ("small cubes", "nxnxn"),
    "444": ("big cubes", "nxnxn"),
    "555": ("big cubes", "nxnxn"),
    "666": ("big cubes", "nxnxn"),
    "777": ("big cubes", "nxnxn"),
    "333bf": ("blindfolded", "bld", "3x3 variants"),
    "444bf": ("blindfolded", "bld", "big blindfolded", "big bld"),
    "555bf": ("blindfolded", "bld", "big blindfolded", "big bld"),
    "333fm": ("3x3 variants",),
    "333oh": ("3x3 variants",),
    "333mbf": ("blindfolded", "bld", "3x3 variants"),
    "clock": ("side events",),
    "minx": ("side events",),
    "pyram": ("side events",),
    "skewb": ("side events",),
    "sq1": ("side events",),
    "333ft": ("dead events", "removed", "retired", "discontinued", "former"),
    "magic": ("dead events", "removed", "retired", "discontinued", "former"),
    "mmagic": ("dead events", "removed", "retired", "discontinued", "former"),
    "333mbo": (
        "blindfolded",
        "bld",
        "dead events",
        "removed",
        "retired",
        "discontinued",
        "former",
    ),
}


def event_other_search_terms(event_id: str | None) -> tuple[str, ...]:
    return EVENT_OTHER_SEARCH_TERMS.get(event_id or "", ())


def event_ids_matching_other_search_terms(term: str) -> tuple[str, ...]:
    candidate = term.strip().casefold()
    if not candidate:
        return ()
    return tuple(
        event_id
        for event_id, search_terms in EVENT_OTHER_SEARCH_TERMS.items()
        if any(candidate in search_term.casefold() for search_term in search_terms)
    )
