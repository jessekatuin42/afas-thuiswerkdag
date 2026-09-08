from __future__ import annotations

from datetime import date

import pytest

from src.adapters.shuttel import (
    READ_ONLY_FIELDS,
    redate_template,
)

# Synthetic ids and placeholder addresses: this repository is public, so no
# real transaction reference or location belongs in a fixture.
TEMPLATE = {
    "title": "Autorit",
    "transactionId": "Shuttel000000001",
    "referenceId": "ref-1",
    "startsOn": "2026-02-13T08:00:00.000+01:00",
    "endsOn": "2026-02-13T09:59:36.000+01:00",
    "costType": "commute",
    "declarationCode": "A3",
    "declarationGroup": "PRIVATE-CAR",
    "quantities": [{"amount": 222.0, "unit": "km", "proposed": False}],
    "locations": [
        {"name": "A", "time": "2026-02-13T08:00:00.000+01:00", "coordinate": "1,2"},
        {"name": "B", "time": "2026-02-13T09:59:36.000+01:00", "coordinate": "3,4"},
    ],
    "processed": True,
    "deletable": True,
    "editable": True,
    "type": "template",
    "filtered": "TAGGED_OWNTRANSPORT",
    "co2": 5.05,
}


def test_the_date_moves_and_the_time_of_day_is_kept():
    out = redate_template(TEMPLATE, date(2026, 9, 9))
    assert out["startsOn"].startswith("2026-09-09T08:00:00.000")
    assert out["endsOn"].startswith("2026-09-09T09:59:36.000")


def test_location_times_move_with_it():
    out = redate_template(TEMPLATE, date(2026, 9, 9))
    assert [loc["time"][:10] for loc in out["locations"]] == ["2026-09-09"] * 2
    assert out["locations"][0]["time"].startswith("2026-09-09T08:00:00.000")


def test_the_utc_offset_follows_dutch_summer_time():
    """The template was recorded in February at +01:00. Copying that offset to
    a September date would file the journey an hour off -- and 08:00+01:00 is
    09:00 local, which is not when the drive happened."""
    winter = redate_template(TEMPLATE, date(2026, 1, 20))
    summer = redate_template(TEMPLATE, date(2026, 7, 20))
    assert winter["startsOn"].endswith("+01:00")
    assert summer["startsOn"].endswith("+02:00")
    assert winter["startsOn"].startswith("2026-01-20T08:00:00.000")
    assert summer["startsOn"].startswith("2026-07-20T08:00:00.000")


def test_server_owned_fields_are_dropped():
    """transactionId identifies an existing transaction. Sending it back either
    fails or ties the new declaration to the old one."""
    out = redate_template(TEMPLATE, date(2026, 9, 9))
    for field in READ_ONLY_FIELDS:
        assert field not in out, field
    assert "transactionId" not in out


def test_the_declaration_content_is_carried_over_untouched():
    out = redate_template(TEMPLATE, date(2026, 9, 9))
    assert out["declarationCode"] == "A3"
    assert out["costType"] == "commute"
    assert out["quantities"] == [{"amount": 222.0, "unit": "km", "proposed": False}]
    assert out["locations"][0]["name"] == "A"
    assert out["locations"][0]["coordinate"] == "1,2"


def test_the_template_itself_is_not_mutated():
    before = TEMPLATE["startsOn"]
    redate_template(TEMPLATE, date(2026, 9, 9))
    assert TEMPLATE["startsOn"] == before
    assert "transactionId" in TEMPLATE


def test_a_template_without_times_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        redate_template({"costType": "commute"}, date(2026, 9, 9))
