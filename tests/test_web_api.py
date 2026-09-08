from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.adapters.base import FileOutcome, FileResult
from src.planner.engine import SyncEngine
from src.planner.store import PlanStore
from web.app import create_app

D1 = date(2026, 9, 8)


class FakeFiler:
    def __init__(self, system):
        self.system = system
        self.filed = []

    def read_month(self, year, month):
        return {}

    def file(self, day):
        self.filed.append(day)
        return FileResult(day, self.system, FileOutcome.FILED)


@pytest.fixture
def client(tmp_path):
    store = PlanStore(tmp_path / "plan.db")
    filers = {"afas": FakeFiler("afas"), "shuttel": FakeFiler("shuttel")}
    app = create_app(store, SyncEngine(store, filers))
    app.state.filers = filers
    return TestClient(app)


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


def test_preview_reports_unknown_before_a_refresh_and_file_after(client):
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    before = client.get("/api/month/2026/9").json()["actions"]
    assert [a["kind"] for a in before] == ["unknown"]

    client.post("/api/refresh/2026/9")
    after = client.get("/api/month/2026/9").json()["actions"]
    assert [a["kind"] for a in after] == ["file"]


def test_sync_is_a_post_and_reports_its_outcome(client):
    client.put("/api/day/2026-09-08", json={"intent": "home"})
    client.post("/api/refresh/2026/9")
    body = client.post("/api/sync/2026/9").json()
    assert body["outcome"] == "done"
    assert client.app.state.filers["afas"].filed == [D1]


def test_sync_refuses_to_run_from_a_get(client):
    assert client.get("/api/sync/2026/9").status_code == 405


def test_the_dashboard_page_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
