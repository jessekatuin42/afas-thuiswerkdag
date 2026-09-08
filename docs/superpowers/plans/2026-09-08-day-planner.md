# Day Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local dashboard where you paint a month's days as Home or Office, and sync files AFAS Thuiswerkdag declarations for the home days and Shuttel commute entries for the office days.

**Architecture:** One FastAPI app on loopback. Three things are kept separate per date — your *intent*, the *state* read back from each system, and the *diff* between them — so an interrupted sync is simply a smaller diff next time rather than a state to repair. Two adapters sit behind one `DayFiler` protocol: AFAS drives the existing proven Playwright code, Shuttel talks to a Keycloak-protected REST API with no browser at all.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, httpx, stdlib `sqlite3` (no ORM), vanilla HTML/JS (no build step), existing Playwright/pyotp/python-dotenv.

**Spec:** `docs/superpowers/specs/2026-09-08-day-planner-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Bind to `127.0.0.1`, never `0.0.0.0`.** This dashboard files financial declarations and must not be reachable from the LAN.
- **No delete/amend in v1.** A mismatch is *shown*, never silently corrected.
- **Never retry an uncertain submission.** A retry after a submit that may have landed is how duplicates are created.
- **An `UNVERIFIED` outcome stops the entire run**, both systems included. A *definite* failure isolates to the system that produced it.
- **No test files a real entry**, in either system.
- **Do not modify `src/afas.py`, `src/detection.py`, `src/browser.py`, or `src/models.py`.** They are proven live. New code wraps them.
- **This repository is public.** No employer-specific values in source — no tenant numbers, saved-trip IDs, or account identifiers. Configuration only, mirroring the existing `DEFAULT_TENANT == ""` test.
- **Secrets never logged, printed, screenshotted, or committed.** Follow the existing `Credentials.__repr__` pattern.
- **The existing 142 tests must keep passing** (`python -m pytest tests/ -q`).
- Python 3.12. Ruff-clean. `from __future__ import annotations` at the top of every new module, matching the existing files.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/planner/model.py` | `Intent`, `DayState`, `Action`, `ActionKind`. Pure value types. |
| `src/planner/diff.py` | `plan x state -> actions`. Pure, no I/O. **Safety-critical.** |
| `src/planner/store.py` | SQLite persistence for plan, state cache, run history. |
| `src/adapters/base.py` | `DayFiler` protocol, `Entry`, `FileResult`. |
| `src/adapters/afas_adapter.py` | Wraps existing `AfasInSite`. Adds no AFAS knowledge. |
| `src/adapters/shuttel.py` | Keycloak token client + REST calls. |
| `src/planner/engine.py` | `SyncEngine` — executes a diff under the safety rules. |
| `web/app.py` | FastAPI routes. |
| `web/static/index.html` | Calendar UI, no build step. |
| `docker-compose.yml` | Local stack. |

Rationale for the split: `diff.py` is the new safety-critical logic and must be testable with no browser, no network and no database — the same discipline that keeps `detection.py` trustworthy. `engine.py` is separate from `diff.py` because the engine has I/O and the diff must not.

---

## Task 1: Pure planner model and diff

**Files:**
- Create: `src/planner/__init__.py` (empty)
- Create: `src/planner/model.py`
- Create: `src/planner/diff.py`
- Test: `tests/test_planner_diff.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Intent` (`HOME`/`OFFICE`/`NONE`), `DayState(afas: bool, shuttel: bool)`, `ActionKind` (`FILE`/`SATISFIED`/`CONFLICT`/`UNKNOWN`), `Action(day, system, kind, reason)`, and `diff(plan: dict[date, Intent], state: dict[date, DayState | None]) -> list[Action]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_diff.py`:

```python
from __future__ import annotations

from datetime import date

from src.planner.diff import diff
from src.planner.model import Action, ActionKind, DayState, Intent

D1 = date(2026, 9, 8)


def kinds(actions: list[Action], system: str) -> list[ActionKind]:
    return [a.kind for a in actions if a.system == system]


#: Both systems read, both empty. Spelled out because DayState() means
#: "neither system was read", which is a different thing entirely.
BOTH_EMPTY = DayState(afas=False, shuttel=False)


def test_home_day_with_nothing_filed_files_afas_only():
    actions = diff({D1: Intent.HOME}, {D1: BOTH_EMPTY})
    assert kinds(actions, "afas") == [ActionKind.FILE]
    assert kinds(actions, "shuttel") == []


def test_home_day_already_in_afas_is_satisfied():
    actions = diff({D1: Intent.HOME}, {D1: DayState(afas=True, shuttel=False)})
    assert kinds(actions, "afas") == [ActionKind.SATISFIED]


def test_office_day_with_nothing_filed_files_shuttel_only():
    actions = diff({D1: Intent.OFFICE}, {D1: BOTH_EMPTY})
    assert kinds(actions, "shuttel") == [ActionKind.FILE]
    assert kinds(actions, "afas") == []


def test_office_day_already_in_shuttel_is_satisfied():
    actions = diff({D1: Intent.OFFICE}, {D1: DayState(afas=False, shuttel=True)})
    assert kinds(actions, "shuttel") == [ActionKind.SATISFIED]


def test_office_day_with_a_stray_afas_entry_reports_conflict_and_still_files():
    actions = diff({D1: Intent.OFFICE}, {D1: DayState(afas=True, shuttel=False)})
    assert kinds(actions, "shuttel") == [ActionKind.FILE]
    assert kinds(actions, "afas") == [ActionKind.CONFLICT]


def test_none_day_with_an_entry_reports_conflict_and_files_nothing():
    actions = diff({D1: Intent.NONE}, {D1: DayState(afas=True, shuttel=False)})
    assert kinds(actions, "afas") == [ActionKind.CONFLICT]
    assert not [a for a in actions if a.kind is ActionKind.FILE]


def test_none_day_with_nothing_produces_no_actions():
    assert diff({D1: Intent.NONE}, {D1: BOTH_EMPTY}) == []


def test_unread_target_system_is_unknown_even_when_the_other_was_read():
    """The partial-read case. Only AFAS has been read; Shuttel is unknown, so
    an office day must not be filed against it."""
    actions = diff({D1: Intent.OFFICE}, {D1: DayState(afas=False, shuttel=None)})
    assert kinds(actions, "shuttel") == [ActionKind.UNKNOWN]
    assert not [a for a in actions if a.kind is ActionKind.FILE]


def test_unread_other_system_is_not_reported_as_a_conflict():
    """Never having looked at AFAS is not evidence that AFAS holds something."""
    actions = diff({D1: Intent.OFFICE}, {D1: DayState(afas=None, shuttel=False)})
    assert kinds(actions, "shuttel") == [ActionKind.FILE]
    assert kinds(actions, "afas") == []


def test_unread_state_never_produces_a_file_action():
    """The single most important property here.

    State of None means 'we have not looked'. Filing blind would duplicate a
    declaration that already exists, which is exactly the failure the whole
    read-back design exists to prevent.
    """
    actions = diff({D1: Intent.HOME}, {D1: None})
    assert not [a for a in actions if a.kind is ActionKind.FILE]
    assert kinds(actions, "afas") == [ActionKind.UNKNOWN]


def test_missing_state_key_is_treated_as_unread_not_as_empty():
    actions = diff({D1: Intent.HOME}, {})
    assert not [a for a in actions if a.kind is ActionKind.FILE]
    assert kinds(actions, "afas") == [ActionKind.UNKNOWN]


def test_actions_are_ordered_by_date():
    d0, d2 = date(2026, 9, 1), date(2026, 9, 30)
    plan = {D1: Intent.HOME, d2: Intent.HOME, d0: Intent.HOME}
    state = {d: BOTH_EMPTY for d in (d0, D1, d2)}
    assert [a.day for a in diff(plan, state)] == [d0, D1, d2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_planner_diff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.planner'`

