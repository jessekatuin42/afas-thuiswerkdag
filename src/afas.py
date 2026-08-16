"""AFAS InSite page interactions.

Written against a real AFAS InSite portal, captured with
``tools/inspect_afas.py``. What is actually there:

*Mijn declaraties* (``/mijn-declaraties-prs/overzicht``) renders an ARIA grid
whose header row is::

    Datum boeking │ Datum │ Status │ Soort declaratie │ Omschrijving │
    Aantal te declareren │ Totaalbedrag │ Bijlage

Below the header sits a *Snelfilter* row: one textbox per column, labelled
``Snelfilter voor <column>``. Filtering there is far more reliable than paging
through 57+ rows, so detection filters on 'Soort declaratie' and reads the
'Datum' column of what comes back.

*Aanmaken verzameldeclaratie thuiswerkdag* has a ``Nieuw`` button and its own
``Aanmaken`` button. Pressing ``Nieuw`` opens a modal dialog containing:

    textbox "Datum"                        ← the only field to fill
    button  "Selecteer een datum"          ← date picker (not used)
    textbox "Totaalbedrag" [disabled] 2,00 ← derived by AFAS, never typed
    button  "Aanmaken"                     ← adds the line to the page grid

So creation is a **two-step** flow: the dialog's *Aanmaken* adds a line, then
the page's *Aanmaken* submits the collected declaration. Both are needed.

Selector strategy, in the project's preferred order: accessible roles, labels,
visible text, stable attributes, semantic HTML, then narrow CSS. No positional
selectors.
"""

from __future__ import annotations

import re
from datetime import date

from playwright.sync_api import Locator, TimeoutError as PWTimeout

from . import dates
from .browser import BrowserSession, settle
from .config import Config
from .detection import (
    COL_SOORT,
    GridRow,
    find_thuiswerkdag,
    find_thuiswerkdag_in_texts,
    resolve_columns,
    summarize_thuiswerkdagen,
)
from .logging_util import log, warn
from .models import Declaration, Outcome, Result

RE_NIEUW = re.compile(r"^\s*nieuw\s*$", re.IGNORECASE)
RE_AANMAKEN = re.compile(r"^\s*aanmaken\s*$", re.IGNORECASE)
RE_DATUM = re.compile(r"^\s*datum\s*$", re.IGNORECASE)
RE_SNELFILTER_SOORT = re.compile(r"snelfilter voor soort", re.IGNORECASE)

MAX_ROWS = 400
MAX_PAGES = 20
VERIFY_ATTEMPTS = 3


