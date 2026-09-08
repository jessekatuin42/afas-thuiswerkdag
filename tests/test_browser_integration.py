"""Browser-level tests — against local fixtures that mirror the real AFAS DOM.

These launch a real headless Chromium so the Playwright plumbing in
``AfasInSite`` is genuinely exercised: grid/column resolution, quick filters,
the two-step create flow, and the fail-safe paths. They never reach AFAS and
never create a real declaration.

Run just these:      python -m pytest tests/test_browser_integration.py -q
Skip them entirely:  python -m pytest tests/ -q -m "not browser"
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.afas import RE_AANMAKEN, AfasInSite
from src.browser import BrowserSession, settle
from src.config import load_config
from src.detection import find_thuiswerkdag

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = date(2026, 8, 17)


@pytest.fixture(scope="module")
def sess(tmp_path_factory):
    """A throwaway headless browser with its own profile (not your AFAS one)."""
    profile = tmp_path_factory.mktemp("profile")
    artifacts = tmp_path_factory.mktemp("artifacts")
    cfg = load_config().with_overrides(
        headless=True, profile_dir=profile, artifacts_dir=artifacts
    )
    s = BrowserSession(cfg)
    try:
        s.start()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Chromium unavailable: {exc}")
    yield s
    s.stop()


def _open(sess: BrowserSession, fixture: str) -> AfasInSite:
    sess.page.goto((FIXTURES / fixture).as_uri(), wait_until="domcontentloaded")
    settle(sess.page, quiet_ms=200)
    return AfasInSite(sess, sess.cfg)


class TestGridReading:
    def test_resolves_column_headers(self, sess):
        afas = _open(sess, "declaraties_table.html")
        headers = afas.read_headers()
        assert "Datum" in headers and "Soort declaratie" in headers

    def test_reads_structured_rows_only(self, sess):
        """Header and quick-filter rows must not become declarations.

        The filter row's cells hold inputs and no text, so it drops out — which
        is what we want: only the 3 real data rows survive.
        """
        afas = _open(sess, "declaraties_table.html")
        rows = afas.read_grid_rows()
        assert len(rows) == 3
        assert {r.cell("soort declaratie") for r in rows} == {
            "Thuiswerkdag", "Parkeerkosten"
        }

    def test_booking_date_is_not_treated_as_the_declared_date(self, sess):
        """End-to-end through the browser: the 17-08 *booking* must not match."""
        afas = _open(sess, "declaraties_table.html")
        rows = afas.read_grid_rows()
        labels = sess.cfg.thuiswerkdag_labels
        assert find_thuiswerkdag(rows, TARGET, labels) is None
        assert find_thuiswerkdag(rows, date(2026, 2, 5), labels) is not None

    def test_parkeerkosten_on_target_date_is_not_a_duplicate(self, sess):
        afas = _open(sess, "declaraties_table.html")
        rows = afas.read_grid_rows()
        assert find_thuiswerkdag(rows, TARGET, sess.cfg.thuiswerkdag_labels) is None

    def test_finds_a_genuine_match(self, sess):
        afas = _open(sess, "declaraties_table.html")
        rows = afas.read_grid_rows()
        found = find_thuiswerkdag(rows, date(2026, 8, 18), sess.cfg.thuiswerkdag_labels)
        assert found is not None and found.amount == "2,00"


class TestAsyncRendering:
    def test_waits_for_a_grid_that_renders_late(self, sess):
        """AFAS builds the grid asynchronously; rows must still be found."""
        afas = _open(sess, "declaraties_async.html")
        settle(sess.page)
        rows = afas.read_grid_rows()
        found = find_thuiswerkdag(rows, TARGET, sess.cfg.thuiswerkdag_labels)
        assert found is not None and found.date == TARGET

    def test_booking_date_still_not_matched_when_async(self, sess):
        afas = _open(sess, "declaraties_async.html")
        settle(sess.page)
        rows = afas.read_grid_rows()
        # 16-08-2026 is only the booking date on that row.
        assert find_thuiswerkdag(rows, date(2026, 8, 16), sess.cfg.thuiswerkdag_labels) is None


class TestQuickFilter:
    def test_uses_the_soort_quick_filter(self, sess):
        afas = _open(sess, "declaraties_table.html")
        assert afas.filter_by_soort("Thuiswerkdag") is True
        box = sess.page.get_by_role("textbox", name="Snelfilter voor Soort declaratie")
        assert box.input_value() == "Thuiswerkdag"


class TestCreateFlow:
    def test_two_step_create_fills_date_and_submits(self, sess):
        afas = _open(sess, "thuiswerkdag_form.html")
        afas.click_nieuw()
        dialog = afas.wait_for_dialog()

        afas.fill_datum(dialog, TARGET)
        assert afas.read_datum(dialog) == TARGET
        # AFAS derives the amount; it must be read, never typed.
        assert afas.read_totaalbedrag(dialog) == "2,00"

        afas.click_button_in(dialog, RE_AANMAKEN, "Aanmaken (dialog)")
        settle(sess.page, quiet_ms=200)
        assert sess.page.locator("body").get_attribute("data-line-added") == "17-08-2026"

        afas.submit_verzameldeclaratie()
        settle(sess.page, quiet_ms=200)
        assert sess.page.locator("body").get_attribute("data-submitted") == "1"

    def test_amount_field_is_disabled(self, sess):
        """Guards the 'never enter the amount' requirement."""
        afas = _open(sess, "thuiswerkdag_form.html")
        afas.click_nieuw()
        afas.wait_for_dialog()
        assert sess.page.locator("#bedrag").is_disabled()


class TestFailSafe:
    def test_missing_nieuw_button_raises_and_submits_nothing(self, sess):
        afas = _open(sess, "declaraties_table.html")
        with pytest.raises(RuntimeError, match="Nieuw"):
            afas.click_nieuw()

    def test_missing_datum_field_raises(self, sess):
        afas = _open(sess, "declaraties_table.html")
        with pytest.raises(RuntimeError, match="Datum"):
            afas._datum_field(sess.page)

    def test_wrong_date_in_field_aborts_before_submitting(self, sess):
        """If the field does not hold the requested date, nothing is submitted."""
        afas = _open(sess, "thuiswerkdag_form.html")
        afas.click_nieuw()
        dialog = afas.wait_for_dialog()
        sess.page.locator("#datum").fill("18-08-2026")
        assert afas.read_datum(dialog) != TARGET
        assert sess.page.locator("body").get_attribute("data-submitted") is None


class TestAfasRefusal:
    """AFAS refusing access must be reported as a refusal, not as DOM drift.

    On 02-09-2026 the account lost rights on the page the 'Nieuw' action opens
    (``/aanmaken-declaratie-ess-incl-autorisatie-prs/thuiswerkdag``). The tool
    reported "The dialog appeared but has no 'Datum' field", which reads like a
    selector problem and sends you looking in entirely the wrong place.
    """

    def test_refusal_dialog_is_reported_as_a_refusal(self, sess):
        afas = _open(sess, "thuiswerkdag_notauthorized.html")
        afas.click_nieuw()

        with pytest.raises(RuntimeError) as excinfo:
            afas.wait_for_dialog()

        message = str(excinfo.value)
        assert "geen toegang" in message, "must quote what AFAS actually said"
        assert "C79BD2A377614BCB856B7AF9C00BBF3E" in message, "must carry the error id"
        assert "Datum" not in message, "must not blame a missing Datum field"

    def test_refusal_page_is_reported_before_clicking_anything(self, sess):
        """A full-page refusal must be caught on arrival, not chased into a click."""
        afas = _open(sess, "notauthorized_page.html")

        with pytest.raises(RuntimeError, match="niet geautoriseerd|geen toegang"):
            afas.assert_page_accessible()

    def test_a_healthy_page_is_not_mistaken_for_a_refusal(self, sess):
        """Guards against the detector firing on the normal page."""
        afas = _open(sess, "thuiswerkdag_form.html")
        afas.assert_page_accessible()  # must not raise
        afas.click_nieuw()
        assert afas.wait_for_dialog().locator("#datum").count() == 1
