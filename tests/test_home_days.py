from __future__ import annotations

import pytest

from src.planner.model import (
    DEFAULT_HOME_DAYS,
    InvalidWeekdaysError,
    parse_home_days,
)


def test_the_default_is_tuesday_wednesday_thursday():
    """Tue/Wed/Thu are the default home days. Inherited from the unattended
    timer this tool used to ship with, and kept because existing installs
    already set AFAS_DAYS and the question it answers has not changed."""
    assert DEFAULT_HOME_DAYS == (2, 3, 4)
    assert parse_home_days(None) == (2, 3, 4)


def test_an_empty_value_falls_back_to_the_default():
    """Unset and empty mean the same thing: use the default."""
    assert parse_home_days("") == (2, 3, 4)
    assert parse_home_days("   ") == (2, 3, 4)


def test_an_explicit_list_is_honoured():
    assert parse_home_days("1,2,3,4,5") == (1, 2, 3, 4, 5)


def test_values_are_deduplicated_and_ordered():
    assert parse_home_days("4,2,2,3") == (2, 3, 4)


def test_surrounding_whitespace_is_tolerated():
    assert parse_home_days(" 2 , 3 ,4 ") == (2, 3, 4)


@pytest.mark.parametrize("raw", ["abc", "2,x", "0", "8", "-1", "2.5"])
def test_a_malformed_list_refuses_rather_than_guessing(raw):
    """Quietly falling back to the default would mark days the user never
    chose, which is the one thing a planner must not do."""
    with pytest.raises(InvalidWeekdaysError):
        parse_home_days(raw)
