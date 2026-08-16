"""Small value types shared across the tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Outcome(str, Enum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"
    WOULD_CREATE = "would_create"      # dry-run, none present
    WOULD_SKIP = "would_skip"          # dry-run, already present
    UNVERIFIED = "unverified"          # submitted, AFAS did not confirm
    FAILED = "failed"


# Process exit codes. 0 = nothing is wrong (created, exists, dry-run).
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNVERIFIED = 2
EXIT_USAGE = 3


@dataclass(frozen=True)
class Declaration:
    """One declaration row as read off the AFAS 'Mijn declaraties' page."""

    date: date | None
    description: str
    raw_text: str
    amount: str = ""
    status: str = ""

    def summary(self) -> str:
        parts = [self.description.strip() or "(no description)"]
        if self.date:
            parts.append(self.date.strftime("%d-%m-%Y"))
        if self.amount:
            parts.append(self.amount)
        if self.status:
            parts.append(self.status)
        return " | ".join(p for p in parts if p)


@dataclass
class Result:
    outcome: Outcome
    target_date: date
    existing: Declaration | None = None
    amount: str = ""
    messages: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.outcome is Outcome.FAILED:
            return EXIT_FAILED
        if self.outcome is Outcome.UNVERIFIED:
            return EXIT_UNVERIFIED
        return EXIT_OK
