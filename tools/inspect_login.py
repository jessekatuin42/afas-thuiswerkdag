#!/usr/bin/env python3
"""Development helper: capture the AFAS login page *structure*.

Enters nothing and submits nothing — it only records which fields and buttons
exist, so the automated login can target them. Values are never read.

    python tools/inspect_login.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.nixshim import ensure_native_libs

ensure_native_libs()

from src.browser import session, settle  # noqa: E402
from src.config import load_config  # noqa: E402
from src.logging_util import log  # noqa: E402


def main() -> int:
    cfg = load_config()
    outdir = cfg.artifacts_dir / "inspect"
    outdir.mkdir(parents=True, exist_ok=True)

    with session(cfg) as sess:
        page = sess.page
        sess.goto(cfg.base_url)
        settle(page)
        log(f"landed on: {page.url}")

        (outdir / "login.url.txt").write_text(page.url + "\n", encoding="utf-8")
        (outdir / "login.aria.txt").write_text(
            page.locator("body").aria_snapshot(), encoding="utf-8"
        )

        # Field inventory: names/types/labels only — never values.
        lines: list[str] = [f"URL: {page.url}", ""]
        inputs = page.locator("input, button, a[role='button']")
        for i in range(min(inputs.count(), 60)):
            el = inputs.nth(i)
            try:
                info = el.evaluate(
                    """e => ({
                        tag: e.tagName,
                        type: e.getAttribute('type'),
                        name: e.getAttribute('name'),
                        id: e.getAttribute('id'),
                        placeholder: e.getAttribute('placeholder'),
                        aria: e.getAttribute('aria-label'),
                        autocomplete: e.getAttribute('autocomplete'),
                        text: (e.innerText || e.value_placeholder || '').slice(0, 40),
                        visible: !!(e.offsetWidth || e.offsetHeight)
                    })"""
                )
            except Exception:
                continue
            if info.get("visible"):
                lines.append(str(info))
        (outdir / "login.fields.txt").write_text("\n".join(lines), encoding="utf-8")
        for line in lines:
            log(line)

        sess.screenshot("inspect-login")

    print(f"\nWritten to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
