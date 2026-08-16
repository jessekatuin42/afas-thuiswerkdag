#!/usr/bin/env python3
"""Create an AFAS InSite 'Thuiswerkdag' declaration from the command line.

    python afas_thuiswerk.py --date 2026-08-17
    python afas_thuiswerk.py --today
    python afas_thuiswerk.py --yesterday
    python afas_thuiswerk.py --date 2026-08-17 --dry-run

Safety properties:
  * checks AFAS for an existing Thuiswerkdag on that date before creating;
  * never assumes a click succeeded — creation is verified against AFAS;
  * never retries an uncertain submission (that could duplicate);
  * never handles or stores your credentials.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.nixshim import ensure_native_libs

ensure_native_libs()

from src import dates  # noqa: E402
from src.afas import AfasInSite  # noqa: E402
from src.browser import session  # noqa: E402
from src.config import ConfigError, load_config  # noqa: E402
from src.logging_util import error, log, set_quiet, warn  # noqa: E402
from src.models import EXIT_FAILED, EXIT_USAGE, Outcome, Result  # noqa: E402


class _Parser(argparse.ArgumentParser):
    """Exits with EXIT_USAGE instead of argparse's default 2.

    Otherwise a usage error would be indistinguishable from EXIT_UNVERIFIED,
    which is the one exit code that means "check AFAS by hand".
    """

    def error(self, message: str):  # noqa: D102
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="afas_thuiswerk.py",
        description="Create an AFAS InSite Thuiswerkdag declaration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", metavar="YYYY-MM-DD",
                      help="the declaration date, e.g. 2026-08-17")
    when.add_argument("--today", action="store_true", help="use today's date")
    when.add_argument("--yesterday", action="store_true", help="use yesterday's date")

    p.add_argument("--dry-run", action="store_true",
                   help="check only; never click 'Aanmaken'")
    p.add_argument("--headless", action="store_true",
                   help="run without a visible browser (needs an existing session)")
    p.add_argument("--trace", action="store_true",
                   help="record a Playwright trace into artifacts/")
    p.add_argument("--quiet", action="store_true", help="suppress progress logging")
    return p


def resolve_date(args: argparse.Namespace) -> date:
    if args.today:
        return dates.today()
    if args.yesterday:
        return dates.yesterday()
    return dates.parse_iso(args.date)


def print_result(result: Result, dry_run: bool, amount: str) -> None:
    """Human-readable outcome block on stdout."""
    iso = dates.to_iso(result.target_date)
    out = print

    if dry_run:
        out("")
        out("AFAS Thuiswerkdag - DRY RUN")
        out("")
        out(f"Requested date: {iso}")
        if result.outcome is Outcome.WOULD_SKIP:
            out("Existing declaration: YES")
            if result.existing:
                out(f"  Found: {result.existing.summary()}")
            out("")
            out("No changes would be made.")
        else:
            out("Existing declaration: No")
            out("")
            out("Would create:")
            out("  Type: Thuiswerkdag")
            out(f"  Date: {iso}")
            out("")
            out("No changes were made.")
        out("")
        return

    if result.outcome is Outcome.ALREADY_EXISTS:
        out("")
        out("AFAS Thuiswerkdag")
        out(f"Date: {iso}")
        out("")
        out("Already exists.")
        if result.existing:
            out(f"  Found: {result.existing.summary()}")
        out("No new declaration was created.")
        out("")
        return

    if result.outcome is Outcome.CREATED:
        out("")
        out("Successfully created AFAS Thuiswerkdag declaration.")
        out("")
        out(f"Date: {iso}")
        out(f"Amount: {result.amount or amount}")
        out("Status: Created")
        out("")
        return

    if result.outcome is Outcome.UNVERIFIED:
        out("")
        out("WARNING: AFAS did not confirm creation.")
        out("")
        out("The script did not assume success.")
        out("Please check AFAS manually.")
        out(f"Date: {iso}")
        out("")
        for path in result.artifacts:
            out(f"Diagnostics: {path}")
        return

    out("")
    out("FAILED: no declaration was created.")
    out(f"Date: {iso}")
    for msg in result.messages:
        out(f"  {msg}")
    for path in result.artifacts:
        out(f"Diagnostics: {path}")
    out("")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_quiet(args.quiet)

    try:
        target = resolve_date(args)
    except dates.InvalidDateError as exc:
        error(str(exc))
        return EXIT_USAGE

    try:
        cfg = load_config().with_overrides(headless=args.headless or None)
    except ConfigError as exc:
        error(str(exc))
        return EXIT_USAGE

    log("Starting AFAS Thuiswerkdag automation")
    log(f"Requested date: {dates.to_iso(target)}")
    if args.dry_run:
        log("DRY RUN — 'Aanmaken' will never be clicked")

    result: Result
    with session(cfg, trace=args.trace) as sess:
        afas = AfasInSite(sess, cfg)
        try:
            sess.login_if_needed()
            result = afas.run(target, dry_run=args.dry_run)
        except KeyboardInterrupt:
            warn("Interrupted by user; nothing was submitted")
            return EXIT_FAILED
        except Exception as exc:  # fail safe, fail loud, fail closed
            error(f"{type(exc).__name__}: {exc}")
            shot = sess.screenshot("failure", target)
            dump = sess.dump_html("failure")
            result = Result(
                outcome=Outcome.FAILED,
                target_date=target,
                messages=[f"{type(exc).__name__}: {exc}"],
                artifacts=[p for p in (shot, dump) if p],
            )

    print_result(result, args.dry_run, cfg.default_amount)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
