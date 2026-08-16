"""Duplicate detection — the safety-critical part.

Rows below mirror the real AFAS grid captured from the portal:

    Datum boeking │ Datum │ Status │ Soort declaratie │ Omschrijving │
    Aantal te declareren │ Totaalbedrag │ Bijlage
"""

from datetime import date

import pytest

from src.config import THUISWERKDAG_LABELS as LABELS
from src.detection import (
    GridRow,
    find_thuiswerkdag,
    find_thuiswerkdag_in_texts,
    is_thuiswerkdag_row,
    normalize,
    resolve_columns,
    summarize_thuiswerkdagen,
)

TARGET = date(2026, 8, 17)

# Exactly as AFAS renders them — note the sort/filter digits on the first header.
HEADERS = [
    "Datum boeking 1 31",
    "Datum",
    "Status",
    "Soort declaratie",
    "Omschrijving",
    "Aantal te declareren",
    "Totaalbedrag",
    "Bijlage",
]
COLUMNS = resolve_columns(HEADERS)


def row(boeking="", datum="", status="", soort="", omschrijving="",
        aantal="", bedrag="", bijlage="") -> GridRow:
    return GridRow(
        cells=[boeking, datum, status, soort, omschrijving, aantal, bedrag, bijlage],
        columns=COLUMNS,
    )


class TestResolveColumns:
    def test_maps_headers_to_indexes(self):
        assert COLUMNS["datum boeking"] == 0
        assert COLUMNS["datum"] == 1
        assert COLUMNS["status"] == 2
        assert COLUMNS["soort declaratie"] == 3
        assert COLUMNS["totaalbedrag"] == 6

    def test_strips_sort_filter_digits_from_headers(self):
        assert "datum boeking" in resolve_columns(["Datum boeking 1 31"])

    def test_survives_reordered_columns(self):
        cols = resolve_columns(["Soort declaratie", "Datum", "Totaalbedrag"])
        assert cols == {"soort declaratie": 0, "datum": 1, "totaalbedrag": 2}

    def test_empty_headers(self):
        assert resolve_columns([]) == {}


class TestGridRow:
    def test_reads_the_datum_column_not_datum_boeking(self):
        r = row(boeking="11-02-2026", datum="05-02-2026", soort="Thuiswerkdag")
        assert r.declared_date == date(2026, 2, 5)

    def test_missing_datum_is_none(self):
        assert row(boeking="03-08-2026", soort="Studiekosten").declared_date is None

    def test_ambiguous_cell_is_none(self):
        """Two dates in one cell must not be guessed at."""
        assert row(datum="17-08-2026 18-08-2026", soort="Thuiswerkdag").declared_date is None

    def test_to_declaration_carries_amount_and_status(self):
        d = row(
            boeking="11-02-2026", datum="17-08-2026", status="Afgehandeld",
            soort="Thuiswerkdag", omschrijving="Thuiswerkdag",
            aantal="1,00", bedrag="2,00",
        ).to_declaration()
        assert d.date == TARGET
        assert d.amount == "2,00"
        assert d.status == "Afgehandeld"


class TestIsThuiswerkdagRow:
    def test_matches_on_soort(self):
        assert is_thuiswerkdag_row(row(soort="Thuiswerkdag"), LABELS)

    def test_matches_on_omschrijving(self):
        assert is_thuiswerkdag_row(row(omschrijving="Thuiswerkdag"), LABELS)

    @pytest.mark.parametrize(
        "soort",
        ["Studiekosten", "Parkeerkosten", "Overuren 150%", "Standby uren",
         "Beurs- Congreskosten", "Reiskosten", ""],
    )
    def test_rejects_other_types(self, soort):
        assert not is_thuiswerkdag_row(row(soort=soort), LABELS)

    def test_ignores_the_word_outside_the_type_columns(self):
        """A status or attachment mentioning the word must not qualify."""
        r = row(status="Thuiswerkdag", soort="Parkeerkosten")
        assert not is_thuiswerkdag_row(r, LABELS)

    def test_normalize_is_accent_and_case_insensitive(self):
        assert normalize("  THUÍSWERKDAG\n ") == "thuiswerkdag"


