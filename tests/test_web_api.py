from __future__ import annotations

import threading
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.adapters.base import Entry, FileOutcome, FileResult
from src.planner.engine import SyncEngine
from src.planner.store import PlanStore
from web.app import create_app

D1 = date(2026, 9, 8)


class FakeFiler:
    def __init__(self, system, month=None, gate: threading.Event | None = None):
        self.system = system
        self._month = month if month is not None else {}
        self._gate = gate
        self.filed = []

    def read_month(self, year, month):
        return self._month

    def file(self, day):
        if self._gate is not None:
            self._gate.wait(5)
        self.filed.append(day)
        return FileResult(day, self.system, FileOutcome.FILED)


def wait_idle(client, timeout=5.0):
    """Block until no background job is running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get("/api/status").json()["busy"]:
            return True
        time.sleep(0.01)
    return False


def make_client(tmp_path, filers):
    store = PlanStore(tmp_path / "plan.db")
    app = create_app(store, SyncEngine(store, filers))
    app.state.filers = filers
    app.state.store = store
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, {"afas": FakeFiler("afas"),
                                  "shuttel": FakeFiler("shuttel")})


def test_month_view_returns_a_cell_for_every_day(client):
    body = client.get("/api/month/2026/9").json()
    assert len(body["days"]) == 30
    assert body["days"][0]["date"] == "2026-09-01"


def test_setting_an_intent_persists_it(client):
    assert client.put("/api/day/2026-09-08", json={"intent": "home"}).status_code == 200
    days = {d["date"]: d for d in client.get("/api/month/2026/9").json()["days"]}
    assert days["2026-09-08"]["intent"] == "home"


def test_an_unknown_intent_is_rejected(client):
    assert client.put("/api/day/2026-09-08", json={"intent": "holiday"}).status_code == 422


def test_a_malformed_date_is_rejected(client):
    assert client.put("/api/day/not-a-date", json={"intent": "home"}).status_code == 422


def test_many_days_can_be_set_in_one_request(client):
    """Painting a range must not be one round trip per day."""
    resp = client.put("/api/days", json={
        "dates": ["2026-09-07", "2026-09-08", "2026-09-09"], "intent": "home",
    })
    assert resp.status_code == 200
    days = {d["date"]: d["intent"] for d in client.get("/api/month/2026/9").json()["days"]}
    assert days["2026-09-07"] == "home"
    assert days["2026-09-09"] == "home"


def test_a_bad_date_in_a_bulk_write_rejects_the_whole_request(client):
    """All-or-nothing: a partially applied paint would be invisible to the user."""
    resp = client.put("/api/days", json={
        "dates": ["2026-09-07", "nonsense"], "intent": "home",
    })
    assert resp.status_code == 422
    days = {d["date"]: d["intent"] for d in client.get("/api/month/2026/9").json()["days"]}
    assert days["2026-09-07"] == "none"


def test_preview_reports_unknown_before_a_refresh_and_file_after(client):
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    before = client.get("/api/month/2026/9").json()["actions"]
    assert [a["kind"] for a in before] == ["unknown"]

    client.post("/api/refresh/2026/9")
    assert wait_idle(client)
    after = client.get("/api/month/2026/9").json()["actions"]
    assert [a["kind"] for a in after] == ["file"]


def test_sync_returns_a_run_id_immediately_rather_than_the_outcome(client):
    """11 days at ~30s each cannot live inside one HTTP request."""
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    client.post("/api/refresh/2026/9")
    assert wait_idle(client)

    body = client.post("/api/sync/2026/9").json()
    assert isinstance(body["run_id"], int)
    assert "outcome" not in body

    assert wait_idle(client)
    assert client.app.state.filers["afas"].filed == [D1]


def test_a_second_sync_while_one_is_running_is_refused(tmp_path):
    gate = threading.Event()
    filers = {"afas": FakeFiler("afas", gate=gate), "shuttel": FakeFiler("shuttel")}
    client = make_client(tmp_path, filers)
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    client.post("/api/refresh/2026/9")
    assert wait_idle(client)

    assert client.post("/api/sync/2026/9").status_code == 200
    assert client.post("/api/sync/2026/9").status_code == 409
    gate.set()
    assert wait_idle(client)


def test_run_progress_is_readable_by_run_id(client):
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    client.post("/api/refresh/2026/9")
    assert wait_idle(client)
    run_id = client.post("/api/sync/2026/9").json()["run_id"]
    assert wait_idle(client)

    body = client.get(f"/api/run/{run_id}").json()
    assert body["outcome"] == "done"
    assert body["results"] == [
        {"date": "2026-09-08", "system": "afas", "outcome": "filed", "message": ""}
    ]


def test_an_unknown_run_id_is_a_404(client):
    assert client.get("/api/run/9999").status_code == 404


def test_status_reports_idle_when_nothing_is_running(client):
    body = client.get("/api/status").json()
    assert body["busy"] is False


def test_a_day_already_present_in_afas_is_marked_on_the_month_view(tmp_path):
    filers = {"afas": FakeFiler("afas", month={D1: Entry(D1, "Thuiswerkdag")}),
              "shuttel": FakeFiler("shuttel")}
    client = make_client(tmp_path, filers)
    client.post("/api/refresh/2026/9")
    assert wait_idle(client)
    days = {d["date"]: d for d in client.get("/api/month/2026/9").json()["days"]}
    assert days["2026-09-08"]["afas"] is True
    assert days["2026-09-07"]["afas"] is False


def test_the_dashboard_page_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
