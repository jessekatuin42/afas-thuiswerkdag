#!/usr/bin/env python3
"""One-time Shuttel login (authorization code + PKCE).

    python tools/shuttel_login.py            # opens a browser, watches for the redirect
    python tools/shuttel_login.py --manual   # prints a URL, you paste the result back

Why not a password: Keycloak runs a separate Direct Grant Flow from the Browser
Flow, and this realm's direct flow rejects credentials that log in perfectly
well in a browser. No password will fix that, so we use the flow the portal
itself uses.

You log in once. What is stored afterwards is an offline_access refresh token
-- not your password -- and later runs use that with no browser at all.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.nixshim import ensure_native_libs  # noqa: E402

ensure_native_libs()

from src.adapters.shuttel import (  # noqa: E402
    REDIRECT_URI,
    ShuttelAuthError,
    ShuttelClient,
    ShuttelCredentials,
    TokenClient,
    TokenStore,
    authorize_url,
    extract_code,
    is_callback,
    new_verifier,
)
from src.config import PROJECT_ROOT, _detect_chromium  # noqa: E402
from src.logging_util import error, log  # noqa: E402

TOKEN_PATH = PROJECT_ROOT / ".shuttel-token.json"
#: Its own profile: the AFAS session is expensive to recreate and must not be
#: disturbed by an unrelated login.
PROFILE_DIR = PROJECT_ROOT / ".shuttel-browser-profile"
LOGIN_TIMEOUT_S = 300


def wait_for_redirect(url: str) -> str:
    """Open a real browser and return the callback URL once it arrives.

    The human logs in; this only watches the address bar. Nothing is typed for
    them and no field is read.
    """
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    launch: dict = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": False,
        "viewport": {"width": 1100, "height": 900},
        "locale": "nl-NL",
    }
    chromium = _detect_chromium()
    if chromium:
        launch["executable_path"] = chromium

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(**launch)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        log("Browser open. Log in there; this is only watching the address bar.")
        deadline = time.monotonic() + LOGIN_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                try:
                    current = page.url
                except Exception:
                    break                      # window closed
                if is_callback(current):
                    return current
                page.wait_for_timeout(400)
        finally:
            try:
                context.close()
            except Exception:
                pass

    raise ShuttelAuthError(
        f"No redirect within {LOGIN_TIMEOUT_S}s. Nothing was stored."
    )


def prompt_for_redirect(url: str, state: str) -> str:
    print()
    print("  1. Open this URL in your browser and log in:")
    print()
    print(f"     {url}")
    print()
    print(f"  2. You land on {REDIRECT_URI}?code=...  It shows exactly:")
    print()
    print("         User-agent: *")
    print("         Disallow: /")
    print()
    print("  3. Paste that tab's FULL address-bar URL here.")
    print(f"     (it must carry state={state})")
    print()
    try:
        return input("  URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise ShuttelAuthError("Cancelled; nothing was stored.") from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", action="store_true",
                    help="print a URL and read the result back, instead of "
                         "opening a browser")
    args = ap.parse_args(argv)

    verifier = new_verifier()
    state = secrets.token_urlsafe(16)
    url = authorize_url(verifier, state=state)

    store = TokenStore(TOKEN_PATH)
    client = TokenClient(ShuttelCredentials(), store=store)

    try:
        landed = (prompt_for_redirect(url, state) if args.manual
                  else wait_for_redirect(url))
        code = extract_code(landed, expected_state=state)
        log("Exchanging the authorization code")
        client.exchange_code(code, verifier)
    except ShuttelAuthError as exc:
        error(str(exc))
        return 1
    except Exception as exc:
        error(f"{type(exc).__name__}: {exc}")
        return 1

    log(f"Stored a refresh token in {TOKEN_PATH.name} (chmod 600)")

    log("Verifying by reading your profile")
    result = ShuttelClient(client).get("/api/v1/profile/")
    if result.get("status") != 200:
        error(f"Token obtained, but /api/v1/profile/ answered {result.get('status')}.")
        return 1

    print()
    print("  Logged in. Now run:  python tools/inspect_shuttel.py")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
