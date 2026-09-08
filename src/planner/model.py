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
