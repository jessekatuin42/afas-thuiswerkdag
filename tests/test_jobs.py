from __future__ import annotations

import threading

from web.jobs import JobRunner


def wait_until(predicate, timeout=5.0):
    deadline = threading.Event()
    t = threading.Timer(timeout, deadline.set)
    t.start()
    try:
        while not deadline.is_set():
            if predicate():
                return True
            threading.Event().wait(0.01)
        return False
    finally:
        t.cancel()


def test_a_job_runs_and_clears_busy_when_it_finishes():
    runner = JobRunner()
    done = threading.Event()
    assert runner.start("sync", done.set) is True
    assert done.wait(5)
    assert wait_until(lambda: not runner.status()["busy"])


def test_a_second_job_is_refused_while_one_is_running():
    """Not a queue on purpose: AFAS's browser profile is single-writer, so two
    concurrent jobs would corrupt the session."""
    runner = JobRunner()
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(5)

    assert runner.start("sync", slow) is True
    assert started.wait(5)
    assert runner.start("refresh", lambda: None) is False
    release.set()
    assert wait_until(lambda: not runner.status()["busy"])


def test_status_reports_the_kind_and_run_id_of_the_current_job():
    runner = JobRunner()
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(5)

    runner.start("sync", slow, run_id=42)
    assert started.wait(5)
    status = runner.status()
    assert status["busy"] is True
    assert status["kind"] == "sync"
    assert status["run_id"] == 42
    release.set()


def test_a_failing_job_records_the_error_and_still_clears_busy():
    """A job that dies must not wedge the runner -- otherwise one AFAS timeout
    locks the dashboard until restart."""
    runner = JobRunner()

    def boom():
        raise RuntimeError("chromium went away")

    runner.start("sync", boom)
    assert wait_until(lambda: not runner.status()["busy"])
    assert "chromium went away" in runner.status()["error"]


def test_a_new_job_can_start_after_the_previous_one_finished():
    runner = JobRunner()
    first, second = threading.Event(), threading.Event()
    runner.start("refresh", first.set)
    assert first.wait(5)
    assert wait_until(lambda: not runner.status()["busy"])
    assert runner.start("sync", second.set) is True
    assert second.wait(5)


def test_a_new_job_clears_the_previous_error():
    runner = JobRunner()
    runner.start("sync", lambda: (_ for _ in ()).throw(RuntimeError("old")))
    assert wait_until(lambda: not runner.status()["busy"])
    done = threading.Event()
    runner.start("refresh", done.set)
    assert done.wait(5)
    assert wait_until(lambda: not runner.status()["busy"])
    assert runner.status()["error"] == ""


def test_a_jobs_return_value_is_kept_and_reported():
    """refresh_state returns a per-system status. Dropping it made a Shuttel
    authentication failure look like nothing happened at all."""
    runner = JobRunner()
    runner.start("refresh", lambda: {"afas": "read 3", "shuttel": "no session"})
    assert wait_until(lambda: not runner.status()["busy"])
    assert runner.status()["result"] == {"afas": "read 3", "shuttel": "no session"}


def test_a_new_job_clears_the_previous_result():
    runner = JobRunner()
    runner.start("refresh", lambda: {"afas": "old"})
    assert wait_until(lambda: not runner.status()["busy"])
    runner.start("sync", lambda: None)
    assert wait_until(lambda: not runner.status()["busy"])
    assert runner.status()["result"] is None
