"""Pure duplicate-detection logic.

Kept free of Playwright so it can be tested exhaustively without a browser.
The browser layer's only job is to hand this module the *cells* of each grid
row plus the column headers; deciding what counts as "a Thuiswerkdag on date X"
happens here.

Two independent conditions must both hold for a row to be a match:

1. the row's **Soort declaratie** is a Thuiswerkdag, and
2. the row's **Datum** column is the target date.

Both parts matter, and the second is subtler than it looks. The real AFAS grid
has *two* date columns:

    Datum boeking │ Datum      │ Status      │ Soort declaratie │ … │ Totaalbedrag
    11-02-2026    │ 05-02-2026 │ Afgehandeld │ Thuiswerkdag     │ … │ 2,00

'Datum boeking' is when the declaration was entered; 'Datum' is the day being
declared. Matching a date anywhere in the row would report "already exists" for
11-02-2026 on the row above, whose actual thuiswerk day is 05-02-2026 — and the
tool would then silently skip a declaration that is genuinely needed. So the
date is read from the 'Datum' column specifically.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from .models import Declaration

# Column headers we care about, by normalized name.
COL_DATUM = "datum"
COL_DATUM_BOEKING = "datum boeking"
COL_SOORT = "soort declaratie"
COL_OMSCHRIJVING = "omschrijving"
COL_STATUS = "status"
COL_BEDRAG = "totaalbedrag"


def normalize(text: str) -> str:
    """Casefold + strip accents + collapse whitespace, for robust matching."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def _clean_header(text: str) -> str:
    """Normalize a column header.

    AFAS appends sort/filter affordances to header text (e.g. the 'Datum
    boeking' header reads 'Datum boeking 1 31'), so trailing digits are dropped.
    """
    norm = normalize(text)
    return re.sub(r"[\s\d]+$", "", norm).strip()


def resolve_columns(headers: list[str]) -> dict[str, int]:
    """Map normalized header name -> column index.

    Resolving by *name* rather than by position means the tool keeps working if
    AFAS reorders or inserts columns.
    """
    columns: dict[str, int] = {}
    for index, raw in enumerate(headers):
        name = _clean_header(raw)
        if name and name not in columns:
            columns[name] = index
    return columns


@dataclass(frozen=True)
class GridRow:
    """One grid row: its cell texts, interpreted through the column map."""

    cells: list[str] = field(default_factory=list)
    columns: dict[str, int] = field(default_factory=dict)

    def cell(self, column: str) -> str:
        index = self.columns.get(column)
        if index is None or index >= len(self.cells):
            return ""
        return (self.cells[index] or "").strip()

    @property
    def raw_text(self) -> str:
        return " | ".join(c.strip() for c in self.cells if c and c.strip())

    def date_for(self, column: str) -> date | None:
        """Parse a single unambiguous date out of one column."""
        from . import dates

        found = dates.extract_dates(self.cell(column))
        return next(iter(found)) if len(found) == 1 else None

    @property
    def declared_date(self) -> date | None:
        """The day being declared — 'Datum', never 'Datum boeking'."""
        return self.date_for(COL_DATUM)

    @property
    def soort(self) -> str:
        return self.cell(COL_SOORT) or self.cell(COL_OMSCHRIJVING)

    def to_declaration(self) -> Declaration:
        return Declaration(
            date=self.declared_date,
            description=self.soort or "(unknown)",
            raw_text=self.raw_text[:300],
            amount=self.cell(COL_BEDRAG),
            status=self.cell(COL_STATUS),
        )


def is_thuiswerkdag_row(row: GridRow, labels: tuple[str, ...]) -> bool:
    """Type check against the declaration-type columns only.

    Scoped to 'Soort declaratie' / 'Omschrijving' so that an unrelated row
    merely *mentioning* the word elsewhere cannot pass.
    """
    haystack = normalize(f"{row.cell(COL_SOORT)} {row.cell(COL_OMSCHRIJVING)}")
    return any(normalize(label) in haystack for label in labels)


def find_thuiswerkdag(
    rows: list[GridRow],
    target: date,
    labels: tuple[str, ...],
) -> Declaration | None:
    """First row that is a Thuiswerkdag *for* ``target``, else None."""
    for row in rows:
        if not is_thuiswerkdag_row(row, labels):
            continue
        if row.declared_date != target:
            continue
        return row.to_declaration()
    return None


def summarize_thuiswerkdagen(
    rows: list[GridRow], labels: tuple[str, ...]
) -> list[Declaration]:
    """Every Thuiswerkdag row, for logging and post-creation checks."""
    return [r.to_declaration() for r in rows if is_thuiswerkdag_row(r, labels)]


# ---------------------------------------------------------------------------
# Unstructured fallback
# ---------------------------------------------------------------------------

def is_thuiswerkdag_text(text: str, labels: tuple[str, ...]) -> bool:
    haystack = normalize(text)
    return any(normalize(label) in haystack for label in labels)


def find_thuiswerkdag_in_texts(
    row_texts: list[str],
    target: date,
    labels: tuple[str, ...],
) -> Declaration | None:
    """Last-resort matcher for when column headers cannot be resolved.

    Deliberately weaker than the column-aware path: with no way to tell 'Datum'
    from 'Datum boeking', a row is only accepted when it contains exactly one
    distinct date and that date is the target. Ambiguous rows are skipped rather
    than guessed at, because a false positive here silently suppresses a
    declaration you actually need.
    """
    from . import dates

    for text in row_texts:
        if not text or not text.strip():
            continue
        if not is_thuiswerkdag_text(text, labels):
            continue
        found = dates.extract_dates(text)
        if found == {target}:
            amount = _AMOUNT_RE.search(text)
            status = _STATUS_RE.search(text)
            return Declaration(
                date=target,
                description="Thuiswerkdag",
                raw_text=re.sub(r"\s+", " ", text).strip()[:300],
                amount=amount.group(0).strip() if amount else "",
                status=status.group(0).strip() if status else "",
            )
    return None


_AMOUNT_RE = re.compile(r"€\s?-?\d[\d.,]*")
_STATUS_RE = re.compile(
    r"(ter goedkeuring|goedgekeurd|afgekeurd|in behandeling|verzonden|"
    r"concept|ingediend|akkoord|afgehandeld|verwerkt)",
    re.IGNORECASE,
)
