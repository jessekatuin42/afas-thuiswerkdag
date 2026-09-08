from __future__ import annotations

from datetime import date

import pytest

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


def test_read_month_carries_the_amount_afas_reports(monkeypatch):
    """The 2 euro is read from the grid, not hardcoded: if the rate changes,
    the counter should follow rather than quietly stay wrong."""
    import src.adapters.afas_adapter as mod

    monkeypatch.setattr(mod, "summarize_thuiswerkdagen",
                        lambda rows, labels: [decl(D1)])
    entries = AfasAdapter(FakeAfas(rows=[object()]), LABELS).read_month(2026, 9)
    assert entries[D1].amount == 2.0


@pytest.mark.parametrize("raw, expected", [
    ("2,00", 2.0),            # Dutch decimal comma, as AFAS renders it
    ("2.00", 2.0),
    ("€ 2,00", 2.0),
    ("1.234,56", 1234.56),    # Dutch thousands separator
    ("", None),
    ("n/a", None),
])
def test_amount_parsing(raw, expected):
    from src.adapters.afas_adapter import parse_amount

    assert parse_amount(raw) == expected
