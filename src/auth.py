"""Credential loading and automated AFAS login (including TOTP 2FA).

Design rules, all enforced below:

* Credentials come from ``.env`` (gitignored) or the environment — never from
  source, never from the command line (which would land in shell history).
* Values are never logged, printed, screenshotted or written to artifacts.
  ``Credentials`` overrides ``__repr__`` so an accidental ``print`` or a stack
  trace cannot leak it.
* Anything missing simply falls back to you typing it in the browser. Automated
  login is a convenience, never a requirement.
* The login page is driven as a small state machine, because AFAS splits login
  across several screens (account → password → 2FA) and the exact sequence
  varies with what the profile already remembers.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .logging_util import log, warn

if TYPE_CHECKING:  # keeps credential loading importable without Playwright
    from playwright.sync_api import Page

# Field identification. AFAS's IdP uses stable ids (#Password, #btnSubmit);
# the rest are deliberately broad so a relabelled 2FA screen still resolves.
_USERNAME_SEL = (
    "input[type='email'], input[name='Username' i], input[id='Username' i], "
    "input[autocomplete='username']"
)
_PASSWORD_SEL = "input[type='password']"
_OTP_SEL = (
    "input[autocomplete='one-time-code'], input[name*='code' i], "
    "input[id*='code' i], input[name*='token' i], input[id*='token' i], "
    "input[name*='otp' i], input[id*='otp' i], input[name*='verification' i]"
)
_SUBMIT_SEL = "#btnSubmit, button[type='submit'], input[type='submit']"

_TOTP_RE = re.compile(r"^[A-Z2-7 ]+=*$", re.IGNORECASE)


@dataclass(frozen=True)
class Credentials:
    username: str = ""
    password: str = ""
    totp_secret: str = ""

    # Make leaking hard: repr/str never reveal values.
    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"Credentials(username={'set' if self.username else 'unset'}, "
            f"password={'set' if self.password else 'unset'}, "
            f"totp_secret={'set' if self.totp_secret else 'unset'})"
        )

    __str__ = __repr__

    @property
    def can_autologin(self) -> bool:
        return bool(self.password)

    def describe(self) -> str:
        """Safe, value-free summary for logs."""
        have = [
            name
            for name, val in (
                ("username", self.username),
                ("password", self.password),
                ("TOTP secret", self.totp_secret),
            )
            if val
        ]
        return ", ".join(have) if have else "none"


def load_credentials(project_root: Path) -> Credentials:
    """Read credentials from ``.env`` then the process environment."""
    env_path = project_root / ".env"
    if env_path.exists():
        _warn_on_loose_permissions(env_path)
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except ImportError:  # pragma: no cover
            warn("python-dotenv not installed; .env ignored")

    creds = Credentials(
        username=os.environ.get("AFAS_USERNAME", "").strip(),
        password=os.environ.get("AFAS_PASSWORD", "").strip(),
        totp_secret=os.environ.get("AFAS_TOTP_SECRET", "").replace(" ", "").strip(),
    )
    if creds.totp_secret and not _TOTP_RE.match(creds.totp_secret):
        warn(
            "AFAS_TOTP_SECRET does not look like a base32 secret. It must be the "
            "setup key behind the QR code, not the 6-digit code."
        )
    return creds


def _warn_on_loose_permissions(path: Path) -> None:
    try:
        mode = path.stat().st_mode & 0o077
    except OSError:  # pragma: no cover
        return
    if mode:
        warn(f"{path.name} is readable by other users — run: chmod 600 {path}")


def totp_now(secret: str) -> str:
    """Current 6-digit TOTP code. The code is never logged."""
    import pyotp

    return pyotp.TOTP(secret).now()


def seconds_until_next_totp() -> float:
    return 30.0 - (time.time() % 30)


class LoginAutomator:
    """Drives the AFAS IdP screens using whatever credentials are available."""

    MAX_STEPS = 12

    def __init__(self, page: "Page", creds: Credentials, expected_host: str):
        self.page = page
        self.creds = creds
        self.expected_host = expected_host.lower()
        self._used_totp: set[str] = set()

    def _visible(self, selector: str):
        loc = self.page.locator(selector)
        try:
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:
            return None
        return None

    def _submit(self) -> None:
        btn = self._visible(_SUBMIT_SEL)
        if btn is not None:
            btn.click()
        else:
            self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(2_500)

    def on_afas(self) -> bool:
        try:
            return urlparse(self.page.url).netloc.lower() == self.expected_host
        except Exception:
            return False

    def attempt(self) -> bool:
        """Work through the login screens. True if we ended up back on AFAS.

        Returns False (rather than raising) whenever a screen needs a human —
        an unknown challenge, a missing credential, a passkey prompt — so the
        caller can fall back to interactive login.
        """
        for step in range(self.MAX_STEPS):
            self.page.wait_for_timeout(1_200)

            if self.on_afas():
                return True

            # 2FA first: if a code box is on screen it is the active challenge.
            otp = self._visible(_OTP_SEL)
            if otp is not None:
                if not self.creds.totp_secret:
                    warn("AFAS is asking for a 2FA code but AFAS_TOTP_SECRET is not set")
                    return False
                if not self._fill_totp(otp):
                    return False
                continue

            pwd = self._visible(_PASSWORD_SEL)
            if pwd is not None:
                if not self.creds.password:
                    warn("AFAS is asking for a password but AFAS_PASSWORD is not set")
                    return False
                user = self._visible(_USERNAME_SEL)
                if user is not None and self.creds.username and not user.input_value():
                    user.fill(self.creds.username)
                log("Submitting password")
                pwd.fill(self.creds.password)
                self._submit()
                continue

            user = self._visible(_USERNAME_SEL)
            if user is not None:
                if not self.creds.username:
                    warn("AFAS is asking for a username but AFAS_USERNAME is not set")
                    return False
                log("Submitting username")
                user.fill(self.creds.username)
                self._submit()
                continue

            # Nothing recognisable — could be a passkey prompt, a consent
            # screen or an error. Let a human look at it.
            log(f"Unrecognised login screen at step {step + 1}")
            return False

        warn("Login automation ran out of steps")
        return False

    def _fill_totp(self, field) -> bool:
        """Enter a fresh TOTP code, never reusing one already submitted."""
        code = totp_now(self.creds.totp_secret)
        if code in self._used_totp:
            wait = seconds_until_next_totp() + 1
            log(f"Waiting {wait:.0f}s for a fresh 2FA code")
            self.page.wait_for_timeout(int(wait * 1000))
            code = totp_now(self.creds.totp_secret)
        # Avoid submitting a code about to expire mid-flight.
        if seconds_until_next_totp() < 3:
            self.page.wait_for_timeout(3_500)
            code = totp_now(self.creds.totp_secret)

        self._used_totp.add(code)
        log("Submitting 2FA code")
        try:
            field.fill(code)
        except Exception:
            # Some 2FA screens use one box per digit.
            boxes = self.page.locator(_OTP_SEL)
            if boxes.count() >= len(code):
                for i, ch in enumerate(code):
                    boxes.nth(i).fill(ch)
            else:
                warn("Could not enter the 2FA code")
                return False
        self._submit()
        return True