class AfasInSite:
    """High-level operations against AFAS InSite."""

    def __init__(self, sess: BrowserSession, cfg: Config):
        self.sess = sess
        self.cfg = cfg

    @property
    def page(self):
        assert self.sess.page is not None
        return self.sess.page

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self, target: date, dry_run: bool = False) -> Result:
        iso = dates.to_iso(target)

        self.open_declarations()
        existing = self.find_existing_thuiswerkdag(target)

        if existing is not None:
            log(f"Existing Thuiswerkdag found for {iso}")
            log("Skipping creation")
            return Result(
                outcome=Outcome.WOULD_SKIP if dry_run else Outcome.ALREADY_EXISTS,
                target_date=target,
                existing=existing,
            )

        log(f"No declaration found for {iso}")

        if dry_run:
            log("DRY RUN — stopping before 'Aanmaken'")
            return Result(outcome=Outcome.WOULD_CREATE, target_date=target)

        self.create_thuiswerkdag(target)

        log("Verifying creation")
        verified = self.verify_thuiswerkdag(target)
        if verified is not None:
            log(f"SUCCESS: Thuiswerkdag created for {iso}")
            return Result(
                outcome=Outcome.CREATED,
                target_date=target,
                existing=verified,
                amount=verified.amount or self.cfg.default_amount,
            )

        warn("AFAS did not confirm the new declaration")
        shot = self.sess.screenshot("unverified", target)
        return Result(
            outcome=Outcome.UNVERIFIED,
            target_date=target,
            messages=["Submission sent but not found on 'Mijn declaraties'."],
            artifacts=[p for p in (shot,) if p],
        )

    # ------------------------------------------------------------------
    # Reading declarations
    # ------------------------------------------------------------------

    def open_declarations(self) -> None:
        log("Opening Mijn declaraties")
        self.sess.goto(self.cfg.declarations_url)
        self.sess.ensure_authenticated()
        settle(self.page)

    def find_existing_thuiswerkdag(self, target: date) -> Declaration | None:
        log("Checking existing declarations")

        self.filter_by_soort("Thuiswerkdag")
        rows = self.read_grid_rows()

        if rows:
            known = summarize_thuiswerkdagen(rows, self.cfg.thuiswerkdag_labels)
            log(f"Scanned {len(rows)} row(s); {len(known)} Thuiswerkdag")
            return find_thuiswerkdag(rows, target, self.cfg.thuiswerkdag_labels)

        # Columns unresolvable — fall back to whole-row text, which is stricter
        # about ambiguity (see detection.find_thuiswerkdag_in_texts).
        warn("Could not read the declarations grid; using text fallback")
        texts = self.read_row_texts()
        log(f"Scanned {len(texts)} row(s) as text")
        return find_thuiswerkdag_in_texts(texts, target, self.cfg.thuiswerkdag_labels)

    def grid(self) -> Locator:
        """The declarations grid, preferring the one AFAS names."""
        named = self.page.get_by_role("grid", name=re.compile("declaraties", re.I))
        try:
            if named.count() > 0:
                return named.first
        except Exception:
            pass
        for role in ("grid", "table"):
            loc = self.page.get_by_role(role)
            try:
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return self.page.locator("table").first

    def read_headers(self) -> list[str]:
        try:
            headers = self.grid().get_by_role("columnheader")
            return [
                headers.nth(i).inner_text(timeout=2_000).strip()
                for i in range(min(headers.count(), 30))
            ]
        except Exception:
            return []

    def read_grid_rows(self) -> list[GridRow]:
        """Structured rows, resolved through the column headers.

        Returns [] when the headers cannot be resolved, so the caller can fall
        back rather than silently mis-reading columns.
        """
        headers = self.read_headers()
        if not headers:
            return []
        columns = resolve_columns(headers)
        if "datum" not in columns:
            warn("Grid has no 'Datum' column; cannot read it safely")
            return []
        log(f"Grid columns: {', '.join(sorted(columns))}")

        out: list[GridRow] = []
        for page_index in range(1, MAX_PAGES + 1):
            out.extend(self._rows_on_current_page(columns))
            if not self.go_to_next_page():
                break
            log(f"Following pagination to page {page_index + 1}")
            settle(self.page)
        return out

    def _rows_on_current_page(self, columns: dict[str, int]) -> list[GridRow]:
        rows: list[GridRow] = []
        try:
            row_loc = self.grid().get_by_role("row")
            count = min(row_loc.count(), MAX_ROWS)
        except Exception:
            return rows

        for i in range(count):
            row = row_loc.nth(i)
            try:
                cells = row.get_by_role("gridcell")
                n = cells.count()
                if n == 0:
                    continue  # header or filter row
                texts = [
                    cells.nth(j).inner_text(timeout=2_000).strip() for j in range(n)
                ]
            except Exception:
                continue
            if any(t for t in texts):
                rows.append(GridRow(cells=texts, columns=columns))
        return rows

    def read_row_texts(self) -> list[str]:
        """Whole-row text, for the unstructured fallback path."""
        texts: list[str] = []
        seen: set[str] = set()
        for locator in (
            self.page.get_by_role("row"),
            self.page.locator("table tbody tr"),
        ):
            try:
                count = min(locator.count(), MAX_ROWS)
            except Exception:
                continue
            for i in range(count):
                try:
                    text = locator.nth(i).inner_text(timeout=2_000)
                except Exception:
                    continue
                key = re.sub(r"\s+", " ", text).strip()
                if key and key not in seen:
                    seen.add(key)
                    texts.append(key)
            if texts:
                break
        return texts

    def filter_by_soort(self, soort: str) -> bool:
        """Type into the grid's own 'Snelfilter voor Soort declaratie' box.

        Narrowing server-side is what makes detection reliable without paging
        through every declaration. Failure is non-fatal: the caller still walks
        pagination.
        """
        for loc in (
            self.page.get_by_role("textbox", name=RE_SNELFILTER_SOORT),
            self.page.get_by_label(RE_SNELFILTER_SOORT),
        ):
            try:
                if loc.count() == 0:
                    continue
                box = loc.first
                if not box.is_visible():
                    continue
                box.fill(soort)
                box.press("Enter")
                settle(self.page)
                log(f"Filtered grid on Soort declaratie = {soort}")
                return True
            except Exception:
                continue
        log("No Soort quick-filter available; reading the unfiltered grid")
        return False

    def go_to_next_page(self) -> bool:
        for loc in (
            self.page.get_by_role("button", name=re.compile(r"volgende|next", re.I)),
            self.page.get_by_role("link", name=re.compile(r"volgende|next", re.I)),
        ):
            try:
                if loc.count() == 0:
                    continue
                btn = loc.first
                if not btn.is_visible() or not btn.is_enabled():
                    continue
                if (btn.get_attribute("aria-disabled") or "").lower() == "true":
                    continue
                btn.click()
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Creating a declaration
    # ------------------------------------------------------------------

    def create_thuiswerkdag(self, target: date) -> None:
        log("Opening Thuiswerkdag declaration form")
        self.sess.goto(self.cfg.thuiswerkdag_url)
        self.sess.ensure_authenticated()
        settle(self.page)

        self.click_nieuw()
        dialog = self.wait_for_dialog()

        log(f"Filling date: {dates.to_iso(target)}")
        self.fill_datum(dialog, target)

        # Refuse to submit unless the field really holds the requested date.
        actual = self.read_datum(dialog)
        if actual != target:
            self.sess.screenshot("date-mismatch", target)
            raise RuntimeError(
                "Date field verification failed: expected "
                f"{dates.to_dutch(target)}, field holds "
                f"{dates.to_dutch(actual) if actual else '(empty/unreadable)'}. "
                "Nothing was submitted."
            )
        log("Date field verified")

        # Step 1 of 2 — adds the line to the page's Thuiswerkdagen grid.
        log("Adding declaration line")
        self.click_button_in(dialog, RE_AANMAKEN, "Aanmaken (dialog)")
        settle(self.page, quiet_ms=2_000)

        # Step 2 of 2 — submits the collected declaration.
        log("Submitting declaration")
        self.submit_verzameldeclaratie()
        settle(self.page, quiet_ms=2_500)

    def click_nieuw(self) -> None:
        for loc in (
            self.page.get_by_role("button", name=RE_NIEUW),
            self.page.get_by_role("link", name=RE_NIEUW),
            self.page.get_by_role("button", name=re.compile(r"nieuw", re.I)),
        ):
            try:
                if loc.count() == 0:
                    continue
                btn = loc.first
                btn.wait_for(state="visible", timeout=5_000)
                btn.click()
                log("Clicked 'Nieuw'")
                settle(self.page)
                return
            except Exception:
                continue
        self.sess.screenshot("no-nieuw-button")
        raise RuntimeError("Could not find the 'Nieuw' control on the Thuiswerkdag page.")

    def wait_for_dialog(self) -> Locator:
        """The modal opened by 'Nieuw', scoped so page-level buttons can't match."""
        dialog = self.page.get_by_role("dialog").last
        try:
            dialog.wait_for(state="visible", timeout=15_000)
        except PWTimeout:
            self.sess.screenshot("no-dialog")
            raise RuntimeError("The 'Nieuw' dialog did not appear.") from None

        for _ in range(10):
            try:
                if dialog.get_by_label(RE_DATUM).count() > 0:
                    return dialog
                if dialog.get_by_role("textbox", name=RE_DATUM).count() > 0:
                    return dialog
            except Exception:
                pass
            self.page.wait_for_timeout(1_000)

        self.sess.screenshot("no-datum-field")
        raise RuntimeError("The dialog appeared but has no 'Datum' field.")

    def _datum_field(self, scope) -> Locator:
        for loc in (
            scope.get_by_role("textbox", name=RE_DATUM),
            scope.get_by_label(RE_DATUM),
            scope.locator("input[type='date']"),
            scope.locator("input[name*='datum' i], input[id*='datum' i]"),
        ):
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                continue
        raise RuntimeError("Could not locate the 'Datum' field.")

    def fill_datum(self, scope, target: date) -> None:
        field = self._datum_field(scope)

        # AFAS's Datum is a free-text box (with a picker beside it), so it wants
        # the Dutch display format. A native date input would want ISO.
        input_type = (field.get_attribute("type") or "").lower()
        value = dates.to_iso(target) if input_type == "date" else dates.to_dutch(target)

        try:
            field.click()
            field.fill("")
        except Exception:
            pass
        field.fill(value)
        # Commit: AFAS validates and reformats on blur.
        try:
            field.press("Tab")
        except Exception:
            pass
        settle(self.page, quiet_ms=1_000)

    def read_datum(self, scope) -> date | None:
        try:
            field = self._datum_field(scope)
        except RuntimeError:
            return None
        for getter in (
            lambda: field.input_value(timeout=3_000),
            lambda: field.get_attribute("value") or "",
        ):
            try:
                raw = getter()
            except Exception:
                continue
            found = dates.extract_dates(raw or "")
            if len(found) == 1:
                return next(iter(found))
            if found:
                return None  # ambiguous — refuse to submit
        return None

    def read_totaalbedrag(self, scope) -> str:
        """The amount AFAS derived. Read-only; never typed into."""
        try:
            box = scope.get_by_role("textbox", name=re.compile("totaalbedrag", re.I))
            if box.count() > 0:
                return (box.first.input_value(timeout=2_000) or "").strip()
        except Exception:
            pass
        return ""

    def click_button_in(self, scope, pattern: re.Pattern, label: str) -> None:
        for loc in (
            scope.get_by_role("button", name=pattern),
            scope.get_by_role("link", name=pattern),
        ):
            try:
                if loc.count() == 0:
                    continue
                btn = loc.first
                btn.wait_for(state="visible", timeout=5_000)
                btn.click()
                log(f"Clicked '{label}'")
                return
            except Exception:
                continue
        self.sess.screenshot(f"no-button-{label.replace(' ', '-').lower()}")
        raise RuntimeError(f"Could not find the '{label}' button.")

    def submit_verzameldeclaratie(self) -> None:
        """Click the page-level 'Aanmaken', not the dialog's.

        Scoped to <main> so a lingering dialog button cannot be clicked twice.
        """
        main = self.page.get_by_role("main")
        scope = main if main.count() > 0 else self.page
        self.click_button_in(scope, RE_AANMAKEN, "Aanmaken (page)")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_thuiswerkdag(self, target: date) -> Declaration | None:
        """Re-read AFAS and confirm the declaration is really there.

        Read-only, so retrying is safe — unlike the submission itself, which is
        never retried.
        """
        for attempt in range(1, VERIFY_ATTEMPTS + 1):
            try:
                self.open_declarations()
                found = self.find_existing_thuiswerkdag(target)
                if found is not None:
                    return found
            except PWTimeout:
                pass
            if attempt < VERIFY_ATTEMPTS:
                log(f"Not visible yet; re-checking ({attempt}/{VERIFY_ATTEMPTS})")
                self.page.wait_for_timeout(3_000)
        return None
