"""plan x state -> actions.

Pure by design, and deliberately the only place that decides what gets filed.
Keeping it free of I/O is what makes the safety-critical behaviour --
especially "never file against unread state" -- exhaustively testable offline.
The same discipline that keeps detection.py trustworthy.
"""

from __future__ import annotations

from datetime import date

from .model import AFAS, SHUTTEL, Action, ActionKind, DayState, Intent, SYSTEM_FOR_INTENT

_SYSTEMS = (AFAS, SHUTTEL)


def diff(
    plan: dict[date, Intent],
    state: dict[date, DayState | None],
) -> list[Action]:
    """Every action implied by the plan, ordered by date.

    A day missing from ``state``, a ``None`` value, or a ``DayState`` whose
    relevant system is ``None`` all mean the same thing: that system has not
    been read for that date. Every one of them yields UNKNOWN and never FILE,
    because filing blind is how a declaration gets duplicated.
    """
    actions: list[Action] = []

    for day in sorted(plan):
        intent = plan[day]
        wanted = SYSTEM_FOR_INTENT[intent]
        observed = state.get(day)

        if observed is None:
            if wanted is not None:
                actions.append(
                    Action(day, wanted, ActionKind.UNKNOWN,
                           "state not read; refusing to file blind")
                )
            continue

        for system in _SYSTEMS:
            present = observed.has(system)
            if system == wanted:
                if present is None:
                    actions.append(
                        Action(day, system, ActionKind.UNKNOWN,
                               "state not read; refusing to file blind")
                    )
                else:
                    actions.append(
                        Action(day, system,
                               ActionKind.SATISFIED if present else ActionKind.FILE)
                    )
            elif present is True:
                # present is None means we never read that system, which is not
                # grounds to claim a conflict.
                actions.append(
                    Action(day, system, ActionKind.CONFLICT,
                           f"{system} holds an entry the plan does not call for")
                )

    return actions


def to_file(actions: list[Action]) -> list[Action]:
    """Only the actions a sync may execute."""
    return [a for a in actions if a.kind is ActionKind.FILE]
