# afas-thuiswerkdag

Create your AFAS InSite **Thuiswerkdag** (work-from-home) declaration from the
command line, instead of clicking through the portal.

```console
$ afas-thuiswerk --today
[09:30:03] Checking AFAS authentication
[09:30:04] Opening Mijn declaraties
[09:30:06] Filtered grid on Soort declaratie = Thuiswerkdag
[09:30:08] No declaration found for 2026-08-17
[09:30:11] Clicked 'Nieuw'
[09:30:12] Date field verified
[09:30:14] Submitting declaration
[09:30:18] SUCCESS: Thuiswerkdag created for 2026-08-17

Successfully created AFAS Thuiswerkdag declaration.

Date: 2026-08-17
Amount: 2,00
Status: Created
```

It drives the same web UI you use by hand, with Playwright + Chromium. There is
no AFAS API involved: AFAS InSite exposes no documented employee-facing API for
creating a `verzameldeclaratie`, so browser automation is the honest mechanism.

👉 **New here? Start with [SETUP.md](SETUP.md).**

## What it guarantees

* **No duplicates.** It checks your declarations grid for an existing
  Thuiswerkdag on the requested date before creating anything. AFAS is the
  source of truth — there is no local database of what you have submitted.
* **No blind success.** After submitting it re-reads the grid and confirms the
  record is really there. If it cannot confirm, it says so instead of claiming
  success, and exits non-zero.
* **No dangerous retries.** An uncertain submission is never retried
  automatically, because that is exactly how duplicates get created.
* **No credential handling by default.** You log in yourself in a real browser
  window. Credentials are opt-in, local-only, and never printed or logged.

## Requirements

* Python 3.11+ (3.12 recommended — `tomllib` is used for config)
* Chromium (installed via Playwright, or a system Chromium)
* An AFAS InSite account with the Thuiswerkdag declaration available

## Quick start

```bash
git clone git@github.com:jessekatuin42/afas-thuiswerkdag.git
cd afas-thuiswerkdag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env && chmod 600 .env   # then set AFAS_TENANT
python afas_thuiswerk.py --today --dry-run
```

Full walkthrough, including how to find your environment number and set up
unattended 2FA: **[SETUP.md](SETUP.md)**.

## Usage

```bash
python afas_thuiswerk.py --today
python afas_thuiswerk.py --yesterday
python afas_thuiswerk.py --date 2026-08-17
python afas_thuiswerk.py --date 2026-08-17 --dry-run
```

| Flag | Meaning |
| --- | --- |
| `--date YYYY-MM-DD` | The declaration date. Validated as a real calendar date. |
| `--today` / `--yesterday` | Convenience shorthands. |
| `--dry-run` | Check only; never clicks the final submit. |
| `--headless` | No visible browser. Needs an existing session or credentials. |
| `--trace` | Record a Playwright trace into `artifacts/`. |
| `--quiet` | Suppress progress logging. |

### Output

Already present:

```
AFAS Thuiswerkdag
Date: 2026-08-17

Already exists.
  Found: Thuiswerkdag | 17-08-2026 | 2,00 | Afgehandeld
No new declaration was created.
```

Dry run:

```
AFAS Thuiswerkdag - DRY RUN

Requested date: 2026-08-17
Existing declaration: No

Would create:
  Type: Thuiswerkdag
  Date: 2026-08-17

No changes were made.
```

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Created, already existed, or dry run — nothing is wrong. |
| `1` | Failed. Nothing was submitted, or the failure is unambiguous. |
| `2` | **Submitted but unconfirmed — check AFAS by hand.** |
| `3` | Usage error (bad flags, invalid date). |

Code `2` is deliberately reserved: it is the only state where AFAS may or may
not have recorded something, so it must never be confused with a usage error.

## How it works against the real AFAS UI

Everything below was established by inspecting a live portal, not assumed.

**Duplicate detection.** The page linked from the menu
(`/portal-medewerker-declaratie-prs/mijn-declaraties`) is only a tile page of
declaration *types* — it contains no declarations. The real list is the grid at
`/mijn-declaraties-prs/overzicht`, whose columns are:

```
Datum boeking │ Datum │ Status │ Soort declaratie │ Omschrijving │
Aantal te declareren │ Totaalbedrag │ Bijlage
```

The tool types `Thuiswerkdag` into that grid's own *Snelfilter voor Soort
declaratie* box, walks the pagination, and matches on **two** conditions: the
row's `Soort declaratie` is a Thuiswerkdag **and** its `Datum` equals the
requested day.

Reading the right column is essential, not pedantry. `Datum boeking` is when the
declaration was *entered*; `Datum` is the day being *declared*, and one booking
commonly covers many days:

