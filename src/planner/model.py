"""Value types for the day planner. Pure: no I/O, no Playwright, no network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

AFAS = "afas"
SHUTTEL = "shuttel"


class Intent(str, Enum):
    """What the user wants a date to be."""

    HOME = "home"      # -> an AFAS Thuiswerkdag, nothing in Shuttel
    OFFICE = "office"  # -> a Shuttel commute trip, nothing in AFAS
    NONE = "none"      # -> nothing anywhere


#: Each system gets only what it pays for. Decided 2026-09-08.
SYSTEM_FOR_INTENT: dict[Intent, str | None] = {
    Intent.HOME: AFAS,
    Intent.OFFICE: SHUTTEL,
    Intent.NONE: None,
}


class ActionKind(str, Enum):
    FILE = "file"            # must be filed
    SATISFIED = "satisfied"  # already present and wanted
    CONFLICT = "conflict"    # present but not wanted -- shown, never corrected
    UNKNOWN = "unknown"      # state was never read; filing is refused


@dataclass(frozen=True)
class DayState:
    """What each system actually holds for one date.

    Tri-state per system on purpose. ``False`` means "we looked and there is
    nothing"; ``None`` means "we never looked at this system". Collapsing those
    two into one boolean would let an unread system be filed against blindly,
    which is the exact failure read-back exists to prevent.
    """

    afas: bool | None = None
    shuttel: bool | None = None

    def has(self, system: str) -> bool | None:
        return self.afas if system == AFAS else self.shuttel


@dataclass(frozen=True)
class Action:
    day: date
    system: str
    kind: ActionKind
    reason: str = ""


# ---------------------------------------------------------------------------
# Which weekdays are home days by default
#
# Deliberately the same AFAS_DAYS variable, and the same ISO-weekday encoding
# (Mon=1 ... Sun=7), that scripts/daily-run.sh already reads. One setting
# governing both the timer and the dashboard is worth more than a tidier name:
# two settings for the same fact drift apart, and the failure mode is a day
# declared in one place and not the other.
# ---------------------------------------------------------------------------

DEFAULT_HOME_DAYS: tuple[int, ...] = (2, 3, 4)   # Tue, Wed, Thu

WEEKDAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
                 5: "Fri", 6: "Sat", 7: "Sun"}


class InvalidWeekdaysError(ValueError):
    """AFAS_DAYS could not be read. Never fall back silently."""


def parse_home_days(raw: str | None) -> tuple[int, ...]:
    """Parse an AFAS_DAYS list of ISO weekdays.

    Empty or unset gives the default, matching the shell's
    ``${AFAS_DAYS:-2,3,4}``. Anything malformed raises rather than falling
    back: daily-run.sh skips rather than guessing, and guessing here would
    mark days the user never chose.
    """
    if raw is None or not raw.strip():
        return DEFAULT_HOME_DAYS

    days: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= 7:
            raise InvalidWeekdaysError(
                f"unreadable AFAS_DAYS {raw!r} - expected ISO weekdays "
                f"like 2,3,4 (Mon=1 ... Sun=7)"
            )
        days.add(int(part))
    if not days:
        return DEFAULT_HOME_DAYS
    return tuple(sorted(days))