class TestFindThuiswerkdag:
    def test_finds_exact_match(self):
        rows = [
            row(boeking="03-08-2026", soort="Studiekosten", bedrag="302,50"),
            row(boeking="18-08-2026", datum="17-08-2026", status="Afgehandeld",
                soort="Thuiswerkdag", bedrag="2,00"),
        ]
        found = find_thuiswerkdag(rows, TARGET, LABELS)
        assert found is not None and found.date == TARGET
        assert found.amount == "2,00"

    def test_booking_date_must_not_be_matched(self):
        """The bug this column-aware design exists to prevent.

        A Thuiswerkdag booked on 17-08 but declared for 05-02 is NOT a
        declaration for 17-08. Matching row text would wrongly say it is, and
        the tool would skip a declaration that is genuinely needed.
        """
        rows = [row(boeking="17-08-2026", datum="05-02-2026", soort="Thuiswerkdag")]
        assert find_thuiswerkdag(rows, TARGET, LABELS) is None
        assert find_thuiswerkdag(rows, date(2026, 2, 5), LABELS) is not None

    def test_real_capture_shape(self):
        """Four Thuiswerkdagen all booked 11-02-2026 for four different days."""
        rows = [
            row(boeking="11-02-2026", datum=d, status="Afgehandeld",
                soort="Thuiswerkdag", omschrijving="Thuiswerkdag",
                aantal="1,00", bedrag="2,00")
            for d in ("05-02-2026", "11-02-2026", "04-02-2026", "27-01-2026")
        ]
        for declared in (date(2026, 2, 5), date(2026, 2, 11),
                         date(2026, 2, 4), date(2026, 1, 27)):
            assert find_thuiswerkdag(rows, declared, LABELS) is not None
        # A day only present as a booking date is not a declared day.
        assert find_thuiswerkdag(rows, date(2026, 2, 10), LABELS) is None

    def test_adjacent_dates_do_not_match(self):
        rows = [row(datum="18-08-2026", soort="Thuiswerkdag")]
        assert find_thuiswerkdag(rows, TARGET, LABELS) is None
        assert find_thuiswerkdag(rows, date(2026, 8, 18), LABELS) is not None

    def test_right_date_wrong_type_is_not_a_duplicate(self):
        rows = [
            row(datum="17-08-2026", soort="Parkeerkosten", bedrag="4,50"),
            row(boeking="17-08-2026", soort="Studiekosten"),
        ]
        assert find_thuiswerkdag(rows, TARGET, LABELS) is None

    def test_no_rows(self):
        assert find_thuiswerkdag([], TARGET, LABELS) is None


class TestSummarize:
    def test_lists_only_thuiswerkdag_rows(self):
        rows = [
            row(datum="16-08-2026", soort="Reiskosten"),
            row(datum="17-08-2026", soort="Thuiswerkdag"),
            row(datum="18-08-2026", soort="Thuiswerkdag"),
        ]
        summary = summarize_thuiswerkdagen(rows, LABELS)
        assert {d.date for d in summary} == {date(2026, 8, 17), date(2026, 8, 18)}


class TestTextFallback:
    """Used only when column headers cannot be resolved — deliberately strict."""

    def test_matches_unambiguous_row(self):
        rows = ["Thuiswerkdag | 17-08-2026 | € 2,00 | Afgehandeld"]
        assert find_thuiswerkdag_in_texts(rows, TARGET, LABELS) is not None

    def test_skips_ambiguous_two_date_row(self):
        """Cannot tell booking date from declared date — refuse to guess."""
        rows = ["11-02-2026 | 17-08-2026 | Afgehandeld | Thuiswerkdag | 2,00"]
        assert find_thuiswerkdag_in_texts(rows, TARGET, LABELS) is None

    def test_wrong_type_not_matched(self):
        rows = ["Parkeerkosten | 17-08-2026 | € 4,50"]
        assert find_thuiswerkdag_in_texts(rows, TARGET, LABELS) is None

    def test_adjacent_date_not_matched(self):
        rows = ["Thuiswerkdag | 18-08-2026 | € 2,00"]
        assert find_thuiswerkdag_in_texts(rows, TARGET, LABELS) is None

    def test_blank_rows_ignored(self):
        assert find_thuiswerkdag_in_texts(["", "  "], TARGET, LABELS) is None
