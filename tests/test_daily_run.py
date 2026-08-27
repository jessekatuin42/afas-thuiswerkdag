"""Tests for the unattended wrapper, ``scripts/daily-run.sh``.

The wrapper's whole safety story rests on one question: *was this machine on at
11:00 local time?* These tests run it for real, but sandboxed — the state and
config directories are redirected into ``tmp_path`` and a pause file is planted,
so the run always stops at a guard and never reaches Playwright or AFAS.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parent.parent / "scripts" / "daily-run.sh"
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2}\s")

WINDOW_START = (11, 0)
WINDOW_END = (11, 59)


def _run(tmp_path: Path, tz: str | None, days: str = "1,2,3,4,5") -> list[str]:
    """Run the wrapper with a hostile TZ; return the lines it logged.

    ``days`` opens the work-from-home guard to every weekday by default, so
    these clock tests reach the window guard they are actually about whichever
    day of the week they happen to run on.
    """
    state = tmp_path / "state"
    config = tmp_path / "config"
    (config / "afas-thuiswerk").mkdir(parents=True)
    # Guard 3 stops the run dead, so nothing can reach the browser even if the
    # clock happens to sit inside the window while the tests run.
    (config / "afas-thuiswerk" / "pause").write_text("2099-01-01\n")

    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["AFAS_DAYS"] = days
    if tz is None:
        env.pop("TZ", None)
    else:
        env["TZ"] = tz

    subprocess.run([str(WRAPPER)], env=env, check=True, timeout=60)
    return (state / "afas-thuiswerk" / "run.log").read_text().splitlines()


@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo", "America/Denver"])
def test_logs_local_wall_clock_whatever_the_caller_says(tmp_path, tz):
    """A caller's TZ must not move the clock the wrapper reasons about.

    Regression: a run started at 13:00 local with TZ=UTC read its own clock as
    11:00, sailed through the presence window, and declared an afternoon run as
    the morning one.
    """
    before = datetime.now()
    lines = _run(tmp_path, tz)
    after = datetime.now()

    assert lines, "the wrapper logged nothing"
    stamped = STAMP.match(lines[0])
    assert stamped, f"unparseable log line: {lines[0]!r}"

    logged = stamped.group(1)
    allowed = {before.strftime("%Y-%m-%d %H:%M"), after.strftime("%Y-%m-%d %H:%M")}
    assert logged in allowed, (
        f"wrapper logged {logged} under TZ={tz}, expected local time {sorted(allowed)}"
    )


@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo"])
def test_window_verdict_follows_local_time(tmp_path, tz):
    """The presence window is judged against local time, not the caller's."""
    now = datetime.now()
    lines = _run(tmp_path, tz)
    refused = any("outside the 11:00-11:59 window" in line for line in lines)

    inside = WINDOW_START <= (now.hour, now.minute) <= WINDOW_END
    weekend = now.isoweekday() > 5

    if weekend:
        assert any("weekend" in line for line in lines)
    elif inside:
        assert not refused, "a run inside the local window was refused as a replay"
    else:
        assert refused, "a run outside the local window was accepted"


def test_office_days_are_skipped(tmp_path):
    """Today is an office day unless it is in the work-from-home list.

    Presence cannot distinguish a Monday at home from a Monday before leaving
    for the office, so the day list has to. Naming a list that excludes today
    must stop the run whatever the clock says.
    """
    today = datetime.now().isoweekday()
    others = ",".join(str(d) for d in range(1, 6) if d != today) or "1"

    lines = _run(tmp_path, tz=None, days=others)

    assert any("not a work-from-home day" in line or "weekend" in line for line in lines)


def test_default_declares_only_tuesday_to_thursday(tmp_path):
    """With AFAS_DAYS unset the schedule is Tue/Wed/Thu — Mon and Fri are office."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    (config / "afas-thuiswerk").mkdir(parents=True)
    (config / "afas-thuiswerk" / "pause").write_text("2099-01-01\n")

    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env.pop("AFAS_DAYS", None)

    subprocess.run([str(WRAPPER)], env=env, check=True, timeout=60)
    lines = (state / "afas-thuiswerk" / "run.log").read_text().splitlines()

    today = datetime.now().isoweekday()
    skipped = any(
        "not a work-from-home day" in line or "weekend" in line for line in lines
    )
    assert skipped is (today not in (2, 3, 4)), (
        f"ISO day {today}: unexpected work-from-home verdict in {lines}"
    )


def test_malformed_day_list_refuses_to_guess(tmp_path):
    """A garbled AFAS_DAYS must skip, never fall back to declaring."""
    lines = _run(tmp_path, tz=None, days="tue,wed")

    assert any("unreadable AFAS_DAYS" in line for line in lines)
