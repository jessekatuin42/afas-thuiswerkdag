"""The one interface both systems are driven through.

Deliberately has no `delete`: removing a filed declaration is destructive and
both systems treat a submission as a financial record. A mismatch is shown to
the user, never silently corrected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Protocol


class FileOutcome(str, Enum):
    FILED = "filed"            # confirmed by re-reading the system
    ALREADY = "already"        # was already there; nothing submitted
    UNVERIFIED = "unverified"  # submitted, could not confirm -- STOPS THE RUN
    FAILED = "failed"          # definite failure, isolated to this system
    REFUSED = "refused"        # entitlement withdrawn; not a bug


@dataclass(frozen=True)
class Entry:
    """One thing a system already holds for a date."""

    day: date
    summary: str = ""
    #: What the system says the day is worth, in euro, and how far it was
    #: driven. Read back rather than computed: a rate change should show up on
    #: its own instead of making every figure quietly wrong.
    amount: float | None = None
    km: float | None = None


@dataclass(frozen=True)
class FileResult:
    day: date
    system: str
    outcome: FileOutcome
    message: str = ""
    artifacts: list[str] = field(default_factory=list)


class DayFiler(Protocol):
    system: str

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        """Everything this system holds for the month. Read-only.

        An empty mapping means "nothing known", which the caller must not
        confuse with "nothing exists" -- see diff()'s UNKNOWN handling.
        """

    def file(self, day: date) -> FileResult:
        """File one day. The caller NEVER retries this."""
