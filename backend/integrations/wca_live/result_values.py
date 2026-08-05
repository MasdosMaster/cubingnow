from dataclasses import dataclass

SKIPPED = 0
DNF = -1
DNS = -2


def is_complete(value: int | None) -> bool:
    return value is not None and value > 0


def comparison_key(event_id: str, value: int | None) -> tuple[int, int]:
    """Return a WCA-safe sortable key; incomplete results never beat valid ones.

    WCA Live deliberately encodes successful timed, FMC, and multi-blind values so
    lower positive integers rank better. Keeping the event argument explicit prevents
    callers from applying a raw numeric comparison to DNF/DNS/zero/null values.
    """

    if not is_complete(value):
        return (1, 0)
    if event_id == "333mbf":
        # The encoded value is specifically designed by WCA Live to be monotonic.
        return (0, int(value))
    if event_id == "333fm":
        return (0, int(value))
    return (0, int(value))


def is_better(event_id: str, candidate: int | None, incumbent: int | None) -> bool:
    if not is_complete(candidate):
        return False
    if not is_complete(incumbent):
        return True
    return comparison_key(event_id, candidate) < comparison_key(event_id, incumbent)


@dataclass(frozen=True)
class MultiBlindResult:
    solved: int
    attempted: int
    centiseconds: int | None


def decode_multi_blind(value: int) -> MultiBlindResult:
    if value <= 0:
        return MultiBlindResult(solved=0, attempted=0, centiseconds=None)
    missed = value % 100
    seconds = (value // 100) % 100_000
    points = 99 - ((value // 10_000_000) % 100)
    solved = points + missed
    attempted = solved + missed
    centiseconds = None if seconds == 99_999 else seconds * 100
    return MultiBlindResult(solved=solved, attempted=attempted, centiseconds=centiseconds)


def _clock(centiseconds: int | None) -> str:
    if centiseconds is None:
        return ""
    hours, remaining = divmod(centiseconds, 360_000)
    minutes, remaining = divmod(remaining, 6_000)
    seconds, centis = divmod(remaining, 100)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"
    if minutes:
        return f"{minutes}:{seconds:02d}.{centis:02d}"
    return f"{seconds}.{centis:02d}"


def format_result(event_id: str, kind: str, value: int | None) -> str:
    if value is None or value == SKIPPED:
        return ""
    if value == DNF:
        return "DNF"
    if value == DNS:
        return "DNS"
    if value < 0:
        return "Invalid"
    if event_id == "333mbf":
        decoded = decode_multi_blind(value)
        clock = _clock(decoded.centiseconds).removesuffix(".00")
        return f"{decoded.solved}/{decoded.attempted} {clock}".strip()
    if event_id == "333fm":
        if kind == "average":
            value_string = f"{value / 100:.2f}".rstrip("0")
            return value_string if not value_string.endswith(".") else value_string + "0"
        return str(value)
    return _clock(value)
