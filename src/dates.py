"""Date parsing, formatting and matching.

The CLI speaks ISO (``YYYY-MM-DD``); AFAS displays Dutch (``DD-MM-YYYY``).
Everything in between is a real ``datetime.date`` — never string surgery.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

ISO_FORMAT = "%Y-%m-%d"
DUTCH_FORMAT = "%d-%m-%Y"

# Dutch month names as AFAS may render them in list views.
_NL_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dec": 12,
}


class InvalidDateError(ValueError):
    """Raised for input that is not a real calendar date."""


def parse_iso(value: str) -> date:
    """Parse ``YYYY-MM-DD`` strictly.

    Rejects nonsense like ``2026-99-99`` and near-misses like ``2026-02-30``.
    """
    if not isinstance(value, str):
        raise InvalidDateError(f"Expected a date string, got {type(value).__name__}")
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise InvalidDateError(
            f"Invalid date {value!r}: expected format YYYY-MM-DD (e.g. 2026-08-17)"
        )
    try:
        return datetime.strptime(text, ISO_FORMAT).date()
    except ValueError as exc:
        raise InvalidDateError(f"Invalid date {value!r}: {exc}") from exc


def to_iso(d: date) -> str:
    return d.strftime(ISO_FORMAT)


def to_dutch(d: date) -> str:
    """``17-08-2026`` — the format AFAS shows and its date inputs accept."""
    return d.strftime(DUTCH_FORMAT)


def today() -> date:
    return date.today()


def yesterday() -> date:
    return date.today() - timedelta(days=1)


def display_variants(d: date) -> list[str]:
    """Plausible textual renderings of ``d``, for scanning page text.

    Only used as a *candidate* filter; :func:`text_contains_date` is the
    authority, because it re-parses what it finds instead of trusting a
    substring hit.
    """
    return [
        d.strftime("%d-%m-%Y"),
        d.strftime("%d-%m-%y"),
        f"{d.day}-{d.month}-{d.year}",
        d.strftime("%d/%m/%Y"),
        f"{d.day}/{d.month}/{d.year}",
        d.strftime("%Y-%m-%d"),
        d.strftime("%d.%m.%Y"),
    ]


# Numeric d-m-y / d/m/y / d.m.y with 2- or 4-digit year.
_NUMERIC_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})\b")
# ISO y-m-d.
_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# "17 augustus 2026" / "17 aug 2026" / "17 aug. 2026".
_NAMED_RE = re.compile(
    r"\b(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})\b", re.IGNORECASE
)


def extract_dates(text: str) -> set[date]:
    """Pull every date-looking token out of ``text`` and return real dates.

    Tokens that do not form a valid calendar date are dropped, so ``31-11-2026``
    never produces a match.
    """
    found: set[date] = set()
    if not text:
        return found

    for day, month, year in _NUMERIC_RE.findall(text):
        y = int(year)
        if y < 100:
            y += 2000
        try:
            found.add(date(y, int(month), int(day)))
        except ValueError:
            continue

    for year, month, day in _ISO_RE.findall(text):
        try:
            found.add(date(int(year), int(month), int(day)))
        except ValueError:
            continue

    for day, month_name, year in _NAMED_RE.findall(text):
        month = _NL_MONTHS.get(month_name.lower().rstrip("."))
        if month is None:
            continue
        try:
            found.add(date(int(year), month, int(day)))
        except ValueError:
            continue

    return found


def text_contains_date(text: str, target: date) -> bool:
    """True only if ``text`` contains ``target`` as a genuine date.

    Distinguishes 17-08-2026 from 18-08-2026 and refuses to match on a bare
    substring, which is why detection cannot be fooled by e.g. an ID that
    happens to contain the digits.
    """
    return target in extract_dates(text)
