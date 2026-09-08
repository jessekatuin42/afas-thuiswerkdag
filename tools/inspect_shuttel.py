#!/usr/bin/env python3
"""Development helper: capture the real Shuttel API so payloads are not guessed.

Reads a set of read-only endpoints from your Shuttel account and saves each
response to artifacts/inspect/shuttel/. It never POSTs, so it cannot create a
declaration.

    python tools/inspect_shuttel.py

Needs SHUTTEL_USERNAME and SHUTTEL_PASSWORD in .env. The portal authenticates
through Keycloak; this uses the password grant, which is also what unattended
runs would need. If the shuttel-portal client has Direct Access Grants
disabled, this fails with a clear message and the credentials are not at fault.

> artifacts/ holds your own account data -- addresses, routes, declarations.
> It is gitignored. Redact before sharing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.shuttel import (  # noqa: E402
    INSPECT_ENDPOINTS,
    ShuttelAuthError,
    ShuttelClient,
    ShuttelCredentials,
    TokenClient,
)
from src.config import PROJECT_ROOT  # noqa: E402
from src.logging_util import error, log  # noqa: E402


def load_credentials() -> ShuttelCredentials:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except ImportError:  # pragma: no cover
            pass
    return ShuttelCredentials(
        username=os.environ.get("SHUTTEL_USERNAME", "").strip(),
        password=os.environ.get("SHUTTEL_PASSWORD", "").strip(),
    )


def main() -> int:
    creds = load_credentials()
    if not creds.complete:
        error(
            "Shuttel credentials are not configured.\n"
            "Add to .env:\n"
            "  SHUTTEL_USERNAME=your.shuttel.login\n"
            "  SHUTTEL_PASSWORD=your-shuttel-password"
        )
        return 3

    outdir = PROJECT_ROOT / "artifacts" / "inspect" / "shuttel"
    outdir.mkdir(parents=True, exist_ok=True)

    log("Authenticating with Shuttel (Keycloak password grant)")
    client = ShuttelClient(TokenClient(creds))

    try:
        results = client.inspect()
    except ShuttelAuthError as exc:
        error(str(exc))
        return 1

    print()
    for path, result in results.items():
        name = path.strip("/").replace("/", "_") + ".json"
        (outdir / name).write_text(
            json.dumps(result.get("body"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        status = result.get("status")
        size = len(json.dumps(result.get("body") or ""))
        print(f"  {str(status):>4}  {path:<45} -> {name} ({size} bytes)")

    print()
    print(f"Saved to {outdir}")
    print("These files contain your own account data. artifacts/ is gitignored.")
    return 0 if any(r.get("status") == 200 for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
