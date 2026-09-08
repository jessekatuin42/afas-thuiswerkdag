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
