from __future__ import annotations

from datetime import date

import pytest

from src.planner.earnings import Observation, earnings, period_for
from src.planner.model import Intent


# ---- the 25th-to-24th period ---------------------------------------------

@pytest.mark.parametrize("day, start, end", [
    (date(2026, 9, 8),  date(2026, 8, 25), date(2026, 9, 24)),
    (date(2026, 9, 24), date(2026, 8, 25), date(2026, 9, 24)),   # last day
    (date(2026, 9, 25), date(2026, 9, 25), date(2026, 10, 24)),  # first day
    (date(2026, 1, 3),  date(2025, 12, 25), date(2026, 1, 24)),  # across new year
    (date(2026, 12, 26), date(2026, 12, 25), date(2027, 1, 24)), # into new year
    (date(2026, 3, 1),  date(2026, 2, 25), date(2026, 3, 24)),   # short February
])
def test_period_boundaries(day, start, end):
    assert period_for(day) == (start, end)


def test_the_period_is_inclusive_at_both_ends():
    start, end = period_for(date(2026, 9, 8))
    assert period_for(start) == (start, end)
    assert period_for(end) == (start, end)


# ---- earnings -------------------------------------------------------------

START, END = date(2026, 8, 25), date(2026, 9, 24)
HOME1, HOME2 = date(2026, 9, 1), date(2026, 9, 2)
OFFICE1, OFFICE2 = date(2026, 9, 4), date(2026, 9, 7)


def obs(present, amount=None, km=None):
    return Observation(present=present, amount=amount, km=km)


def test_filed_days_are_summed_per_system():
    result = earnings(
        observed={
            (HOME1, "afas"): obs(True, 2.0),
            (HOME2, "afas"): obs(True, 2.0),
            (OFFICE1, "shuttel"): obs(True, 111.0, 444.0),
        },
        plan={}, start=START, end=END,
    )
    assert result.eur == pytest.approx(115.0)
    assert result.km == pytest.approx(444.0)
    assert result.per_system["afas"].eur == pytest.approx(4.0)
    assert result.per_system["shuttel"].km == pytest.approx(444.0)


def test_days_outside_the_period_are_excluded():
    """The counter resets on the 25th; an August day before that belongs to the
    previous period and must not inflate this one."""
    result = earnings(
        observed={
            (date(2026, 8, 21), "shuttel"): obs(True, 111.0, 444.0),   # before
            (OFFICE1, "shuttel"): obs(True, 111.0, 444.0),             # inside
        },
        plan={}, start=START, end=END,
    )
    assert result.eur == pytest.approx(111.0)


def test_a_day_that_is_not_present_contributes_nothing():
    result = earnings(
        observed={(HOME1, "afas"): obs(False, None)},
        plan={}, start=START, end=END,
    )
    assert result.eur == 0.0
    assert result.per_system["afas"].filed_days == 0


def test_projection_adds_planned_days_at_the_observed_rate():
    """One office day is filed at 111. A second is planned. Projection should
    say 222 without anyone hardcoding a per-kilometre rate."""
    result = earnings(
        observed={(OFFICE1, "shuttel"): obs(True, 111.0, 444.0)},
        plan={OFFICE1: Intent.OFFICE, OFFICE2: Intent.OFFICE},
        start=START, end=END,
    )
    assert result.eur == pytest.approx(111.0)
    assert result.projected_eur == pytest.approx(222.0)
    assert result.per_system["shuttel"].planned_unfiled_days == 1


def test_projection_falls_back_to_a_known_default_when_nothing_is_filed_yet():
    """A fresh period has no observed rate. AFAS is a flat 2 euro, so that one
    is still projectable."""
    result = earnings(
        observed={}, plan={HOME1: Intent.HOME, HOME2: Intent.HOME},
        start=START, end=END, defaults={"afas": 2.0},
    )
    assert result.projected_eur == pytest.approx(4.0)
    assert result.estimated is True


def test_projection_is_flagged_when_a_system_cannot_be_estimated():
    """No filed Shuttel day and no default: the projection must admit it is
    incomplete rather than quietly reporting a low number as fact."""
    result = earnings(
        observed={}, plan={OFFICE1: Intent.OFFICE}, start=START, end=END,
    )
    assert result.projected_eur == pytest.approx(0.0)
    assert result.incomplete == ["shuttel"]


def test_an_exact_projection_is_not_flagged_as_estimated():
    result = earnings(
        observed={(HOME1, "afas"): obs(True, 2.0)},
        plan={HOME1: Intent.HOME}, start=START, end=END,
    )
    assert result.projected_eur == pytest.approx(2.0)
    assert result.estimated is False
    assert result.incomplete == []


def test_a_planned_day_already_filed_is_not_counted_twice():
    result = earnings(
        observed={(HOME1, "afas"): obs(True, 2.0)},
        plan={HOME1: Intent.HOME}, start=START, end=END, defaults={"afas": 2.0},
    )
    assert result.projected_eur == pytest.approx(2.0)


def test_days_with_an_unknown_amount_do_not_drag_the_average_to_zero():
    """State read before the money columns existed has present=True and
    amount=None. Averaging over those days makes every day look worthless and
    silently zeroes the projection."""
    result = earnings(
        observed={
            (HOME1, "afas"): obs(True, None),      # read before amounts existed
            (HOME2, "afas"): obs(True, 2.0),
        },
        plan={date(2026, 9, 3): Intent.HOME},
        start=START, end=END,
    )
    assert result.eur == pytest.approx(2.0)
    assert result.projected_eur == pytest.approx(4.0)   # 2 known + 1 at 2.00


def test_all_amounts_unknown_falls_back_to_the_default():
    result = earnings(
        observed={(HOME1, "afas"): obs(True, None)},
        plan={HOME2: Intent.HOME},
        start=START, end=END, defaults={"afas": 2.0},
    )
    assert result.projected_eur == pytest.approx(2.0)


def test_priced_days_are_counted_separately_from_filed_days():
    result = earnings(
        observed={(HOME1, "afas"): obs(True, None), (HOME2, "afas"): obs(True, 2.0)},
        plan={}, start=START, end=END,
    )
    assert result.per_system["afas"].filed_days == 2
    assert result.per_system["afas"].priced_days == 1