```
Datum boeking   Datum         Status
11-02-2026      10-02-2026    Afgehandeld
11-02-2026      05-02-2026    Afgehandeld
11-02-2026      27-01-2026    Afgehandeld
```

Matching a date anywhere in the row would report "already exists" for
11-02-2026 against rows whose actual day is 05-02 or 27-01 — and the tool would
then silently skip a declaration you genuinely need. Detection therefore reads
the `Datum` column specifically, resolved by header *name* so reordering does
not break it. If the columns ever become unresolvable, the fallback accepts a
row only when it contains exactly one date; ambiguous rows are skipped rather
than guessed at.

**Creation is two steps.** The Thuiswerkdag page has a `Nieuw` button *and* an
`Aanmaken` button, and `Nieuw` opens a modal with its **own** `Aanmaken`:

1. `Nieuw` → modal with `Datum` (free text + date picker) and a **disabled**
   `Totaalbedrag` pre-filled `2,00`.
2. The modal's `Aanmaken` adds the line to the page's Thuiswerkdagen grid.
3. The page's `Aanmaken` submits the collected declaration.

The amount is derived by AFAS and its field is disabled, so the tool reads it
and never types it. Before submitting, the tool reads the `Datum` field back and
aborts if it does not hold the requested date.

**Authentication** goes through `idp.afasonline.com` (OIDC → `sts.afasonline.com`)
with a password screen followed by a TOTP 2FA challenge.

## Configuration

Everything lives in [`src/config.py`](src/config.py) with no organisation
hard-coded. Set `AFAS_TENANT` in `.env`, or override anything in a gitignored
`config.local.toml`:

```toml
[afas]
tenant = "12345"
# Optional: override a derived URL outright if your portal differs.
# declarations_url = "https://12345.afasinsite.nl/mijn-declaraties-prs/overzicht"

[browser]
headless = false
slow_mo_ms = 0
```

Precedence: built-in defaults < `.env` < `config.local.toml`.

## Testing

```bash
python -m pytest tests/ -q               # everything (128 tests)
python -m pytest tests/ -q -m "not browser"   # offline only, no Chromium
```

Tests are hermetic — they need no `.env`, no network and no AFAS account. The
`browser` marker launches a real headless Chromium against local HTML fixtures
that mirror the captured AFAS DOM; those never reach AFAS.

**There are no tests that create real declarations.** To exercise the live UI
without writing anything, use `--dry-run`.

## Troubleshooting

Capture what your AFAS actually renders (never submits):

```bash
python tools/inspect_afas.py --open-form
python tools/inspect_overview.py
python tools/inspect_login.py
```

Output lands in `artifacts/`, which is gitignored. On failure the tool saves a
screenshot and an HTML dump automatically. Use `--trace` for a full Playwright
trace.

> ⚠️ `artifacts/` contains **your own declaration data and name**. Review before
> sharing, and delete when you are done.

### NixOS

Two Nix-specific problems are handled automatically:

* Playwright's bundled Chromium is not patched for the Nix loader and fails to
  launch, so the tool auto-detects the system Chromium
  (`/run/current-system/sw/bin/chromium`). `playwright install chromium` is then
  unnecessary.
* `greenlet` needs `libstdc++.so.6`, which is not on the default loader path.
  The entrypoint re-execs itself once with a corrected `LD_LIBRARY_PATH`
  (see [`src/nixshim.py`](src/nixshim.py)).

Playwright cannot drive Firefox on NixOS — its Firefox build fails the host
requirement check, and unlike Chromium it cannot be pointed at a stock binary.

## Security

* Credentials are optional. Without them you log in by hand and nothing is
  stored beyond the browser session.
* With them, they live only in `.env` — gitignored, and `chmod 600` is checked
  at load time with a warning if it is looser.
* Values are never logged, printed, screenshotted or written to `artifacts/`.
  `Credentials.__repr__` is overridden, so even an accidental `print()` or a
  stack trace shows `password=set`, never the value.
* `.browser-profile/` holds session cookies and is gitignored. Never commit it.
* Nothing is transmitted anywhere except to AFAS itself.

> **On storing both factors.** Putting your password *and* your TOTP secret in
> one file is effectively single-factor auth. That is a deliberate trade-off for
> unattended runs, not a recommendation. Setting only `AFAS_TOTP_SECRET` keeps
> the factors apart: you type the password, the code is filled in for you.

## Disclaimer

Not affiliated with, endorsed by, or supported by AFAS. It automates a UI that
can change without notice — if a selector breaks, the tool fails safe rather
than submitting something unintended. Check the result in AFAS whenever the tool
reports exit code `2`.

## License

MIT — see [LICENSE](LICENSE).
