"""What this pay period is worth, and how far it was driven.

Pure: it is handed observations and a plan, and returns numbers. No I/O, so
the arithmetic behind a figure you will check against a payslip is testable
offline -- the same discipline as detection.py and diff.py.

Amounts are never derived from a hardcoded rate. Both systems report what a day
is actually worth (AFAS's grid total, Shuttel's settlement_net), and reading it
back means a rate change shows up on its own instead of silently making every
figure wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .model import SYSTEM_FOR_INTENT, Intent

#: The pay period resets on the 25th, so a period runs 25th -> 24th and
#: straddles two calendar months.
PERIOD_START_DAY = 25


def period_for(day: date) -> tuple[date, date]:
    """The inclusive pay period containing ``day``."""
    if day.day >= PERIOD_START_DAY:
        start = day.replace(day=PERIOD_START_DAY)
        end_year, end_month = (day.year + (day.month == 12), day.month % 12 + 1)
    else:
        end_year, end_month = day.year, day.month
        prev_year, prev_month = (day.year - (day.month == 1),
                                 12 if day.month == 1 else day.month - 1)
        start = date(prev_year, prev_month, PERIOD_START_DAY)
    return start, date(end_year, end_month, PERIOD_START_DAY - 1)


@dataclass(frozen=True)
class Observation:
    """What a system holds for one date."""

    present: bool
    amount: float | None = None    # euro
    km: float | None = None


@dataclass
class SystemEarnings:
    system: str
    filed_days: int = 0
    #: Filed days that actually reported an amount. Kept apart from
    #: filed_days because state read before the money columns existed is
    #: present-but-unpriced, and averaging over those makes a day look free.
    priced_days: int = 0
    eur: float = 0.0
    km: float = 0.0
    planned_unfiled_days: int = 0
    projected_eur: float = 0.0
    #: True when projected_eur leans on an average or a default rather than on
    #: what the system actually reported.
    estimated: bool = False


@dataclass
class Earnings:
    start: date
    end: date
    per_system: dict[str, SystemEarnings] = field(default_factory=dict)
    eur: float = 0.0
    km: float = 0.0
    projected_eur: float = 0.0
    estimated: bool = False
    #: Systems with planned days whose value could not be estimated at all.
    #: Reported so a low projection is never mistaken for a complete one.
    incomplete: list[str] = field(default_factory=list)


def earnings(
    observed: dict[tuple[date, str], Observation],
    plan: dict[date, Intent],
    start: date,
    end: date,
    defaults: dict[str, float] | None = None,
) -> Earnings:
    """Total the period, and project what the plan would add to it.

    ``defaults`` gives a per-day fallback for a system with nothing filed yet
    to average over -- AFAS pays a flat amount, so it has one; Shuttel's
    depends on the route, so it does not.
    """
    defaults = defaults or {}
    systems = {s for s in SYSTEM_FOR_INTENT.values() if s}
    result = Earnings(start=start, end=end,
                      per_system={s: SystemEarnings(system=s) for s in systems})

    for (day, system), ob in observed.items():
        if not (start <= day <= end) or system not in result.per_system:
            continue
        if not ob.present:
            continue
        acc = result.per_system[system]
        acc.filed_days += 1
        if ob.amount is not None:
            acc.priced_days += 1
            acc.eur += ob.amount
        acc.km += ob.km or 0.0

    for day, intent in plan.items():
        wanted = SYSTEM_FOR_INTENT[intent]
        if wanted is None or not (start <= day <= end):
            continue
        ob = observed.get((day, wanted))
        if ob is not None and ob.present:
            continue                      # already filed; already counted
        result.per_system[wanted].planned_unfiled_days += 1

    for system, acc in result.per_system.items():
        # Average over days that reported a price, not over every filed day.
        per_day = (acc.eur / acc.priced_days) if acc.priced_days else defaults.get(system)
        if acc.planned_unfiled_days and per_day is None:
            # Neither an observation to average nor a default: say so rather
            # than quietly reporting a projection that is only the filed total.
            acc.projected_eur = acc.eur
            result.incomplete.append(system)
        else:
            acc.projected_eur = acc.eur + acc.planned_unfiled_days * (per_day or 0.0)
            acc.estimated = bool(acc.planned_unfiled_days)

        result.eur += acc.eur
        result.km += acc.km
        result.projected_eur += acc.projected_eur
        result.estimated = result.estimated or acc.estimated

    result.incomplete.sort()
    return result