- [ ] **Step 3: Write the model**

Create `src/planner/__init__.py` as an empty file, then `src/planner/model.py`:

```python
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
```

- [ ] **Step 4: Write the diff**

Create `src/planner/diff.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_planner_diff.py -q`
Expected: PASS, 12 tests.

Then confirm nothing regressed: `python -m pytest tests/ -q`
Expected: PASS, 154 tests (142 existing + 12 new).

- [ ] **Step 6: Commit**

```bash
git add src/planner/__init__.py src/planner/model.py src/planner/diff.py tests/test_planner_diff.py
git commit -m "feat(planner): pure intent/state/diff model"
```

---

## Task 2: Plan storage

**Files:**
- Create: `src/planner/store.py`
- Test: `tests/test_planner_store.py`

**Interfaces:**
- Consumes: `Intent`, `DayState` from `src.planner.model`.
- Produces: `PlanStore(db_path: Path)` with `set_intent(day, intent)`, `get_plan(year, month) -> dict[date, Intent]`, `set_state(day, system, present, summary)`, `get_state(year, month) -> dict[date, DayState | None]`, `state_read_at(system) -> datetime | None`, `record_result(run_id, day, system, outcome, message)`, `start_run() -> int`, `finish_run(run_id, outcome)`, `get_run_results(run_id) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_store.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from src.planner.model import DayState, Intent
from src.planner.store import PlanStore

D1 = date(2026, 9, 8)
D2 = date(2026, 9, 9)


@pytest.fixture
def store(tmp_path):
    return PlanStore(tmp_path / "plan.db")


def test_intent_round_trips(store):
    store.set_intent(D1, Intent.HOME)
    assert store.get_plan(2026, 9) == {D1: Intent.HOME}


def test_setting_intent_again_overwrites_rather_than_duplicating(store):
    store.set_intent(D1, Intent.HOME)
    store.set_intent(D1, Intent.OFFICE)
    assert store.get_plan(2026, 9) == {D1: Intent.OFFICE}


def test_intent_none_is_removed_from_the_plan(store):
    store.set_intent(D1, Intent.HOME)
    store.set_intent(D1, Intent.NONE)
    assert store.get_plan(2026, 9) == {}


def test_get_plan_is_scoped_to_the_month(store):
    store.set_intent(D1, Intent.HOME)
    store.set_intent(date(2026, 10, 1), Intent.HOME)
    assert list(store.get_plan(2026, 9)) == [D1]


def test_unread_day_has_state_none_not_an_empty_daystate(store):
    """None and DayState() mean different things to diff(); the store must
    preserve that distinction or the safety property is lost at the boundary."""
    assert store.get_state(2026, 9) == {}


def test_state_round_trips_per_system(store):
    store.set_state(D1, "afas", True, "Thuiswerkdag | 08-09-2026")
    store.set_state(D1, "shuttel", False, "")
    assert store.get_state(2026, 9) == {D1: DayState(afas=True, shuttel=False)}


def test_reading_one_system_leaves_the_other_unread_not_empty(store):
    """Partial reads are normal -- AFAS takes ~16s, Shuttel is instant, so they
    finish at different times. The unread one must stay None."""
    store.set_state(D1, "afas", True, "")
    assert store.get_state(2026, 9) == {D1: DayState(afas=True, shuttel=None)}


def test_state_read_at_is_recorded_per_system(store):
    assert store.state_read_at("afas") is None
    store.set_state(D1, "afas", True, "")
    assert store.state_read_at("afas") is not None
    assert store.state_read_at("shuttel") is None


def test_run_lifecycle_and_results(store):
    run_id = store.start_run()
    store.record_result(run_id, D1, "afas", "created", "")
    store.record_result(run_id, D2, "afas", "failed", "boom")
    store.finish_run(run_id, "failed")
    results = store.get_run_results(run_id)
    assert [(r["date"], r["outcome"]) for r in results] == [
        (D1, "created"),
        (D2, "failed"),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_planner_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.planner.store'`

- [ ] **Step 3: Write the store**

Create `src/planner/store.py`:

```python
"""SQLite persistence for the plan, the read-back state cache, and run history.

stdlib sqlite3 on purpose: one user, one process, a few hundred rows. An ORM
would be more dependency than this earns.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .model import DayState, Intent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS day_plan (
    date       TEXT PRIMARY KEY,
    intent     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_cache (
    date    TEXT NOT NULL,
    system  TEXT NOT NULL,
    present INTEGER NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    read_at TEXT NOT NULL,
    PRIMARY KEY (date, system)
);
CREATE TABLE IF NOT EXISTS sync_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT
);
CREATE TABLE IF NOT EXISTS day_result (
    run_id  INTEGER NOT NULL,
    date    TEXT NOT NULL,
    system  TEXT NOT NULL,
    outcome TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    seq     INTEGER NOT NULL
);
"""


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start.isoformat(), end.isoformat()


class PlanStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- plan -------------------------------------------------------------

    def set_intent(self, day: date, intent: Intent) -> None:
        if intent is Intent.NONE:
            self._conn.execute("DELETE FROM day_plan WHERE date = ?", (day.isoformat(),))
        else:
            self._conn.execute(
                "INSERT INTO day_plan (date, intent, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET intent = excluded.intent, "
                "updated_at = excluded.updated_at",
                (day.isoformat(), intent.value, datetime.now().isoformat()),
            )
        self._conn.commit()

    def get_plan(self, year: int, month: int) -> dict[date, Intent]:
        lo, hi = _month_bounds(year, month)
        rows = self._conn.execute(
            "SELECT date, intent FROM day_plan WHERE date >= ? AND date < ? ORDER BY date",
            (lo, hi),
        ).fetchall()
        return {date.fromisoformat(r["date"]): Intent(r["intent"]) for r in rows}

    # -- state cache ------------------------------------------------------

    def set_state(self, day: date, system: str, present: bool, summary: str = "") -> None:
        self._conn.execute(
            "INSERT INTO state_cache (date, system, present, summary, read_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(date, system) DO UPDATE SET "
            "present = excluded.present, summary = excluded.summary, "
            "read_at = excluded.read_at",
            (day.isoformat(), system, int(present), summary, datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_state(self, year: int, month: int) -> dict[date, DayState]:
        lo, hi = _month_bounds(year, month)
        rows = self._conn.execute(
            "SELECT date, system, present FROM state_cache WHERE date >= ? AND date < ?",
            (lo, hi),
        ).fetchall()
        acc: dict[date, dict[str, bool]] = {}
        for r in rows:
            acc.setdefault(date.fromisoformat(r["date"]), {})[r["system"]] = bool(r["present"])
        # .get() with no default: a system with no row stays None ("never
        # read"), which diff() refuses to file against. Defaulting to False
        # here would silently re-introduce blind filing.
        return {
            day: DayState(afas=flags.get("afas"), shuttel=flags.get("shuttel"))
            for day, flags in acc.items()
        }

    def state_read_at(self, system: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(read_at) AS t FROM state_cache WHERE system = ?", (system,)
        ).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    # -- runs -------------------------------------------------------------

    def start_run(self) -> int:
        cur = self._conn.execute(
            "INSERT INTO sync_run (started_at) VALUES (?)", (datetime.now().isoformat(),)
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_result(
        self, run_id: int, day: date, system: str, outcome: str, message: str = ""
    ) -> None:
        seq = self._conn.execute(
            "SELECT COUNT(*) AS n FROM day_result WHERE run_id = ?", (run_id,)
        ).fetchone()["n"]
        self._conn.execute(
            "INSERT INTO day_result (run_id, date, system, outcome, message, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, day.isoformat(), system, outcome, message, seq),
        )
        self._conn.commit()

    def finish_run(self, run_id: int, outcome: str) -> None:
        self._conn.execute(
            "UPDATE sync_run SET finished_at = ?, outcome = ? WHERE id = ?",
            (datetime.now().isoformat(), outcome, run_id),
        )
        self._conn.commit()

    def get_run_results(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT date, system, outcome, message FROM day_result "
            "WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [
            {
                "date": date.fromisoformat(r["date"]),
                "system": r["system"],
                "outcome": r["outcome"],
                "message": r["message"],
            }
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_planner_store.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/planner/store.py tests/test_planner_store.py
git commit -m "feat(planner): SQLite plan, state cache and run history"
```

