#!/usr/bin/env python3
"""Development helper: capture the real AFAS DOM so selectors are not guessed.

Opens a headed browser, waits for you to log in, then saves HTML + accessibility
snapshots of the declarations list, the Thuiswerkdag page, and (optionally) the
'+ nieuw' form. It never clicks 'Aanmaken'.

    python tools/inspect_afas.py [--open-form]

Output lands in artifacts/inspect/. Those files contain your own declaration
data — they are gitignored, and worth deleting when you are done.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.nixshim import ensure_native_libs

ensure_native_libs()

from src.browser import session, settle  # noqa: E402
from src.config import load_config  # noqa: E402
from src.logging_util import log  # noqa: E402


def capture(sess, name: str, outdir: Path) -> None:
    page = sess.page
    settle(page)
    (outdir / f"{name}.url.txt").write_text(page.url + "\n", encoding="utf-8")
    (outdir / f"{name}.html").write_text(page.content(), encoding="utf-8")
    try:
        (outdir / f"{name}.aria.txt").write_text(
            page.locator("body").aria_snapshot(), encoding="utf-8"
        )
    except Exception as exc:
        (outdir / f"{name}.aria.txt").write_text(f"(aria snapshot failed: {exc})", encoding="utf-8")
    try:
        (outdir / f"{name}.text.txt").write_text(
            page.inner_text("body"), encoding="utf-8"
        )
    except Exception:
        pass

    frames = [
        {"name": f.name, "url": f.url}
        for f in page.frames
        if f is not page.main_frame
    ]
    (outdir / f"{name}.frames.json").write_text(
        json.dumps(frames, indent=2), encoding="utf-8"
    )
    sess.screenshot(f"inspect-{name}")
    log(f"captured {name}: {len(page.content())} bytes html, {len(frames)} subframes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-form", action="store_true",
                    help="also click '+ nieuw' and capture the form (does not submit)")
    args = ap.parse_args()

    cfg = load_config()
    outdir = cfg.artifacts_dir / "inspect"
    outdir.mkdir(parents=True, exist_ok=True)

    with session(cfg) as sess:
        sess.login_if_needed()

        log("Capturing: mijn-declaraties")
        sess.goto(cfg.declarations_url)
        capture(sess, "declaraties", outdir)

        log("Capturing: verzameldeclaratie-thuiswerkdag")
        sess.goto(cfg.thuiswerkdag_url)
        capture(sess, "thuiswerkdag", outdir)

        if args.open_form:
            log("Looking for the '+ nieuw' control")
            page = sess.page
            nieuw = re.compile(r"nieuw", re.IGNORECASE)
            candidates = [
                page.get_by_role("button", name=nieuw),
                page.get_by_role("link", name=nieuw),
                page.get_by_role("menuitem", name=nieuw),
                page.get_by_text(nieuw),
            ]
            clicked = False
            for loc in candidates:
                try:
                    if loc.count() > 0:
                        loc.first.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                settle(sess.page)
                capture(sess, "nieuw-form", outdir)
            else:
                log("no '+ nieuw' control matched — see thuiswerkdag.aria.txt")

    print(f"\nSnapshots written to: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
