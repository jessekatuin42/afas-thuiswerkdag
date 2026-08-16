"""Timestamped console logging.

Logs go to stderr so that stdout stays clean for the human-readable result
block. Nothing here is ever handed a credential — callers must not log page
content verbatim.
"""

from __future__ import annotations

import sys
from datetime import datetime

_QUIET = False


def set_quiet(quiet: bool) -> None:
    global _QUIET
    _QUIET = quiet


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(message: str) -> None:
    if not _QUIET:
        print(f"[{_stamp()}] {message}", file=sys.stderr, flush=True)


def warn(message: str) -> None:
    print(f"[{_stamp()}] WARNING: {message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    print(f"[{_stamp()}] ERROR: {message}", file=sys.stderr, flush=True)