---

## Task 3: Adapter protocol and the AFAS adapter

**Files:**
- Create: `src/adapters/__init__.py` (empty)
- Create: `src/adapters/base.py`
- Create: `src/adapters/afas_adapter.py`
- Test: `tests/test_afas_adapter.py`

**Interfaces:**
- Consumes: existing `AfasInSite`, `Declaration`, `Outcome`, `Result` (unchanged), `summarize_thuiswerkdagen(rows, labels) -> list[Declaration]`.
- Produces: `Entry(day, summary)`, `FileOutcome` (`FILED`/`ALREADY`/`UNVERIFIED`/`FAILED`/`REFUSED`), `FileResult(day, system, outcome, message, artifacts)`, `DayFiler` protocol, `AfasAdapter(afas: AfasInSite, labels: tuple[str, ...])`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_afas_adapter.py`. It uses a fake `AfasInSite` — no browser, no network:

```python
from __future__ import annotations

from datetime import date

from src.adapters.afas_adapter import AfasAdapter
from src.adapters.base import FileOutcome
from src.afas import AfasRefusedError
from src.models import Declaration, Outcome, Result

LABELS = ("thuiswerkdag",)
D1 = date(2026, 9, 8)


def decl(d: date) -> Declaration:
    return Declaration(date=d, description="Thuiswerkdag", raw_text="", amount="2,00")


class FakeAfas:
    """Stands in for AfasInSite. Records calls; performs no I/O."""

    def __init__(self, rows=(), result=None, raises=None):
        self._rows = list(rows)
        self._result = result
        self._raises = raises
        self.created: list[date] = []

    def open_declarations(self):
        pass

    def filter_by_soort(self, soort):
        return True

    def read_grid_rows(self):
        return self._rows

    def run(self, target, dry_run=False):
        if self._raises:
            raise self._raises
        self.created.append(target)
        return self._result


def test_read_month_returns_only_days_in_that_month(monkeypatch):
    import src.adapters.afas_adapter as mod

    monkeypatch.setattr(
        mod, "summarize_thuiswerkdagen",
        lambda rows, labels: [decl(D1), decl(date(2026, 8, 31))],
    )
    entries = AfasAdapter(FakeAfas(rows=[object()]), LABELS).read_month(2026, 9)
    assert list(entries) == [D1]


def test_read_month_is_empty_when_the_grid_cannot_be_read():
    """read_grid_rows() returns [] when headers are unresolvable. That must
    surface as 'nothing known', never as 'nothing exists'."""
    assert AfasAdapter(FakeAfas(rows=[]), LABELS).read_month(2026, 9) == {}


def test_file_maps_created_to_filed():
    fake = FakeAfas(result=Result(outcome=Outcome.CREATED, target_date=D1, amount="2,00"))
    res = AfasAdapter(fake, LABELS).file(D1)
    assert res.outcome is FileOutcome.FILED
    assert fake.created == [D1]


def test_file_maps_already_exists_to_already():
    fake = FakeAfas(result=Result(outcome=Outcome.ALREADY_EXISTS, target_date=D1))
    assert AfasAdapter(fake, LABELS).file(D1).outcome is FileOutcome.ALREADY


def test_file_maps_unverified_to_unverified_and_keeps_artifacts():
    fake = FakeAfas(result=Result(
        outcome=Outcome.UNVERIFIED, target_date=D1,
        messages=["not confirmed"], artifacts=["artifacts/x.png"],
    ))
    res = AfasAdapter(fake, LABELS).file(D1)
    assert res.outcome is FileOutcome.UNVERIFIED
    assert res.artifacts == ["artifacts/x.png"]


def test_file_maps_a_refusal_to_refused_rather_than_failed():
    """An entitlement problem is not a bug, and must not read like one."""
    fake = FakeAfas(raises=AfasRefusedError("not authorized"))
    res = AfasAdapter(fake, LABELS).file(D1)
    assert res.outcome is FileOutcome.REFUSED
    assert "not authorized" in res.message


