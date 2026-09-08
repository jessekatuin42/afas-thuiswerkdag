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

STATIC_DIR = Path(__file__).resolve().parent / "static"


class IntentBody(BaseModel):
    intent: str

    @field_validator("intent")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in {i.value for i in Intent}:
            raise ValueError(f"unknown intent {v!r}")
        return v


def _parse_day(iso: str) -> date:
    try:
        return date.fromisoformat(iso)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"not a date: {iso!r}") from None


def create_app(store: PlanStore, engine: SyncEngine) -> FastAPI:
    app = FastAPI(title="Day planner")

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

    @app.post("/api/refresh/{year}/{month}")
    def refresh(year: int, month: int):
        return {"status": engine.refresh_state(year, month)}

    @app.post("/api/sync/{year}/{month}")
    def sync(year: int, month: int):
        report = engine.sync(year, month)
        return {
            "outcome": report.outcome,
            "stopped_reason": report.stopped_reason,
            "results": [
                {**r, "date": r["date"].isoformat()} for r in report.results
            ],
        }

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
