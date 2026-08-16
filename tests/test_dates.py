"""Date parsing / matching — the logic duplicate detection rests on."""

from datetime import date

import pytest

from src import dates


class TestParseIso:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("2026-08-17", date(2026, 8, 17)),
            ("2026-01-01", date(2026, 1, 1)),
            ("2024-02-29", date(2024, 2, 29)),  # leap year
            ("  2026-08-17  ", date(2026, 8, 17)),
        ],
    )
    def test_valid(self, text, expected):
        assert dates.parse_iso(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "2026-99-99",   # nonsense month/day
            "2026-13-01",   # month 13
            "2026-02-30",   # not a real February day
            "2023-02-29",   # not a leap year
            "17-08-2026",   # Dutch order is not accepted on the CLI
            "2026/08/17",
            "20260817",
            "tomorrow",
            "",
            "2026-8-7",     # must be zero-padded
        ],
    )
    def test_rejected(self, text):
        with pytest.raises(dates.InvalidDateError):
            dates.parse_iso(text)

    def test_rejects_non_string(self):
        with pytest.raises(dates.InvalidDateError):
            dates.parse_iso(20260817)  # type: ignore[arg-type]


class TestFormatting:
    def test_dutch_is_zero_padded(self):
        assert dates.to_dutch(date(2026, 8, 7)) == "07-08-2026"

    def test_iso_roundtrip(self):
        d = date(2026, 8, 17)
        assert dates.parse_iso(dates.to_iso(d)) == d

    def test_dutch_differs_from_iso(self):
        d = date(2026, 8, 17)
        assert dates.to_dutch(d) == "17-08-2026"
        assert dates.to_iso(d) == "2026-08-17"


class TestExtractDates:
    def test_dutch_numeric(self):
        assert date(2026, 8, 17) in dates.extract_dates("Thuiswerkdag 17-08-2026 €2,00")

    def test_iso(self):
        assert date(2026, 8, 17) in dates.extract_dates("date=2026-08-17")

    def test_slashes_and_dots(self):
        assert date(2026, 8, 17) in dates.extract_dates("17/08/2026")
        assert date(2026, 8, 17) in dates.extract_dates("17.08.2026")

    def test_two_digit_year(self):
        assert date(2026, 8, 17) in dates.extract_dates("17-08-26")

    def test_unpadded(self):
        assert date(2026, 8, 7) in dates.extract_dates("7-8-2026")

    def test_dutch_month_names(self):
        assert date(2026, 8, 17) in dates.extract_dates("17 augustus 2026")
        assert date(2026, 8, 17) in dates.extract_dates("17 aug 2026")
        assert date(2026, 8, 17) in dates.extract_dates("17 aug. 2026")

    def test_impossible_dates_dropped(self):
        assert dates.extract_dates("31-11-2026") == set()
        assert dates.extract_dates("99-99-9999") == set()

    def test_multiple(self):
        found = dates.extract_dates("van 17-08-2026 tot 18-08-2026")
        assert found == {date(2026, 8, 17), date(2026, 8, 18)}

    def test_empty(self):
        assert dates.extract_dates("") == set()


class TestTextContainsDate:
    """The precision requirement: 17-08 must never match 18-08."""

    def test_exact_match(self):
        assert dates.text_contains_date("Thuiswerkdag 17-08-2026", date(2026, 8, 17))

    def test_adjacent_day_does_not_match(self):
        assert not dates.text_contains_date("Thuiswerkdag 18-08-2026", date(2026, 8, 17))

    def test_adjacent_month_does_not_match(self):
        assert not dates.text_contains_date("Thuiswerkdag 17-09-2026", date(2026, 8, 17))

    def test_adjacent_year_does_not_match(self):
        assert not dates.text_contains_date("Thuiswerkdag 17-08-2025", date(2026, 8, 17))

    def test_not_fooled_by_substring_in_identifier(self):
        # An ID that merely contains the digits is not a date.
        assert not dates.text_contains_date("ref 1708202612345", date(2026, 8, 17))

    def test_mixed_row_matches_only_its_own_date(self):
        row = "Thuiswerkdag | 18-08-2026 | € 2,00 | Ter goedkeuring"
        assert dates.text_contains_date(row, date(2026, 8, 18))
        assert not dates.text_contains_date(row, date(2026, 8, 17))


class TestRelativeDates:
    def test_yesterday_is_one_day_before_today(self):
        assert (dates.today() - dates.yesterday()).days == 1