def test_file_never_passes_dry_run_true():
    """A dry run returns before create_thuiswerkdag, so an adapter that passed
    dry_run=True would silently file nothing while reporting success."""
    seen = {}

    class Recording(FakeAfas):
        def run(self, target, dry_run=False):
            seen["dry_run"] = dry_run
            return Result(outcome=Outcome.CREATED, target_date=target)

    AfasAdapter(Recording(), LABELS).file(D1)
    assert seen["dry_run"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_afas_adapter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.adapters'`

- [ ] **Step 3: Write the protocol**

Create `src/adapters/__init__.py` as an empty file, then `src/adapters/base.py`:

```python
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
```

- [ ] **Step 4: Write the AFAS adapter**

Create `src/adapters/afas_adapter.py`:

```python
"""Adapts the existing, proven AfasInSite to the DayFiler protocol.

Adds no AFAS knowledge of its own. All grid reading, duplicate detection and
the two-step create flow stay in src/afas.py and src/detection.py, which are
not modified.
"""

from __future__ import annotations

from datetime import date

from ..afas import AfasInSite, AfasRefusedError
from ..detection import summarize_thuiswerkdagen
from ..models import Outcome
from .base import Entry, FileOutcome, FileResult

_OUTCOME_MAP = {
    Outcome.CREATED: FileOutcome.FILED,
    Outcome.ALREADY_EXISTS: FileOutcome.ALREADY,
    Outcome.UNVERIFIED: FileOutcome.UNVERIFIED,
    Outcome.FAILED: FileOutcome.FAILED,
}


class AfasAdapter:
    system = "afas"

    def __init__(self, afas: AfasInSite, labels: tuple[str, ...]):
        self._afas = afas
        self._labels = labels

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        self._afas.open_declarations()
        self._afas.filter_by_soort("Thuiswerkdag")
        rows = self._afas.read_grid_rows()
        found: dict[date, Entry] = {}
        for declaration in summarize_thuiswerkdagen(rows, self._labels):
            d = declaration.date
            if d is not None and d.year == year and d.month == month:
                found[d] = Entry(day=d, summary=declaration.summary())
        return found

    def file(self, day: date) -> FileResult:
        try:
            # dry_run=False is not a default worth relying on: run() returns
            # before create_thuiswerkdag when dry_run is True, so passing it
            # would file nothing while reporting success.
            result = self._afas.run(day, dry_run=False)
        except AfasRefusedError as exc:
            return FileResult(day, self.system, FileOutcome.REFUSED, str(exc))
        except Exception as exc:  # fail closed, never optimistic
            return FileResult(day, self.system, FileOutcome.FAILED,
                              f"{type(exc).__name__}: {exc}")

        return FileResult(
            day=day,
            system=self.system,
            outcome=_OUTCOME_MAP.get(result.outcome, FileOutcome.FAILED),
            message="; ".join(result.messages),
            artifacts=list(result.artifacts),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_afas_adapter.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add src/adapters/ tests/test_afas_adapter.py
git commit -m "feat(adapters): DayFiler protocol and AFAS adapter"
```

---

## Task 4: Shuttel authentication (the API surface stays stubbed)

The Keycloak side is fully known and buildable today. The business endpoints are
not — see spec Open question 1. This task builds and tests everything that is
knowable, and makes the unknown part fail loudly rather than silently.

**Files:**
- Create: `src/adapters/shuttel.py`
- Modify: `requirements.txt`
- Test: `tests/test_shuttel_auth.py`

**Interfaces:**
- Consumes: `Entry`, `FileOutcome`, `FileResult` from `src.adapters.base`.
- Produces: `ShuttelCredentials(username, password)`, `ShuttelAuthError`, `ShuttelNotImplementedError`, `TokenClient(credentials, base_url=SHUTTEL_BASE, transport=None)` with `.access_token() -> str`, `ShuttelAdapter(token_client)`.

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
httpx>=0.27         # Shuttel REST + Keycloak token calls
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_shuttel_auth.py`. Uses `httpx.MockTransport` — no network:

```python
from __future__ import annotations

import json

import httpx
import pytest

from src.adapters.shuttel import (
    SHUTTEL_CLIENT_ID,
    ShuttelAuthError,
    ShuttelCredentials,
    TokenClient,
)

CREDS = ShuttelCredentials(username="someone@example.invalid", password="pw")


def transport_returning(*responses):
    """Serve the given responses in order, recording each request."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def token_response(access="a1", refresh="r1", expires_in=300):
    return httpx.Response(200, json={
        "access_token": access, "refresh_token": refresh,
        "expires_in": expires_in, "token_type": "Bearer",
    })


def form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


def test_first_call_uses_the_password_grant_with_the_portal_client():
    t = transport_returning(token_response())
    assert TokenClient(CREDS, transport=t).access_token() == "a1"
    body = form(t.seen[0])
    assert body["grant_type"] == "password"
    assert body["client_id"] == SHUTTEL_CLIENT_ID
    assert "offline_access" in body["scope"]


def test_token_endpoint_is_the_shuttel_realm():
    t = transport_returning(token_response())
    TokenClient(CREDS, transport=t).access_token()
    assert t.seen[0].url.path == "/auth/realms/shuttel/protocol/openid-connect/token"


def test_a_valid_token_is_reused_rather_than_refetched():
    t = transport_returning(token_response())
    client = TokenClient(CREDS, transport=t)
    assert client.access_token() == client.access_token()
    assert len(t.seen) == 1


def test_an_expired_token_is_refreshed_not_re_passworded():
    t = transport_returning(
        token_response(access="a1", expires_in=0),
        token_response(access="a2"),
    )
    client = TokenClient(CREDS, transport=t)
    assert client.access_token() == "a1"
    assert client.access_token() == "a2"
    assert form(t.seen[1])["grant_type"] == "refresh_token"


def test_a_rejected_password_raises_without_echoing_the_password():
    t = transport_returning(httpx.Response(401, json={"error": "invalid_grant"}))
    with pytest.raises(ShuttelAuthError) as exc:
        TokenClient(CREDS, transport=t).access_token()
    assert CREDS.password not in str(exc.value)


def test_a_failed_refresh_falls_back_to_the_password_grant():
    t = transport_returning(
        token_response(access="a1", expires_in=0),
        httpx.Response(400, json={"error": "invalid_grant"}),
        token_response(access="a3"),
    )
    client = TokenClient(CREDS, transport=t)
    client.access_token()
    assert client.access_token() == "a3"
    assert form(t.seen[2])["grant_type"] == "password"


def test_credentials_never_reveal_the_password_in_repr_or_str():
    assert "pw" not in repr(CREDS)
    assert "pw" not in str(CREDS)
    assert "pw" not in json.dumps(repr(CREDS))


def test_no_account_specific_values_are_baked_into_source():
    """This repository is public. Mirrors the existing DEFAULT_TENANT == ""
    test: vendor-level facts may be committed, account-level ones may not."""
    from src.adapters import shuttel

    assert shuttel.ShuttelCredentials().username == ""
    assert shuttel.ShuttelCredentials().password == ""
    # Vendor-level and identical for every Shuttel customer -- read from the
    # portal's own unauthenticated discovery endpoints, so committing these is
    # fine. Anything account-scoped must live in .env instead.
    assert shuttel.SHUTTEL_CLIENT_ID == "shuttel-portal"
    assert shuttel.SHUTTEL_REALM == "shuttel"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_shuttel_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.adapters.shuttel'`

- [ ] **Step 4: Write the Shuttel module**

Create `src/adapters/shuttel.py`:

```python
"""Shuttel: Keycloak-issued tokens plus a REST API. No browser involved.

The portal itself is a Flutter web app that renders to canvas, so it has no
stable DOM and the AFAS approach -- Playwright plus role selectors -- cannot be
reused here. Fortunately it does not need to be: the app talks to a REST API
behind Keycloak, and so can we.

Everything below was read from the portal's own *unauthenticated* discovery
endpoints on 2026-09-08:

    GET /api/v1/authinfo/mijn.shuttel.nl.n?client=web
    GET /auth/realms/shuttel/.well-known/openid-configuration

These are vendor-level facts, identical for every Shuttel customer, so they are
not employer-specific and belong in source. Anything account-specific (saved
trip identifiers, for instance) is configuration and must never be committed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

import httpx

from .base import Entry, FileOutcome, FileResult

SHUTTEL_BASE = "https://mijn.shuttel.nl"
SHUTTEL_REALM = "shuttel"
SHUTTEL_CLIENT_ID = "shuttel-portal"
SHUTTEL_SCOPE = "openid offline_access shuttel_portal_api_user"

_TOKEN_PATH = f"/auth/realms/{SHUTTEL_REALM}/protocol/openid-connect/token"

#: Refresh this many seconds before the token actually expires, so a slow
#: request cannot land after expiry.
_EXPIRY_MARGIN_S = 30


class ShuttelAuthError(RuntimeError):
    """Authentication failed. Never carries a credential value."""


class ShuttelNotImplementedError(NotImplementedError):
    """The Shuttel API surface has not been mapped yet."""


@dataclass(frozen=True)
class ShuttelCredentials:
    username: str = ""
    password: str = ""

    def __repr__(self) -> str:
        return (
            f"ShuttelCredentials(username={'set' if self.username else 'unset'}, "
            f"password={'set' if self.password else 'unset'})"
        )

    __str__ = __repr__

    @property
    def complete(self) -> bool:
        return bool(self.username and self.password)


class TokenClient:
    """Holds a Keycloak access token, refreshing it as needed."""

    def __init__(
        self,
        credentials: ShuttelCredentials,
        base_url: str = SHUTTEL_BASE,
        transport: httpx.BaseTransport | None = None,
    ):
        self._creds = credentials
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url, transport=transport, timeout=30.0
        )
        self._access: str = ""
        self._refresh: str = ""
        self._expires_at: float = 0.0

    def access_token(self) -> str:
        if self._access and time.time() < self._expires_at - _EXPIRY_MARGIN_S:
            return self._access
        if self._refresh and self._try_refresh():
            return self._access
        return self._password_grant()

    def _store(self, payload: dict) -> str:
        self._access = payload.get("access_token", "")
        self._refresh = payload.get("refresh_token", "") or self._refresh
        self._expires_at = time.time() + float(payload.get("expires_in", 0))
        return self._access

    def _try_refresh(self) -> bool:
        try:
            resp = self._client.post(_TOKEN_PATH, data={
                "grant_type": "refresh_token",
                "client_id": SHUTTEL_CLIENT_ID,
                "refresh_token": self._refresh,
            })
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            self._refresh = ""
            return False
        self._store(resp.json())
        return True

    def _password_grant(self) -> str:
        if not self._creds.complete:
            raise ShuttelAuthError(
                "Shuttel credentials are not configured. Set SHUTTEL_USERNAME "
                "and SHUTTEL_PASSWORD in .env."
            )
        try:
            resp = self._client.post(_TOKEN_PATH, data={
                "grant_type": "password",
                "client_id": SHUTTEL_CLIENT_ID,
                "scope": SHUTTEL_SCOPE,
                "username": self._creds.username,
                "password": self._creds.password,
            })
        except httpx.HTTPError as exc:
            raise ShuttelAuthError(f"Could not reach Shuttel: {exc}") from None

        if resp.status_code != 200:
            # Deliberately not including the request body, which holds the
            # password. Keycloak's own error code is enough to act on.
            code = ""
            try:
                code = resp.json().get("error", "")
            except Exception:
                pass
            raise ShuttelAuthError(
                f"Shuttel rejected the login (HTTP {resp.status_code} {code}). "
                "If the shuttel-portal client has Direct Access Grants "
                "disabled, this flow cannot work and authorization-code + PKCE "
                "is required instead."
            )
        return self._store(resp.json())


