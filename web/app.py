"""FastAPI dashboard. Loopback only -- see web/main.py and compose.

Routes are deliberately coarse: a month view that returns plan, state and diff
together, and two POSTs that do the slow work. The calendar is not worth a
per-cell round trip.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from src.planner.engine import SyncEngine
from src.planner.model import Intent
from src.planner.store import PlanStore
from web.jobs import JobRunner

STATIC_DIR = Path(__file__).resolve().parent / "static"


class IntentBody(BaseModel):
    intent: str

    @field_validator("intent")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in {i.value for i in Intent}:
            raise ValueError(f"unknown intent {v!r}")
        return v


class BulkIntentBody(IntentBody):
    """Painting a range is one request, not one per day."""

    dates: list[str]

    @field_validator("dates")
    @classmethod
    def all_parseable(cls, v: list[str]) -> list[str]:
        # Validated up front so the write is all-or-nothing: a half-applied
        # paint would leave the calendar showing something the user never chose.
        for iso in v:
            try:
                date.fromisoformat(iso)
            except ValueError:
                raise ValueError(f"not a date: {iso!r}") from None
        return v


def _parse_day(iso: str) -> date:
    try:
        return date.fromisoformat(iso)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"not a date: {iso!r}") from None


def create_app(
    store: PlanStore, engine: SyncEngine, runner: JobRunner | None = None
) -> FastAPI:
    app = FastAPI(title="Day planner")
    jobs = runner or JobRunner()

    @app.get("/api/month/{year}/{month}")
    def month(year: int, month: int):
        plan = store.get_plan(year, month)
        state = store.get_state(year, month)
        days = []
        for d in range(1, calendar.monthrange(year, month)[1] + 1):
            day = date(year, month, d)
            observed = state.get(day)
            days.append({
                "date": day.isoformat(),
                "weekday": day.weekday(),
                "intent": plan.get(day, Intent.NONE).value,
                "afas": None if observed is None else observed.afas,
                "shuttel": None if observed is None else observed.shuttel,
            })
        return {
            "year": year,
            "month": month,
            "days": days,
            "actions": [
                {"date": a.day.isoformat(), "system": a.system,
                 "kind": a.kind.value, "reason": a.reason}
                for a in engine.preview(year, month)
            ],
            "read_at": {
                s: (t.isoformat() if (t := store.state_read_at(s)) else None)
                for s in ("afas", "shuttel")
            },
        }

    @app.put("/api/day/{iso}")
    def set_day(iso: str, body: IntentBody):
        store.set_intent(_parse_day(iso), Intent(body.intent))
        return {"ok": True}

    @app.put("/api/days")
    def set_days(body: BulkIntentBody):
        for iso in body.dates:
            store.set_intent(date.fromisoformat(iso), Intent(body.intent))
        return {"ok": True, "count": len(body.dates)}

    @app.post("/api/refresh/{year}/{month}")
    def refresh(year: int, month: int):
        # Reading AFAS costs a browser launch and ~40s, far too long to hold a
        # request open. The client polls /api/status instead.
        if not jobs.start("refresh", lambda: engine.refresh_state(year, month)):
            raise HTTPException(status_code=409, detail="a job is already running")
        return {"started": True}

    @app.post("/api/sync/{year}/{month}")
    def sync(year: int, month: int):
        """Start filing. Returns a run id to poll -- never the outcome.

        Filing a month is ~30s per day, so a synchronous response would time
        out long before the work finished. The run row is opened here so the
        caller has something to poll before any day has been attempted.
        """
        if jobs.status()["busy"]:
            raise HTTPException(status_code=409, detail="a job is already running")
        run_id = store.start_run()
        if not jobs.start("sync", lambda: engine.sync(year, month, run_id=run_id),
                          run_id=run_id):
            raise HTTPException(status_code=409, detail="a job is already running")
        return {"run_id": run_id}

    @app.get("/api/status")
    def status():
        return jobs.status()

    @app.get("/api/run/{run_id}")
    def run(run_id: int):
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        return {
            "run_id": run_id,
            "outcome": record["outcome"],
            "finished_at": record["finished_at"],
            "results": [
                {**r, "date": r["date"].isoformat()}
                for r in store.get_run_results(run_id)
            ],
        }

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
