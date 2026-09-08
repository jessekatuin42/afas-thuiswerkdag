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
