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


def test_sync_uses_an_injected_run_id(store):
    """The API creates the run row first so it can hand the caller a run_id
    before the work starts; the engine must fill that row rather than open a
    second one."""
    afas, shuttel = FakeFiler("afas"), FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    plan_home(store, D1)
    eng.refresh_state(2026, 9)

    run_id = store.start_run()
    report = eng.sync(2026, 9, run_id=run_id)

    assert report.run_id == run_id
    assert [r["date"] for r in store.get_run_results(run_id)] == [D1]


def test_an_injected_run_id_is_still_closed_when_there_is_nothing_to_do(store):
    afas, shuttel = FakeFiler("afas"), FakeFiler("shuttel")
    eng = engine(store, afas, shuttel)
    run_id = store.start_run()
    report = eng.sync(2026, 9, run_id=run_id)
    assert report.run_id == run_id
    assert report.outcome == "nothing_to_do"
    assert store.get_run(run_id)["outcome"] == "nothing_to_do"


def test_results_are_readable_while_a_run_is_still_in_progress(store):
    """Progress polling reads day_result rows as they land, so the engine must
    write each day before starting the next rather than batching at the end."""
    seen: list[int] = []

    class Watching(FakeFiler):
        def file(self, day):
            seen.append(len(store.get_run_results(self.run_id)))
            return super().file(day)

    afas = Watching("afas")
    eng = engine(store, afas, FakeFiler("shuttel"))
    plan_home(store, D1, D2, D3)
    eng.refresh_state(2026, 9)
    run_id = store.start_run()
    afas.run_id = run_id
    eng.sync(2026, 9, run_id=run_id)

    # Nothing recorded before the first day, one before the second, two before
    # the third: results accumulate as work proceeds.
    assert seen == [0, 1, 2]
