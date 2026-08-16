#!/usr/bin/env python3
"""Development helper: probe the real 'Mijn declaraties' overview grid.

Read-only. Confirms the grid's column layout and exercises the per-column
'Snelfilter' inputs, which is how duplicate detection narrows the list.

    python tools/inspect_overview.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.nixshim import ensure_native_libs

ensure_native_libs()

from src.browser import session, settle  # noqa: E402
from src.config import load_config  # noqa: E402
from src.logging_util import log  # noqa: E402


def dump_rows(page, limit: int = 12) -> list[list[str]]:
    """Return each row as its list of cell texts."""
    out: list[list[str]] = []
    rows = page.get_by_role("row")
    for i in range(min(rows.count(), limit)):
        row = rows.nth(i)
        cells = row.get_by_role("gridcell")
        try:
            n = cells.count()
        except Exception:
            n = 0
        if n == 0:
            try:
                out.append(["<header> " + row.inner_text(timeout=1500).replace("\n", " | ")])
            except Exception:
                pass
            continue
        out.append([cells.nth(j).inner_text(timeout=1500).strip() for j in range(n)])
    return out


def main() -> int:
    cfg = load_config()
    outdir = cfg.artifacts_dir / "inspect"
    outdir.mkdir(parents=True, exist_ok=True)

    with session(cfg) as sess:
        page = sess.page
        sess.login_if_needed()
        sess.goto(cfg.declarations_url)
        settle(page)

        (outdir / "overview.url.txt").write_text(page.url + "\n", encoding="utf-8")
        (outdir / "overview.aria.txt").write_text(
            page.locator("body").aria_snapshot(), encoding="utf-8"
        )
        log(f"overview URL resolved to: {page.url}")

        before = dump_rows(page)
        (outdir / "overview.rows.json").write_text(
            json.dumps(before, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log(f"rows before filtering: {len(before)}")

        # Exercise the per-column quick filter on 'Soort declaratie'.
        soort = page.get_by_role("textbox", name="Snelfilter voor Soort declaratie")
        if soort.count() > 0:
            soort.first.fill("Thuiswerkdag")
            soort.first.press("Enter")
            settle(page)
            after = dump_rows(page, limit=20)
            (outdir / "overview.filtered.json").write_text(
                json.dumps(after, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            log(f"rows after Soort='Thuiswerkdag': {len(after)}")
        else:
            log("no 'Snelfilter voor Soort declaratie' textbox found")

        sess.screenshot("inspect-overview")

    print(f"\nWritten to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
