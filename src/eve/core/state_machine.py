from collections.abc import Mapping
from enum import StrEnum

from eve.core.errors import ConflictError


class InvalidStatusTransitionError(ConflictError):
    code = "INVALID_STATUS_TRANSITION"


class StateMachine[S: StrEnum]:
    """A table of allowed status transitions.

    `check()` is the single gate every status change goes through. Moving to the current
    status is reported as a no-op rather than an error, which is what lets repeated
    requests or redelivered events be applied safely.
    """

    def __init__(self, name: str, transitions: Mapping[S, frozenset[S]]) -> None:
        self._name = name
        self._transitions = transitions

    def can(self, current: S, target: S) -> bool:
        return target in self._transitions[current]

    def check(self, current: S, target: S) -> bool:
        """Return True if the transition should be applied, False if it is a no-op.

        Raises InvalidStatusTransitionError (409) when the transition is not allowed.
        """
        if current == target:
            return False
        if not self.can(current, target):
            raise InvalidStatusTransitionError(
                f"{self._name} cannot move from {current} to {target}",
                details={"from": current.value, "to": target.value},
            )
        return True

    def is_terminal(self, status: S) -> bool:
        return not self._transitions[status]