class ShuttelAdapter:
    """DayFiler for Shuttel. API surface not yet mapped -- see spec Q1.

    Deliberately raises rather than returning empty results: an adapter that
    quietly reported "nothing here" would make unfiled office days look filed.
    """

    system = "shuttel"

    def __init__(self, token_client: TokenClient):
        self._tokens = token_client

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        raise ShuttelNotImplementedError(
            "Shuttel read_month is not implemented: the commute-entry API has "
            "not been mapped yet (spec open question 1)."
        )

    def file(self, day: date) -> FileResult:
        return FileResult(
            day, self.system, FileOutcome.FAILED,
            "Shuttel adapter not implemented yet (spec open question 1).",
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_shuttel_auth.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
git add src/adapters/shuttel.py tests/test_shuttel_auth.py requirements.txt
git commit -m "feat(shuttel): Keycloak token client; API surface stubbed"
```

---

## Task 5: Sync engine

Where the safety rules actually live. Read this task's tests before its code —
they *are* the specification of the rules.

**Files:**
- Create: `src/planner/engine.py`
- Test: `tests/test_sync_engine.py`

**Interfaces:**
- Consumes: `PlanStore`, `diff`, `to_file`, `DayFiler`, `FileOutcome`, `FileResult`, `Intent`.
- Produces: `SyncEngine(store, filers: dict[str, DayFiler])` with `refresh_state(year, month) -> dict[str, str]`, `preview(year, month) -> list[Action]`, `sync(year, month) -> SyncReport`; and `SyncReport(run_id, outcome, results, stopped_reason)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sync_engine.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from src.adapters.base import Entry, FileOutcome, FileResult
from src.planner.engine import SyncEngine
from src.planner.model import Intent
from src.planner.store import PlanStore

D1, D2, D3 = date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)


class FakeFiler:
    def __init__(self, system: str, outcomes=None, month=None):
        self.system = system
        self._outcomes = outcomes or {}
        self._month = month if month is not None else {}
        self.filed: list[date] = []

    def read_month(self, year, month):
        return self._month

    def file(self, day):
        self.filed.append(day)
        outcome = self._outcomes.get(day, FileOutcome.FILED)
        return FileResult(day, self.system, outcome)


@pytest.fixture
def store(tmp_path):
    return PlanStore(tmp_path / "plan.db")


def engine(store, afas, shuttel):
    return SyncEngine(store, {"afas": afas, "shuttel": shuttel})


def plan_home(store, *days):
    for d in days:
        store.set_intent(d, Intent.HOME)


def test_refresh_then_sync_files_each_planned_home_day_in_date_order(store):
    afas, shuttel = FakeFiler("afas"), FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D3, D1, D2)
    eng.refresh_state(2026, 9)
    eng.sync(2026, 9)
    assert afas.filed == [D1, D2, D3]
    assert shuttel.filed == []


def test_nothing_is_filed_before_state_has_been_read(store):
    """The read-back guarantee, end to end: no refresh, no filing."""
    afas, shuttel = FakeFiler("afas"), FakeFiler("shuttel")
    plan_home(store, D1)
    report = engine(store, afas, shuttel).sync(2026, 9)
    assert afas.filed == []
    assert report.outcome == "nothing_to_do"


