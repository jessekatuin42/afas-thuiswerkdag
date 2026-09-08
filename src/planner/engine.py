"""Executes a diff against the real systems, under the safety rules.

The rules, in one place because they are the whole point:

  1. Never file against unread state -- enforced upstream in diff().
  2. Never retry an uncertain submission.
  3. An UNVERIFIED outcome stops the entire run, both systems included.
  4. A definite failure (FAILED / REFUSED) blocks only its own system.
  5. Execute sequentially. Both systems are stateful sessions.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from ..adapters.base import DayFiler, FileOutcome
from .diff import diff, to_file
from .model import Action
from .store import PlanStore

#: Outcomes that prove the day is now present in the system.
_SETTLED = {FileOutcome.FILED, FileOutcome.ALREADY}


@dataclass
class SyncReport:
    run_id: int
    outcome: str                      # done | partial | stopped_unverified | nothing_to_do
    results: list[dict] = field(default_factory=list)
    stopped_reason: str = ""


class SyncEngine:
    def __init__(self, store: PlanStore, filers: dict[str, DayFiler]):
        self._store = store
        self._filers = filers

    def refresh_state(self, year: int, month: int) -> dict[str, str]:
        """Re-read both systems. Returns a per-system status message.

        A system that raises is left *unread* rather than recorded as empty:
        "I could not look" and "there is nothing there" must never collapse
        into the same stored value.
        """
        status: dict[str, str] = {}
        for system, filer in self._filers.items():
            try:
                entries = filer.read_month(year, month)
            except Exception as exc:
                status[system] = f"{type(exc).__name__}: {exc}"
                continue
            for day in _days_in_month(year, month):
                entry = entries.get(day)
                self._store.set_state(
                    day, system, entry is not None,
                    entry.summary if entry else "",
                    amount=entry.amount if entry else None,
                    km=entry.km if entry else None,
                )
            status[system] = f"read {len(entries)} entr(y/ies)"
        return status

    def preview(self, year: int, month: int) -> list[Action]:
        return diff(self._store.get_plan(year, month),
                    self._store.get_state(year, month))

    def sync(self, year: int, month: int, run_id: int | None = None) -> SyncReport:
        """File the month's outstanding days.

        ``run_id`` lets the caller open the run row first, so a background
        caller can hand out an id to poll before any work has happened. Left
        None, the run is opened here as usual.
        """
        if run_id is None:
            run_id = self._store.start_run()

        actions = to_file(self.preview(year, month))
        if not actions:
            self._store.finish_run(run_id, "nothing_to_do")
            return SyncReport(run_id, "nothing_to_do")

        blocked: set[str] = set()
        outcome = "done"
        stopped_reason = ""

        for action in actions:
            system = action.system
            if system in blocked:
                self._store.record_result(run_id, action.day, system, "skipped",
                                          "an earlier day on this system failed")
                outcome = "partial"
                continue

            result = self._filers[system].file(action.day)
            self._store.record_result(run_id, action.day, system,
                                      result.outcome.value, result.message)

            if result.outcome in _SETTLED:
                # mark_present, not set_state: filing proves the day exists but
                # not what it is worth, and set_state would null the amount.
                self._store.mark_present(action.day, system, result.message)
                continue

            if result.outcome is FileOutcome.UNVERIFIED:
                # Rule 3. Stop everything -- we do not know what landed.
                stopped_reason = (
                    f"{system} could not confirm {action.day.isoformat()}; "
                    "check it by hand before syncing again"
                )
                outcome = "stopped_unverified"
                break

            # Rule 4: definite failure, isolated to this system.
            blocked.add(system)
            outcome = "partial"

        self._store.finish_run(run_id, outcome)
        return SyncReport(run_id, outcome,
                          self._store.get_run_results(run_id), stopped_reason)


def _days_in_month(year: int, month: int) -> list[date]:
    return [date(year, month, d)
            for d in range(1, calendar.monthrange(year, month)[1] + 1)]
