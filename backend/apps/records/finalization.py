from dataclasses import dataclass

from integrations.wca_live.result_values import is_better


@dataclass(frozen=True)
class RoundFinalizationRule:
    """Provider-neutral structure needed to decide whether a result row is final."""

    expected_attempts: int
    cutoff_attempts: int | None = None
    cutoff_value: int | None = None


def attempt_is_entered(value: int | None) -> bool:
    """DNF/DNS are entered attempts; zero and missing positions are not."""

    return value is not None and value != 0


def all_expected_attempts_are_entered(
    attempts: tuple[int, ...] | list[int],
    expected_attempts: int,
) -> bool:
    if expected_attempts <= 0:
        return False
    values = tuple(attempts[:expected_attempts])
    return len(values) == expected_attempts and all(attempt_is_entered(value) for value in values)


def round_result_is_finalized(
    attempts: tuple[int, ...] | list[int],
    rule: RoundFinalizationRule,
    *,
    event_id: str,
) -> bool:
    """Return whether no more attempts can legitimately be entered for this result."""

    if rule.expected_attempts <= 0:
        return False
    values = tuple(attempts[: rule.expected_attempts])
    values += (0,) * (rule.expected_attempts - len(values))
    if all_expected_attempts_are_entered(values, rule.expected_attempts):
        return True

    cutoff_attempts = rule.cutoff_attempts
    cutoff_value = rule.cutoff_value
    has_cutoff = (
        cutoff_attempts is not None
        and cutoff_value is not None
        and 0 < cutoff_attempts < rule.expected_attempts
        and cutoff_value > 0
    )
    if not has_cutoff:
        return False

    cutoff_values = values[:cutoff_attempts]
    if not all(attempt_is_entered(value) for value in cutoff_values):
        return False
    passed_cutoff = any(is_better(event_id, value, cutoff_value) for value in cutoff_values)
    if passed_cutoff:
        return False

    # A failed cutoff is final only while all later positions remain unentered.
    return not any(attempt_is_entered(value) for value in values[cutoff_attempts:])


def cubingchina_expected_attempts(round_format: str) -> int:
    normalized = (round_format or "a").strip().lower()
    if normalized == "a":
        return 5
    if normalized == "m":
        return 3
    try:
        return int(normalized)
    except ValueError:
        return 0


def cubingchina_finalization_rule(target) -> RoundFinalizationRule:
    expected = cubingchina_expected_attempts(target.format)
    cutoff_value = int(target.cutoff or 0)
    if cutoff_value > 0 and target.event_id != "333fm":
        # CubingChina publishes timed cutoffs in seconds and attempts in centiseconds.
        cutoff_value *= 100
    cutoff_attempts = 2 if (target.format or "a").lower() == "a" else 1
    return RoundFinalizationRule(
        expected_attempts=expected,
        cutoff_attempts=cutoff_attempts if cutoff_value > 0 else None,
        cutoff_value=cutoff_value if cutoff_value > 0 else None,
    )