def test_a_day_already_present_is_not_filed_again(store):
    afas = FakeFiler("afas", month={D1: Entry(D1, "Thuiswerkdag")})
    shuttel = FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D1, D2)
    eng.refresh_state(2026, 9)
    eng.sync(2026, 9)
    assert afas.filed == [D2]


def test_unverified_stops_the_entire_run_including_the_other_system(store):
    """An unknown outcome is not information. Nothing else may be written
    anywhere until a human has looked."""
    afas = FakeFiler("afas", outcomes={D1: FileOutcome.UNVERIFIED})
    shuttel = FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D1, D2)
    store.set_intent(D3, Intent.OFFICE)
    eng.refresh_state(2026, 9)
    report = eng.sync(2026, 9)
    assert afas.filed == [D1]
    assert shuttel.filed == []
    assert report.outcome == "stopped_unverified"


def test_a_definite_failure_blocks_only_its_own_system(store):
    """AFAS DOM drift should not cost eleven screenshots, and must not stop
    Shuttel filing office days."""
    afas = FakeFiler("afas", outcomes={D1: FileOutcome.FAILED})
    shuttel = FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D1, D2)
    store.set_intent(D3, Intent.OFFICE)
    eng.refresh_state(2026, 9)
    report = eng.sync(2026, 9)
    assert afas.filed == [D1]
    assert shuttel.filed == [D3]
    assert report.outcome == "partial"


def test_a_refusal_is_isolated_like_a_failure_not_escalated_like_unverified(store):
    afas = FakeFiler("afas", outcomes={D1: FileOutcome.REFUSED})
    shuttel = FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D1, D2)
    store.set_intent(D3, Intent.OFFICE)
    eng.refresh_state(2026, 9)
    eng.sync(2026, 9)
    assert shuttel.filed == [D3]


def test_a_filed_day_updates_the_state_cache_so_a_rerun_is_a_no_op(store):
    afas, shuttel = FakeFiler("afas"), FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D1)
    eng.refresh_state(2026, 9)
    eng.sync(2026, 9)
    eng.sync(2026, 9)
    assert afas.filed == [D1]


