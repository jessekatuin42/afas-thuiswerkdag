"""SQLite persistence for the plan, the read-back state cache, and run history.

stdlib sqlite3 on purpose: one user, one process, a few hundred rows. An ORM
would be more dependency than this earns.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .model import DayState, Intent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS day_plan (
    date       TEXT PRIMARY KEY,
    intent     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_cache (
    date    TEXT NOT NULL,
    system  TEXT NOT NULL,
    present INTEGER NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    read_at TEXT NOT NULL,
    PRIMARY KEY (date, system)
);
CREATE TABLE IF NOT EXISTS sync_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT
);
CREATE TABLE IF NOT EXISTS day_result (
    run_id  INTEGER NOT NULL,
    date    TEXT NOT NULL,
    system  TEXT NOT NULL,
    outcome TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    seq     INTEGER NOT NULL
);
"""


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start.isoformat(), end.isoformat()


class PlanStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- plan -------------------------------------------------------------

    def set_intent(self, day: date, intent: Intent) -> None:
        if intent is Intent.NONE:
            self._conn.execute("DELETE FROM day_plan WHERE date = ?", (day.isoformat(),))
        else:
            self._conn.execute(
                "INSERT INTO day_plan (date, intent, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET intent = excluded.intent, "
                "updated_at = excluded.updated_at",
                (day.isoformat(), intent.value, datetime.now().isoformat()),
            )
        self._conn.commit()

    def get_plan(self, year: int, month: int) -> dict[date, Intent]:
        lo, hi = _month_bounds(year, month)
        rows = self._conn.execute(
            "SELECT date, intent FROM day_plan WHERE date >= ? AND date < ? ORDER BY date",
            (lo, hi),
        ).fetchall()
        return {date.fromisoformat(r["date"]): Intent(r["intent"]) for r in rows}

    # -- state cache ------------------------------------------------------

    def set_state(self, day: date, system: str, present: bool, summary: str = "") -> None:
        self._conn.execute(
            "INSERT INTO state_cache (date, system, present, summary, read_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(date, system) DO UPDATE SET "
            "present = excluded.present, summary = excluded.summary, "
            "read_at = excluded.read_at",
            (day.isoformat(), system, int(present), summary, datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_state(self, year: int, month: int) -> dict[date, DayState]:
        lo, hi = _month_bounds(year, month)
        rows = self._conn.execute(
            "SELECT date, system, present FROM state_cache WHERE date >= ? AND date < ?",
            (lo, hi),
        ).fetchall()
        acc: dict[date, dict[str, bool]] = {}
        for r in rows:
            acc.setdefault(date.fromisoformat(r["date"]), {})[r["system"]] = bool(r["present"])
        # .get() with no default: a system with no row stays None ("never
        # read"), which diff() refuses to file against. Defaulting to False
        # here would silently re-introduce blind filing.
        return {
            day: DayState(afas=flags.get("afas"), shuttel=flags.get("shuttel"))
            for day, flags in acc.items()
        }

    def state_read_at(self, system: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(read_at) AS t FROM state_cache WHERE system = ?", (system,)
        ).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    # -- runs -------------------------------------------------------------

    def start_run(self) -> int:
        cur = self._conn.execute(
            "INSERT INTO sync_run (started_at) VALUES (?)", (datetime.now().isoformat(),)
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_result(
        self, run_id: int, day: date, system: str, outcome: str, message: str = ""
    ) -> None:
        seq = self._conn.execute(
            "SELECT COUNT(*) AS n FROM day_result WHERE run_id = ?", (run_id,)
        ).fetchone()["n"]
        self._conn.execute(
            "INSERT INTO day_result (run_id, date, system, outcome, message, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, day.isoformat(), system, outcome, message, seq),
        )
        self._conn.commit()

    def finish_run(self, run_id: int, outcome: str) -> None:
        self._conn.execute(
            "UPDATE sync_run SET finished_at = ?, outcome = ? WHERE id = ?",
            (datetime.now().isoformat(), outcome, run_id),
        )
        self._conn.commit()

    def get_run(self, run_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, started_at, finished_at, outcome FROM sync_run WHERE id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_run_results(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT date, system, outcome, message FROM day_result "
            "WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [
            {
                "date": date.fromisoformat(r["date"]),
                "system": r["system"],
                "outcome": r["outcome"],
                "message": r["message"],
            }
            for r in rows
        ]
