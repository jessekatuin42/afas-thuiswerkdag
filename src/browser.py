"""Browser lifecycle: persistent profile, interactive login, diagnostics.

Nothing here ever prints or persists cookies, tokens or credentials. The
Playwright profile directory holds the session on disk; it is gitignored and
its contents are never read by this code.
"""

from __future__ import annotations

import re
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

from .config import Config
from .logging_util import log, warn

# Text/elements that mean "AFAS is asking who you are" rather than
# "here is your portal". Kept broad because AFAS environments differ in
# whether they use local accounts, SSO or a federated IdP.
_LOGIN_HINTS = re.compile(
    r"(inloggen|aanmelden|sign in|log in|wachtwoord|password|verifi|authenticat|"
    r"tweestaps|two-factor|verificatiecode|mfa)",
    re.IGNORECASE,
)


class BrowserSession:
    """A persistent-profile Chromium context pointed at AFAS InSite."""

    def __init__(self, cfg: Config, trace: bool = False):
        self.cfg = cfg
        self.trace = trace
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._trace_path: Path | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> Page:
        self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()
        launch_kwargs: dict = {
            "user_data_dir": str(self.cfg.profile_dir),
            "headless": self.cfg.headless,
            "viewport": {"width": 1440, "height": 900},
            "locale": "nl-NL",
            "timezone_id": "Europe/Amsterdam",
            "accept_downloads": False,
        }
        if self.cfg.chromium_executable:
            launch_kwargs["executable_path"] = self.cfg.chromium_executable
        if self.cfg.slow_mo_ms:
            launch_kwargs["slow_mo"] = self.cfg.slow_mo_ms

        try:
            self.context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:  # pragma: no cover - environment dependent
            self.stop()
            raise RuntimeError(
                f"Could not launch Chromium: {exc}\n"
                "Set AFAS_CHROMIUM=/path/to/chromium if auto-detection picked "
                "an unusable binary."
            ) from exc

        self.context.set_default_timeout(self.cfg.action_timeout_ms)
        self.context.set_default_navigation_timeout(self.cfg.nav_timeout_ms)

        if self.trace:
            self.context.tracing.start(screenshots=True, snapshots=True, sources=False)

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    def stop(self) -> None:
        try:
            if self.trace and self.context is not None:
                self._trace_path = self.cfg.artifacts_dir / (
                    f"trace-{datetime.now():%Y%m%d-%H%M%S}.zip"
                )
                self.context.tracing.stop(path=str(self._trace_path))
                log(f"Trace saved: {self._trace_path}")
        except Exception:
            pass
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self.context = None
        self.page = None

    # -- diagnostics -------------------------------------------------------

    def screenshot(self, label: str, when: date | None = None) -> str:
        """Save a full-page screenshot for troubleshooting.

        Screenshots can show personal data (your own declarations); they never
        show credentials, since they are only taken on portal pages.
        """
        if self.page is None:
            return ""
        stamp = (when or date.today()).isoformat()
        path = self.cfg.artifacts_dir / f"{label}-{stamp}-{datetime.now():%H%M%S}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception as exc:
            warn(f"Could not save screenshot: {exc}")
            return ""

    def dump_html(self, label: str) -> str:
        if self.page is None:
            return ""
        path = self.cfg.artifacts_dir / f"{label}-{datetime.now():%Y%m%d-%H%M%S}.html"
        try:
            path.write_text(self.page.content(), encoding="utf-8")
            return str(path)
        except Exception as exc:
            warn(f"Could not save HTML dump: {exc}")
            return ""

    # -- navigation / auth -------------------------------------------------

    def goto(self, url: str) -> None:
        assert self.page is not None
        self.page.goto(url, wait_until="domcontentloaded")
        settle(self.page)

    def _looks_logged_out(self) -> bool:
        """Heuristic: are we on an authentication screen?

        Two independent signals, either of which is enough:
          * we were redirected off the AFAS InSite host (SSO / IdP), or
          * the page offers a password field or an obvious login affordance.
        """
        assert self.page is not None
        page = self.page

        expected_host = urlparse(self.cfg.base_url).netloc.lower()
        current_host = urlparse(page.url).netloc.lower()
        if current_host and current_host != expected_host:
            return True

        try:
            if page.locator("input[type='password']").count() > 0:
                return True
        except Exception:
            pass

        # A login page is small and login-flavoured; the portal is neither.
        try:
            body = page.inner_text("body", timeout=5_000)
        except Exception:
            return False
        if len(body) < 1_500 and _LOGIN_HINTS.search(body):
            return True
        return False

    def login_if_needed(self) -> None:
        """Ensure an authenticated AFAS session, asking the human if not.

        Never handles credentials itself: it opens the real AFAS login flow and
        waits for the human to finish it, MFA included.
        """
        assert self.page is not None
        log("Checking AFAS authentication")
        self.goto(self.cfg.declarations_url)

        if not self._looks_logged_out():
            log("Existing AFAS session detected")
            return

        # Try credentials from .env first; fall back to a human if anything
        # about the flow is unfamiliar.
        if self._try_automated_login():
            log("Authenticated automatically")
            return

        if self.cfg.headless:
            raise RuntimeError(
                "Not authenticated and running headless, and automated login "
                "did not complete. Re-run without --headless to log in, or set "
                "AFAS_PASSWORD / AFAS_TOTP_SECRET in .env."
            )

        print(
            "\n"
            "  AFAS login required\n"
            "  --------------------------------------------------\n"
            "  A browser window is open. Please log in there\n"
            "  (including SSO / MFA if AFAS asks for it).\n"
            "  This script never sees your credentials.\n"
            "  Waiting...\n",
            file=sys.stderr,
        )
        self._wait_for_login()
        log("Authenticated; continuing")

    def _try_automated_login(self) -> bool:
        """Attempt a credentialed login. Never raises; never logs secrets."""
        from .auth import LoginAutomator, load_credentials
        from .config import PROJECT_ROOT

        creds = load_credentials(PROJECT_ROOT)
        if not creds.can_autologin:
            log("No stored credentials; interactive login required")
            return False

        log(f"Attempting automated login (have: {creds.describe()})")
        automator = LoginAutomator(
            self.page, creds, urlparse(self.cfg.base_url).netloc
        )
        try:
            if not automator.attempt():
                return False
        except Exception as exc:
            # Deliberately not including the exception's page content, which
            # could echo a field value.
            warn(f"Automated login did not complete ({type(exc).__name__})")
            return False

        try:
            self.goto(self.cfg.declarations_url)
        except PWTimeout:
            return False
        return not self._looks_logged_out()

    def _wait_for_login(self) -> None:
        """Block until the human is through the login flow, then re-land."""
        assert self.page is not None
        deadline = time.monotonic() + (self.cfg.login_timeout_ms / 1000)
        expected_host = urlparse(self.cfg.base_url).netloc.lower()

        while time.monotonic() < deadline:
            self.page.wait_for_timeout(2_000)
            try:
                host = urlparse(self.page.url).netloc.lower()
            except Exception:
                continue
            if host != expected_host:
                continue  # still at the IdP
            if not self._looks_logged_out():
                # Back on AFAS and authenticated — make sure we are on the
                # page we actually wanted.
                try:
                    self.goto(self.cfg.declarations_url)
                except PWTimeout:
                    pass
                if not self._looks_logged_out():
                    return
        raise RuntimeError(
            "Timed out waiting for login. Re-run the command and complete the "
            "AFAS login in the browser window."
        )

    def ensure_authenticated(self) -> None:
        """Re-check mid-run; AFAS sessions can expire between steps."""
        if self._looks_logged_out():
            warn("AFAS session expired mid-run")
            self.login_if_needed()


def settle(page: Page, quiet_ms: int = 1_200) -> None:
    """Wait for an AFAS page to stop loading data.

    AFAS InSite renders its lists asynchronously, so 'load' is not enough:
    wait for the network to go quiet, then for any spinner to disappear.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeout:
        pass
    try:
        spinner = page.locator(
            "[class*='loading' i], [class*='spinner' i], [role='progressbar']"
        ).first
        if spinner.count() > 0:
            spinner.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    page.wait_for_timeout(quiet_ms)


@contextmanager
def session(cfg: Config, trace: bool = False):
    s = BrowserSession(cfg, trace=trace)
    try:
        s.start()
        yield s
    finally:
        s.stop()
