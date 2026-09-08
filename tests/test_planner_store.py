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


def test_amount_and_km_round_trip(store):
    store.set_state(D1, "shuttel", True, "2 journeys", amount=111.0, km=444.0)
    rows = store.get_observations(date(2026, 9, 1), date(2026, 9, 30))
    assert rows[(D1, "shuttel")].amount == 111.0
    assert rows[(D1, "shuttel")].km == 444.0
    assert rows[(D1, "shuttel")].present is True


def test_observations_span_a_range_not_a_month(store):
    """The pay period runs 25th to 24th, so it straddles two calendar months."""
    store.set_state(date(2026, 8, 26), "afas", True, "", amount=2.0)
    store.set_state(date(2026, 9, 2), "afas", True, "", amount=2.0)
    store.set_state(date(2026, 8, 20), "afas", True, "", amount=2.0)   # before
    rows = store.get_observations(date(2026, 8, 25), date(2026, 9, 24))
    assert sorted(d.isoformat() for d, _ in rows) == ["2026-08-26", "2026-09-02"]


def test_the_range_is_inclusive_at_both_ends(store):
    store.set_state(date(2026, 8, 25), "afas", True, "", amount=2.0)
    store.set_state(date(2026, 9, 24), "afas", True, "", amount=2.0)
    rows = store.get_observations(date(2026, 8, 25), date(2026, 9, 24))
    assert len(rows) == 2


def test_amount_and_km_default_to_none_when_not_supplied(store):
    store.set_state(D1, "afas", True, "")
    rows = store.get_observations(date(2026, 9, 1), date(2026, 9, 30))
    assert rows[(D1, "afas")].amount is None


def test_a_database_predating_the_money_columns_is_migrated(tmp_path):
    """Existing installs already have a state_cache. CREATE TABLE IF NOT EXISTS
    would leave it without the new columns and every read would fail."""
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE state_cache (date TEXT NOT NULL, system TEXT NOT NULL,"
        " present INTEGER NOT NULL, summary TEXT NOT NULL DEFAULT '',"
        " read_at TEXT NOT NULL, PRIMARY KEY (date, system));"
        "INSERT INTO state_cache VALUES ('2026-09-08','afas',1,'x','2026-09-08T00:00:00');"
    )
    con.commit()
    con.close()

    migrated = PlanStore(path)
    rows = migrated.get_observations(date(2026, 9, 1), date(2026, 9, 30))
    assert rows[(date(2026, 9, 8), "afas")].present is True
    assert rows[(date(2026, 9, 8), "afas")].amount is None
    migrated.set_state(date(2026, 9, 8), "afas", True, "x", amount=2.0)
    assert migrated.get_observations(date(2026, 9, 1), date(2026, 9, 30))[
        (date(2026, 9, 8), "afas")].amount == 2.0


def test_marking_present_does_not_erase_the_money_already_read(store):
    """Filing tells us a day exists; it does not tell us what it is worth.
    Writing NULL over an amount the last Check established would silently
    shrink the counter."""
    store.set_state(D1, "shuttel", True, "2 journeys", amount=111.0, km=444.0)
    store.mark_present(D1, "shuttel", "filed")
    row = store.get_observations(D1, D1)[(D1, "shuttel")]
    assert row.present is True
    assert row.amount == 111.0
    assert row.km == 444.0


def test_marking_present_creates_a_row_when_there_is_none(store):
    store.mark_present(D2, "afas", "filed")
    row = store.get_observations(D2, D2)[(D2, "afas")]
    assert row.present is True
    assert row.amount is None
