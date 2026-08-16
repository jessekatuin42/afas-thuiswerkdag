"""CLI argument handling, output blocks and exit codes.

No browser is launched: these exercise the pure CLI surface.
"""

from datetime import date, timedelta

import pytest

import afas_thuiswerk as cli
from src import dates
from src.models import EXIT_FAILED, EXIT_OK, EXIT_UNVERIFIED, EXIT_USAGE, Declaration, Outcome, Result

TARGET = date(2026, 8, 17)


def parse(argv):
    return cli.build_parser().parse_args(argv)


class TestArguments:
    def test_date_flag(self):
        assert cli.resolve_date(parse(["--date", "2026-08-17"])) == TARGET

    def test_today(self):
        assert cli.resolve_date(parse(["--today"])) == dates.today()

    def test_yesterday(self):
        assert cli.resolve_date(parse(["--yesterday"])) == dates.today() - timedelta(days=1)

    def test_a_date_selector_is_required(self):
        with pytest.raises(SystemExit) as exc:
            parse([])
        assert exc.value.code == EXIT_USAGE

    def test_date_selectors_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as exc:
            parse(["--today", "--date", "2026-08-17"])
        assert exc.value.code == EXIT_USAGE

    def test_usage_errors_do_not_collide_with_unverified(self):
        """Exit 2 must mean 'submitted but unconfirmed', nothing else."""
        with pytest.raises(SystemExit) as exc:
            parse(["--bogus-flag"])
        assert exc.value.code != EXIT_UNVERIFIED

    def test_dry_run_defaults_off(self):
        assert parse(["--today"]).dry_run is False

    def test_headed_by_default(self):
        assert parse(["--today"]).headless is False

    def test_flags(self):
        args = parse(["--date", "2026-08-17", "--dry-run", "--headless", "--trace", "--quiet"])
        assert (args.dry_run, args.headless, args.trace, args.quiet) == (True, True, True, True)


class TestInvalidDateExitsBeforeAnyBrowser:
    @pytest.mark.parametrize("bad", ["2026-99-99", "2026-02-30", "17-08-2026", "nonsense"])
    def test_usage_exit_code(self, bad, monkeypatch):
        def explode(*a, **k):  # noqa: ANN001
            raise AssertionError("browser must not start for an invalid date")

        monkeypatch.setattr(cli, "session", explode)
        assert cli.main(["--date", bad]) == EXIT_USAGE


class TestExitCodes:
    @pytest.mark.parametrize(
        "outcome,expected",
        [
            (Outcome.CREATED, EXIT_OK),
            (Outcome.ALREADY_EXISTS, EXIT_OK),
            (Outcome.WOULD_CREATE, EXIT_OK),
            (Outcome.WOULD_SKIP, EXIT_OK),
            (Outcome.UNVERIFIED, EXIT_UNVERIFIED),
            (Outcome.FAILED, EXIT_FAILED),
        ],
    )
    def test_mapping(self, outcome, expected):
        assert Result(outcome=outcome, target_date=TARGET).exit_code == expected


class TestOutput:
    def test_already_exists(self, capsys):
        existing = Declaration(TARGET, "Thuiswerkdag", "raw", "€ 2,00", "Goedgekeurd")
        cli.print_result(
            Result(Outcome.ALREADY_EXISTS, TARGET, existing=existing), False, "€2.00"
        )
        out = capsys.readouterr().out
        assert "Already exists." in out
        assert "No new declaration was created." in out
        assert "2026-08-17" in out

    def test_created(self, capsys):
        cli.print_result(
            Result(Outcome.CREATED, TARGET, amount="€ 2,00"), False, "€2.00"
        )
        out = capsys.readouterr().out
        assert "Successfully created AFAS Thuiswerkdag declaration." in out
        assert "Status: Created" in out
        assert "€ 2,00" in out

    def test_unverified_does_not_claim_success(self, capsys):
        cli.print_result(Result(Outcome.UNVERIFIED, TARGET), False, "€2.00")
        out = capsys.readouterr().out
        assert "WARNING: AFAS did not confirm creation." in out
        assert "Please check AFAS manually." in out
        assert "Success" not in out

    def test_dry_run_would_create(self, capsys):
        cli.print_result(Result(Outcome.WOULD_CREATE, TARGET), True, "€2.00")
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "Existing declaration: No" in out
        assert "Would create:" in out
        assert "No changes were made." in out

    def test_dry_run_would_skip(self, capsys):
        existing = Declaration(TARGET, "Thuiswerkdag", "raw", "€ 2,00", "")
        cli.print_result(Result(Outcome.WOULD_SKIP, TARGET, existing=existing), True, "€2.00")
        out = capsys.readouterr().out
        assert "Existing declaration: YES" in out
        assert "No changes would be made." in out
        assert "Would create" not in out