def test_a_reader_that_raises_leaves_that_system_unread_not_empty(store):
    """The stubbed Shuttel adapter raises. That must not be recorded as
    'Shuttel holds nothing', which would let office days be filed blind."""
    class Raising(FakeFiler):
        def read_month(self, year, month):
            raise NotImplementedError("not mapped yet")

    afas, shuttel = FakeFiler("afas"), Raising("shuttel")
    eng = engine(store, afas, shuttel)
    store.set_intent(D1, Intent.OFFICE)
    status = eng.refresh_state(2026, 9)
    eng.sync(2026, 9)
    assert shuttel.filed == []
    assert "not mapped yet" in status["shuttel"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.planner.engine'`

- [ ] **Step 3: Write the engine**

Create `src/planner/engine.py`:

```python
"""Executes a diff against the real systems, under the safety rules.

The rules, in one place because they are the whole point:

  1. Never file against unread state -- enforced upstream in diff().
  2. Never retry an uncertain submission.
  3. An UNVERIFIED outcome stops the entire run, both systems included.
  4. A definite failure (FAILED / REFUSED) blocks only its own system.
  5. Execute sequentially. Both systems are stateful sessions.
"""

from __future__ import annotations

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
                self._store.set_state(day, system, entry is not None,
                                      entry.summary if entry else "")
            status[system] = f"read {len(entries)} entr(y/ies)"
        return status

    def preview(self, year: int, month: int) -> list[Action]:
        return diff(self._store.get_plan(year, month),
                    self._store.get_state(year, month))

    def sync(self, year: int, month: int) -> SyncReport:
        actions = to_file(self.preview(year, month))
        if not actions:
            run_id = self._store.start_run()
            self._store.finish_run(run_id, "nothing_to_do")
            return SyncReport(run_id, "nothing_to_do")

        run_id = self._store.start_run()
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
                self._store.set_state(action.day, system, True, result.message)
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
    import calendar

    return [date(year, month, d)
            for d in range(1, calendar.monthrange(year, month)[1] + 1)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sync_engine.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/planner/engine.py tests/test_sync_engine.py
git commit -m "feat(planner): sync engine enforcing the stop/isolate rules"
```

---

## Task 6: HTTP API

**Files:**
- Create: `web/__init__.py` (empty)
- Create: `web/app.py`
- Modify: `requirements.txt`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `PlanStore`, `SyncEngine`, `Intent`.
- Produces: `create_app(store, engine) -> FastAPI`. (The container entrypoint `build_default_app()` is Task 8's, in `web/main.py`.)

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
fastapi>=0.115      # local dashboard
uvicorn>=0.30       # ASGI server for the dashboard
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_web_api.py`:

```python
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.adapters.base import FileOutcome, FileResult
from src.planner.engine import SyncEngine
from src.planner.store import PlanStore
from web.app import create_app

D1 = date(2026, 9, 8)


class FakeFiler:
    def __init__(self, system):
        self.system = system
        self.filed = []

    def read_month(self, year, month):
        return {}

    def file(self, day):
        self.filed.append(day)
        return FileResult(day, self.system, FileOutcome.FILED)


@pytest.fixture
def client(tmp_path):
    store = PlanStore(tmp_path / "plan.db")
    filers = {"afas": FakeFiler("afas"), "shuttel": FakeFiler("shuttel")}
    app = create_app(store, SyncEngine(store, filers))
    app.state.filers = filers
    return TestClient(app)


def test_month_view_returns_a_cell_for_every_day(client):
    body = client.get("/api/month/2026/9").json()
    assert len(body["days"]) == 30
    assert body["days"][0]["date"] == "2026-09-01"


def test_setting_an_intent_persists_it(client):
    assert client.put("/api/day/2026-09-08", json={"intent": "home"}).status_code == 200
    days = {d["date"]: d for d in client.get("/api/month/2026/9").json()["days"]}
    assert days["2026-09-08"]["intent"] == "home"


def test_an_unknown_intent_is_rejected(client):
    assert client.put("/api/day/2026-09-08", json={"intent": "holiday"}).status_code == 422


def test_a_malformed_date_is_rejected(client):
    assert client.put("/api/day/not-a-date", json={"intent": "home"}).status_code == 422


def test_preview_reports_unknown_before_a_refresh_and_file_after(client):
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    before = client.get("/api/month/2026/9").json()["actions"]
    assert [a["kind"] for a in before] == ["unknown"]

    client.post("/api/refresh/2026/9")
    after = client.get("/api/month/2026/9").json()["actions"]
    assert [a["kind"] for a in after] == ["file"]


def test_sync_is_a_post_and_reports_its_outcome(client):
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    client.post("/api/refresh/2026/9")
    body = client.post("/api/sync/2026/9").json()
    assert body["outcome"] == "done"
    assert client.app.state.filers["afas"].filed == [D1]


def test_sync_refuses_to_run_from_a_get(client):
    assert client.get("/api/sync/2026/9").status_code == 405


def test_the_dashboard_page_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_web_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web'`

- [ ] **Step 4: Write the app**

Create `web/__init__.py` as an empty file, then `web/app.py`:

```python
"""FastAPI dashboard. Loopback only -- see build_default_app and compose.

Routes are deliberately coarse: a month view that returns plan, state and diff
together, and two POSTs that do the slow work. The calendar is not worth a
per-cell round trip.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from src.planner.engine import SyncEngine
from src.planner.model import Intent
from src.planner.store import PlanStore

STATIC_DIR = Path(__file__).resolve().parent / "static"


class IntentBody(BaseModel):
    intent: str

    @field_validator("intent")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in {i.value for i in Intent}:
            raise ValueError(f"unknown intent {v!r}")
        return v


def _parse_day(iso: str) -> date:
    try:
        return date.fromisoformat(iso)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"not a date: {iso!r}") from None


def create_app(store: PlanStore, engine: SyncEngine) -> FastAPI:
    app = FastAPI(title="Day planner")

    @app.get("/api/month/{year}/{month}")
    def month(year: int, month: int):
        plan = store.get_plan(year, month)
        state = store.get_state(year, month)
        days = []
        for d in range(1, calendar.monthrange(year, month)[1] + 1):
            day = date(year, month, d)
            observed = state.get(day)
            days.append({
                "date": day.isoformat(),
                "weekday": day.weekday(),
                "intent": plan.get(day, Intent.NONE).value,
                "afas": None if observed is None else observed.afas,
                "shuttel": None if observed is None else observed.shuttel,
            })
        return {
            "year": year,
            "month": month,
            "days": days,
            "actions": [
                {"date": a.day.isoformat(), "system": a.system,
                 "kind": a.kind.value, "reason": a.reason}
                for a in engine.preview(year, month)
            ],
            "read_at": {
                s: (t.isoformat() if (t := store.state_read_at(s)) else None)
                for s in ("afas", "shuttel")
            },
        }

    @app.put("/api/day/{iso}")
    def set_day(iso: str, body: IntentBody):
        store.set_intent(_parse_day(iso), Intent(body.intent))
        return {"ok": True}

    @app.post("/api/refresh/{year}/{month}")
    def refresh(year: int, month: int):
        return {"status": engine.refresh_state(year, month)}

    @app.post("/api/sync/{year}/{month}")
    def sync(year: int, month: int):
        report = engine.sync(year, month)
        return {
            "outcome": report.outcome,
            "stopped_reason": report.stopped_reason,
            "results": [
                {**r, "date": r["date"].isoformat()} for r in report.results
            ],
        }

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_api.py -q`
Expected: 7 pass, 1 fail — `test_the_dashboard_page_is_served`, because `web/static/index.html` does not exist yet. Task 7 creates it.

- [ ] **Step 6: Commit**

```bash
git add web/__init__.py web/app.py tests/test_web_api.py requirements.txt
git commit -m "feat(web): month, refresh and sync API"
```

---

## Task 7: Calendar UI

**Files:**
- Create: `web/static/index.html`
- Test: `tests/test_web_api.py` (the page-served test from Task 6 now passes)

**Interfaces:**
- Consumes: the JSON API from Task 6.
- Produces: nothing other tasks import.

- [ ] **Step 1: Write the page**

Create `web/static/index.html`. No build step, no framework, no CDN:

```html
<!doctype html>
<meta charset="utf-8">
<title>Day planner</title>
<style>
  :root { color-scheme: light dark; --line: #8883; }
  body { font: 14px system-ui, sans-serif; margin: 0; padding: 1.5rem; }
  h1 { font-size: 1.1rem; margin: 0 0 1rem; }
  .bar { display: flex; gap: .5rem; align-items: center; margin-bottom: 1rem; }
  .grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
  .dow { text-align: center; opacity: .6; font-size: .8rem; padding: .25rem; }
  .cell { border: 1px solid var(--line); border-radius: 6px; padding: .5rem;
          min-height: 3.5rem; cursor: pointer; user-select: none; }
  .cell.weekend { opacity: .45; }
  .cell.home { background: #7c3aed22; border-color: #7c3aed; }
  .cell.office { background: #2563eb22; border-color: #2563eb; }
  .num { font-weight: 600; }
  .tags { font-size: .7rem; opacity: .75; margin-top: .25rem; }
  .status { margin-top: 1rem; white-space: pre-wrap; font-family: ui-monospace, monospace;
            font-size: .8rem; }
  button { font: inherit; padding: .4rem .8rem; border-radius: 6px;
           border: 1px solid var(--line); background: transparent; cursor: pointer; }
</style>

<h1>Day planner</h1>
<div class="bar">
  <button id="prev">&lt;</button>
  <strong id="label"></strong>
  <button id="next">&gt;</button>
  <span style="flex:1"></span>
  <button id="refresh">Refresh state</button>
  <button id="sync">Sync</button>
</div>
<div class="grid" id="dow"></div>
<div class="grid" id="grid"></div>
<div class="status" id="status"></div>

<script>
const CYCLE = { none: "home", home: "office", office: "none" };
let year, month;

const $ = (id) => document.getElementById(id);
const setStatus = (t) => { $("status").textContent = t; };

function init() {
  const now = new Date();
  year = now.getFullYear();
  month = now.getMonth() + 1;
  $("dow").innerHTML = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    .map((d) => `<div class="dow">${d}</div>`).join("");
  $("prev").onclick = () => { if (--month < 1) { month = 12; year--; } load(); };
  $("next").onclick = () => { if (++month > 12) { month = 1; year++; } load(); };
  $("refresh").onclick = () => post(`/api/refresh/${year}/${month}`, "Refreshing");
  $("sync").onclick = () => {
    if (confirm("File every planned day that is not already present?")) {
      post(`/api/sync/${year}/${month}`, "Syncing");
    }
  };
  load();
}

async function load() {
  const data = await (await fetch(`/api/month/${year}/${month}`)).json();
  $("label").textContent =
    new Date(year, month - 1, 1).toLocaleString(undefined,
      { month: "long", year: "numeric" });

  const lead = (new Date(year, month - 1, 1).getDay() + 6) % 7;
  const cells = Array.from({ length: lead }, () => "<div></div>");

  for (const day of data.days) {
    const tags = [];
    if (day.afas) tags.push("AFAS");
    if (day.shuttel) tags.push("Shuttel");
    cells.push(
      `<div class="cell ${day.intent} ${day.weekday > 4 ? "weekend" : ""}"
            data-date="${day.date}">
         <div class="num">${Number(day.date.slice(-2))}</div>
         <div class="tags">${tags.join(" &middot; ")}</div>
       </div>`
    );
  }
  $("grid").innerHTML = cells.join("");
  for (const el of document.querySelectorAll(".cell")) el.onclick = () => cycle(el);

  const counts = {};
  for (const a of data.actions) counts[a.kind] = (counts[a.kind] || 0) + 1;
  const read = Object.entries(data.read_at)
    .map(([s, t]) => `${s}: ${t ? new Date(t).toLocaleTimeString() : "never read"}`)
    .join("   ");
  setStatus(`state as of  ${read}\n` +
            `pending: ${JSON.stringify(counts)}`);
}

async function cycle(el) {
  const next = CYCLE[[...el.classList].find((c) => c in CYCLE) || "none"];
  await fetch(`/api/day/${el.dataset.date}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ intent: next }),
  });
  load();
}

