"""Opens a browser only for the duration of one AFAS operation.

Holding a Chromium context open for the life of the web process would mean an
expired AFAS session, a locked browser profile, and a 2 GB resident browser
sitting idle between clicks.
"""

from __future__ import annotations

from datetime import date

from ..afas import AfasInSite
from ..browser import session
from ..config import Config
from .afas_adapter import AfasAdapter
from .base import Entry, FileResult


class LazyAfasAdapter:
    system = "afas"

    def __init__(self, cfg: Config):
        self._cfg = cfg.with_overrides(headless=True)

    def _with_afas(self, fn):
        with session(self._cfg) as sess:
            sess.login_if_needed()
            return fn(AfasAdapter(AfasInSite(sess, self._cfg),
                                  self._cfg.thuiswerkdag_labels))

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        return self._with_afas(lambda a: a.read_month(year, month))

    def file(self, day: date) -> FileResult:
        return self._with_afas(lambda a: a.file(day))
