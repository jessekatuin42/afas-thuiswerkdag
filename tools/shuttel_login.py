#!/usr/bin/env python3
"""One-time Shuttel login (authorization code + PKCE).

    python tools/shuttel_login.py

Why not just a password: Keycloak runs a separate Direct Grant Flow from the
Browser Flow, and this realm's direct flow rejects credentials that log in
perfectly well in a browser. No password will fix that, so we use the flow the
portal itself uses.

You log in once, in your own browser. What is stored afterwards is an
offline_access refresh token -- not your password -- and unattended runs use
that with no browser involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.shuttel import (  # noqa: E402
    REDIRECT_URI,
    ShuttelAuthError,
    ShuttelClient,
    ShuttelCredentials,
    TokenClient,
    TokenStore,
    authorize_url,
    extract_code,
    new_verifier,
)
from src.config import PROJECT_ROOT  # noqa: E402
from src.logging_util import error, log  # noqa: E402

TOKEN_PATH = PROJECT_ROOT / ".shuttel-token.json"


def main() -> int:
    verifier = new_verifier()
    url = authorize_url(verifier)

    print()
    print("  1. Open this URL in your browser and log in to Shuttel:")
    print()
    print(f"     {url}")
    print()
    print(f"  2. You will land on {REDIRECT_URI}?code=...")
    print("     That page shows a couple of lines of plain text. That is")
    print("     deliberate: it runs no JavaScript, so nothing swallows the")
    print("     code and it stays visible in the address bar.")
    print()
    print("     It shows exactly this, and nothing else:")
    print()
    print("         User-agent: *")
    print("         Disallow: /")
    print()
    print("  3. Paste that tab's FULL address-bar URL here and press Enter.")
    print()

    store = TokenStore(TOKEN_PATH)
    client = TokenClient(ShuttelCredentials(), transport=None, store=store)

    # Retry in-process: the verifier stays valid, so a mis-paste costs a
    # re-paste rather than a whole new login round.
    for attempt in range(3):
        try:
            pasted = input("  URL: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            error("Cancelled; nothing was stored.")
            return 1

        if not pasted:
            error("Nothing pasted.")
            continue

        try:
            code = extract_code(pasted)
        except ShuttelAuthError as exc:
            error(str(exc))
            if attempt < 2:
                print("\n  Try again with the /robots.txt tab.\n")
            continue

        try:
            log("Exchanging the authorization code")
            client.exchange_code(code, verifier)
            break
        except ShuttelAuthError as exc:
            error(str(exc))
            return 1
    else:
        error("Gave up after three attempts; nothing was stored.")
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