async function post(url, verb) {
  setStatus(`${verb}...`);
  const body = await (await fetch(url, { method: "POST" })).json();
  setStatus(JSON.stringify(body, null, 2));
  load();
}

init();
</script>
```

- [ ] **Step 2: Run the page test to verify it now passes**

Run: `python -m pytest tests/test_web_api.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 3: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS — 142 existing + 12 + 9 + 7 + 8 + 8 + 8 = 194 tests.

- [ ] **Step 4: Commit**

```bash
git add web/static/index.html
git commit -m "feat(web): month calendar UI"
```

---

## Task 8: Wire it up and run it locally

**Files:**
- Create: `web/main.py`
- Create: `docker-compose.yml`
- Modify: `Dockerfile`
- Test: `tests/test_bind_address.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `web.main:app`, and a `docker compose up` that serves the dashboard on `127.0.0.1:8765`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bind_address.py`. A regression guard, not a formality — a
container makes `0.0.0.0` the accidental default, and this app files financial
declarations:

```python
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_compose_publishes_on_loopback_only():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "127.0.0.1:8765:8765" in compose
    assert "\n      - 8765:8765" not in compose


def test_main_defaults_to_loopback():
    main = (ROOT / "web" / "main.py").read_text()
    assert '"127.0.0.1"' in main
    assert "0.0.0.0" not in main
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bind_address.py -q`
Expected: FAIL — `FileNotFoundError: docker-compose.yml`

- [ ] **Step 3: Write the entrypoint**

Create `web/main.py`:

```python
"""Container entrypoint for the dashboard.

Binds loopback only. This app can file financial declarations; it has no
authentication because it is not reachable from anywhere that would need it,
and that assumption must stay true.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.adapters.shuttel import ShuttelAdapter, ShuttelCredentials, TokenClient
from src.config import PROJECT_ROOT, load_config
from src.planner.engine import SyncEngine
from src.planner.store import PlanStore
from web.app import create_app


def build_default_app():
    cfg = load_config()
    store = PlanStore(Path(os.environ.get("PLANNER_DB", PROJECT_ROOT / "data" / "plan.db")))

    shuttel = ShuttelAdapter(TokenClient(ShuttelCredentials(
        username=os.environ.get("SHUTTEL_USERNAME", "").strip(),
        password=os.environ.get("SHUTTEL_PASSWORD", "").strip(),
    )))

    # AFAS costs a browser, so it is built per request inside the adapter
    # factory rather than held open for the life of the process.
    from src.adapters.afas_lazy import LazyAfasAdapter

    return create_app(store, SyncEngine(store, {
        "afas": LazyAfasAdapter(cfg),
        "shuttel": shuttel,
    }))


app = build_default_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
```

- [ ] **Step 4: Write the lazy AFAS adapter**

A Playwright browser cannot be held open for the process lifetime — the AFAS
session expires and the profile is single-writer. Create
`src/adapters/afas_lazy.py`:

```python
"""Opens a browser only for the duration of one AFAS operation.

Holding a Chromium context open for the life of the web process would mean an
expired AFAS session, a locked browser profile, and a 2 GB resident browser
sitting idle between clicks.
"""

from __future__ import annotations

from datetime import date

from ..afas import AfasInSite
from ..browser import session
from ..config import Config
from .afas_adapter import AfasAdapter
from .base import Entry, FileResult


class LazyAfasAdapter:
    system = "afas"

    def __init__(self, cfg: Config):
        self._cfg = cfg.with_overrides(headless=True)

    def _with_afas(self, fn):
        with session(self._cfg) as sess:
            sess.login_if_needed()
            return fn(AfasAdapter(AfasInSite(sess, self._cfg),
                                  self._cfg.thuiswerkdag_labels))

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        return self._with_afas(lambda a: a.read_month(year, month))

    def file(self, day: date) -> FileResult:
        return self._with_afas(lambda a: a.file(day))
```

- [ ] **Step 5: Write the compose file**

Create `docker-compose.yml`:

```yaml
services:
  planner:
    build:
      context: .
    # Loopback only. This app files financial declarations and has no auth.
    ports:
      - "127.0.0.1:8765:8765"
    command: ["python", "-m", "uvicorn", "web.main:app", "--host", "127.0.0.1", "--port", "8765"]
    environment:
      TZ: Europe/Amsterdam
    shm_size: 1g                 # Chromium dies on the default 64 MB
    userns_mode: "keep-id:uid=1001,gid=1001"
    volumes:
      - ./.env:/app/.env:ro
      - ./.browser-profile-docker:/app/.browser-profile
      - ./artifacts:/app/artifacts
      - ./data:/app/data
```

- [ ] **Step 6: Update the Dockerfile**

The image must ship `web/` and expose the port. Add after the existing
`COPY pytest.ini ./` line:

```dockerfile
COPY web/ ./web/
```

And add before `USER pwuser`, extending the existing `mkdir` line to include
the database directory:

```dockerfile
RUN mkdir -p /app/.browser-profile /app/artifacts /app/data && chown -R pwuser:pwuser /app
```

(Replace the existing `mkdir -p /app/.browser-profile /app/artifacts ...` line
rather than adding a second one.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_bind_address.py -q`
Expected: PASS, 2 tests.

Run the whole suite: `python -m pytest tests/ -q`
Expected: PASS, 196 tests.

- [ ] **Step 8: Build and start it**

```bash
docker compose build
docker compose up -d
```

Then confirm it is serving, and confirm it is **not** reachable off-host:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/
```
Expected: `200`

```bash
curl -s -m 3 -o /dev/null -w '%{http_code}\n' "http://$(hostname -I | awk '{print $1}'):8765/" || echo "refused - correct"
```
Expected: `refused - correct`

- [ ] **Step 9: Commit**

```bash
git add web/main.py src/adapters/afas_lazy.py docker-compose.yml Dockerfile tests/test_bind_address.py
git commit -m "feat: local dashboard stack on loopback"
```

---

## Done means

- `python -m pytest tests/ -q` is green, 196 tests, and the original 142 are untouched.
- `docker compose up` serves a month calendar on `127.0.0.1:8765` and nothing answers on the LAN address.
- Clicking days cycles none -> home -> office; **Refresh state** reads AFAS back; **Sync** files only home days that AFAS does not already have.
- Office days show as `unknown` and are never filed, because the Shuttel adapter is honestly stubbed.

## Deliberately not done

- **The Shuttel API surface** (spec open question 1). `ShuttelAdapter.read_month` raises and `file` returns FAILED, so office days surface as unknown rather than quietly appearing filed. Needs its own plan once an authenticated session can be inspected.
- **Deletion / amendment.** Conflicts are shown, never corrected.
- **Kubernetes.** The container facts in the spec's Deferred section carry over when that happens.
