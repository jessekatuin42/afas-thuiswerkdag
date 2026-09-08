"""Adapts the existing, proven AfasInSite to the DayFiler protocol.

Adds no AFAS knowledge of its own. All grid reading, duplicate detection and
the two-step create flow stay in src/afas.py and src/detection.py, which are
not modified.
"""

from __future__ import annotations

import re
from datetime import date

from ..afas import AfasInSite, AfasRefusedError
from ..detection import summarize_thuiswerkdagen
from ..models import Outcome
from .base import Entry, FileOutcome, FileResult

def parse_amount(raw: str) -> float | None:
    """Read AFAS's rendered total, which uses Dutch conventions.

    "2,00" and "1.234,56" both appear, so the comma is the decimal separator
    and the dot groups thousands. Returns None for anything unparseable rather
    than guessing a number into a financial total.
    """
    if not raw:
        return None
    text = re.sub(r"[^\d.,-]", "", raw)
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


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
                found[d] = Entry(day=d, summary=declaration.summary(),
                                 amount=parse_amount(declaration.amount))
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
