"""Runs one background job at a time.

One at a time is a correctness requirement, not a simplification: the AFAS
adapter drives a Chromium profile directory, and Chromium profiles are
single-writer. Two concurrent jobs would fight over it and cost the logged-in
session -- the one thing here that is expensive to recreate.

So a second request is refused rather than queued. A queue would hide the
contention; refusing surfaces it.
"""

from __future__ import annotations

import threading
from typing import Callable


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._kind = ""
        self._run_id: int | None = None
        self._error = ""
        self._result: object = None
        self._thread: threading.Thread | None = None

    def start(
        self, kind: str, fn: Callable[[], object], run_id: int | None = None
    ) -> bool:
        """Begin a job. False (and nothing started) if one is already running."""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._kind = kind
            self._run_id = run_id
            self._error = ""
            self._result = None

        def wrapper() -> None:
            try:
                # Keep what the job returned. refresh_state reports per-system
                # status, and discarding it turned a Shuttel authentication
                # failure into silence.
                self._result = fn()
            except Exception as exc:
                # Never let a failed job wedge the runner: one AFAS timeout
                # would otherwise lock the dashboard until a restart.
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    self._busy = False

        self._thread = threading.Thread(target=wrapper, daemon=True)
        self._thread.start()
        return True

    def status(self) -> dict:
        with self._lock:
            return {
                "busy": self._busy,
                "kind": self._kind,
                "run_id": self._run_id,
                "error": self._error,
                "result": self._result,
            }
