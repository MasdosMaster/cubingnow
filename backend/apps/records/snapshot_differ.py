from collections.abc import Sequence

from integrations.wca_live.result_values import is_complete

from .domain import ResultChange


def diff_result_values(
    previous_attempts: Sequence[int],
    current_attempts: Sequence[int],
    *,
    previous_average: int | None = None,
    current_average: int | None = None,
) -> tuple[ResultChange, ...]:
    """Describe meaningful changes inside one full so-far round result.

    Attempt position, rather than value, is the identity. This keeps identical solve
    values distinct and lets a later source correction update the existing solve.
    """

    changes: list[ResultChange] = []
    shared = min(len(previous_attempts), len(current_attempts))
    for index in range(shared):
        old_value = previous_attempts[index]
        new_value = current_attempts[index]
        if old_value != new_value:
            changes.append(
                ResultChange(
                    change_type="corrected",
                    kind="single",
                    attempt_number=index + 1,
                    value=new_value,
                    previous_value=old_value,
                )
            )
    for index in range(shared, len(current_attempts)):
        changes.append(
            ResultChange(
                change_type="added",
                kind="single",
                attempt_number=index + 1,
                value=current_attempts[index],
            )
        )
    for index in range(shared, len(previous_attempts)):
        changes.append(
            ResultChange(
                change_type="retracted",
                kind="single",
                attempt_number=index + 1,
                value=None,
                previous_value=previous_attempts[index],
            )
        )

    old_complete = is_complete(previous_average)
    new_complete = is_complete(current_average)
    if new_complete and not old_complete:
        changes.append(ResultChange("added", "average", current_average))
    elif old_complete and new_complete and previous_average != current_average:
        changes.append(
            ResultChange(
                "corrected",
                "average",
                current_average,
                previous_value=previous_average,
            )
        )
    elif old_complete and not new_complete:
        changes.append(ResultChange("retracted", "average", None, previous_value=previous_average))
    return tuple(changes)
