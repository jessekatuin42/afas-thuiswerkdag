from __future__ import annotations

from datetime import date

from src.adapters.base import FileOutcome
from src.adapters.shuttel import ShuttelAdapter

D = date(2026, 9, 9)

TEMPLATE_OUT = {
    "transactionId": "T-OUT", "startsOn": "2026-02-13T08:00:00.000+01:00",
    "endsOn": "2026-02-13T09:59:36.000+01:00", "costType": "commute",
    "declarationCode": "A3", "quantities": [{"amount": 222.0, "unit": "km"}],
    "locations": [{"name": "A", "time": "2026-02-13T08:00:00.000+01:00"}],
}
TEMPLATE_BACK = {**TEMPLATE_OUT, "transactionId": "T-BACK",
                 "startsOn": "2026-02-13T17:00:00.000+01:00",
                 "endsOn": "2026-02-13T18:59:36.000+01:00"}


def tx(day: date, hour: int):
    return {"startsOn": f"{day.isoformat()}T{hour:02d}:00:00.000+02:00",
            "costType": "commute", "quantities": [{"amount": 222.0, "unit": "km"}]}


class FakeApi:
    """Stands in for the HTTP client. Records posts; performs no I/O."""

    def __init__(self, transactions=None, post_status=200, favourites=None):
        self._transactions = list(transactions or [])
        self._post_status = post_status
        self._favourites = favourites if favourites is not None else [
            TEMPLATE_OUT, TEMPLATE_BACK]
        self.posted: list[dict] = []

    def get(self, path):
        if "favorites/routes" in path:
            return {"status": 200, "body": self._favourites}
        page = {"content": self._transactions, "totalElements": len(self._transactions),
                "totalPages": 1, "number": 0}
        return {"status": 200, "body": page}

    def post(self, path, payload):
        self.posted.append(payload)
        if self._post_status == 200:
            self._transactions.append(payload)
        return {"status": self._post_status, "body": {}}


def adapter(api):
    return ShuttelAdapter(api, template_ids=("T-OUT", "T-BACK"))


def test_read_month_reports_days_that_already_have_commute_transactions():
    api = FakeApi(transactions=[tx(D, 8), tx(D, 17)])
    entries = adapter(api).read_month(2026, 9)
    assert list(entries) == [D]


def test_read_month_ignores_transactions_outside_the_month():
    api = FakeApi(transactions=[tx(date(2026, 8, 31), 8)])
    assert adapter(api).read_month(2026, 9) == {}


def test_read_month_is_empty_when_nothing_is_filed():
    assert adapter(FakeApi()).read_month(2026, 9) == {}


def test_filing_a_day_posts_every_configured_leg():
    """An office day is two journeys. Filing one would under-claim silently."""
    api = FakeApi()
    result = adapter(api).file(D)
    assert len(api.posted) == 2
    assert result.outcome is FileOutcome.FILED


def test_each_posted_leg_lands_on_the_requested_date_at_its_own_time():
    api = FakeApi()
    adapter(api).file(D)
    starts = sorted(p["startsOn"] for p in api.posted)
    assert starts[0].startswith("2026-09-09T08:00:00.000")
    assert starts[1].startswith("2026-09-09T17:00:00.000")


def test_no_posted_leg_carries_the_template_transaction_id():
    api = FakeApi()
    adapter(api).file(D)
    assert all("transactionId" not in p for p in api.posted)


def test_filing_is_confirmed_by_re_reading_not_by_the_post_status():
    """Same rule as AFAS: never assume a submit worked."""
    class PostSucceedsButNothingAppears(FakeApi):
        def post(self, path, payload):
            self.posted.append(payload)
            return {"status": 200, "body": {}}      # server says fine, stores nothing

    api = PostSucceedsButNothingAppears()
    assert adapter(api).file(D).outcome is FileOutcome.UNVERIFIED


def test_a_rejected_post_fails_without_posting_the_remaining_legs():
    """Never retry or continue past an error mid-day: a half-filed day is worse
    than an unfiled one, because nothing shows it is half-filed."""
    api = FakeApi(post_status=400)
    result = adapter(api).file(D)
    assert result.outcome is FileOutcome.FAILED
    assert len(api.posted) == 1


def test_a_day_that_is_already_filed_is_reported_as_already():
    api = FakeApi(transactions=[tx(D, 8), tx(D, 17)])
    result = adapter(api).file(D)
    assert result.outcome is FileOutcome.ALREADY
    assert api.posted == []


def test_missing_template_configuration_fails_loudly():
    api = FakeApi(favourites=[])
    result = ShuttelAdapter(api, template_ids=("T-OUT",)).file(D)
    assert result.outcome is FileOutcome.FAILED
    assert "template" in result.message.lower()


def test_a_refusal_carries_the_servers_explanation():
    """A bare status code sends you guessing. Shuttel answers RFC 7807 problem
    documents, and the 'detail' field is the whole diagnosis."""
    class Refusing(FakeApi):
        def post(self, path, payload):
            self.posted.append(payload)
            return {"status": 409, "body": {
                "title": "Conflict",
                "detail": "Transaction overlaps an existing one",
            }}

    result = adapter(Refusing()).file(D)
    assert result.outcome is FileOutcome.FAILED
    assert "409" in result.message
    assert "overlaps an existing one" in result.message


def test_journeys_go_to_the_transaction_endpoint_not_the_declaration_one():
    """/api/v1/transaction/declaration is the *expense* endpoint and answers
    409 'Declaration does not have an attachment' for a journey. Journeys go to
    /api/v1/transaction/."""
    class PathRecording(FakeApi):
        def __init__(self):
            super().__init__()
            self.paths = []

        def post(self, path, payload):
            self.paths.append(path)
            return super().post(path, payload)

    api = PathRecording()
    adapter(api).file(D)
    assert api.paths == ["/api/v1/transaction/"] * 2


def test_a_day_missing_one_leg_is_refused_rather_than_topped_up():
    """A half-filed day must not be reported done, and must not be completed
    blindly either: posting the full set again would duplicate the leg that is
    already there. Surface it instead."""
    api = FakeApi(transactions=[tx(D, 8)])          # outbound only
    result = adapter(api).file(D)
    assert result.outcome is FileOutcome.FAILED
    assert "1 of 2" in result.message
    assert api.posted == []


def test_a_complete_day_is_reported_already():
    api = FakeApi(transactions=[tx(D, 8), tx(D, 17)])
    assert adapter(api).file(D).outcome is FileOutcome.ALREADY


def test_read_month_does_not_report_a_partly_filed_day_as_done():
    api = FakeApi(transactions=[tx(D, 8)])
    assert adapter(api).read_month(2026, 9) == {}
