# afas-thuiswerkdag

Declare your work days without clicking through two portals.

* **Home days** become an AFAS InSite **Thuiswerkdag** declaration (€2/day).
* **Office days** become **Shuttel** commute journeys, so the kilometres get
  reimbursed.

Pick a month on a calendar, press Sync, and it files what is missing.

```
Thuiswerkdagen                    [Home|Office]  Fill Tue/Wed/Thu  Fill Mon/Fri  Check  Sync

  Mon      Tue      Wed      Thu      Fri
   1        2        3        4        5
          [AFAS]   [AFAS]  [Shuttel]

AFAS checked 21:45   Shuttel checked 21:45
```

The two halves work nothing alike, and that is forced by the systems rather
than chosen. AFAS InSite exposes no employee-facing API for creating a
`verzameldeclaratie`, so it is driven through the real web UI with Playwright.
Shuttel is a Flutter app with no usable DOM, but it talks to a REST API behind
Keycloak — so it is driven through that, with no browser at all.

👉 **New here? Start with [SETUP.md](SETUP.md).**

## What it guarantees

* **Nothing is filed against a system it has not read.** Both systems are read
  back first. A day whose system could not be read is marked *unknown* and
  refused, never filed on the assumption that it is probably empty.
* **No duplicates.** Existing declarations are the source of truth; there is no
  local record of what you submitted. An office day counts as done only when
  *both* its journeys are present, so a half-filed day is never reported
  complete.
* **No blind success.** After submitting, it re-reads and confirms. If it
  cannot confirm, it says so and stops the entire run — an unknown outcome is
  not information, and nothing else should be written until a human looks.
* **No dangerous retries.** An uncertain submission is never retried, because
  that is exactly how duplicates get created.
* **Nothing runs unattended.** Declarations happen when you press Sync.

## Requirements

* Python 3.11+ (3.12 recommended — `tomllib` is used for config)
* Chromium (via Playwright, or a system one) for the AFAS half
* Docker or Podman, for the dashboard
* An AFAS InSite account with the Thuiswerkdag declaration available
* A Shuttel account, if you want the commute half

## Quick start

```bash
git clone git@github.com:jessekatuin42/afas-thuiswerkdag.git
cd afas-thuiswerkdag
cp .env.example .env && chmod 600 .env   # then set AFAS_TENANT
docker compose up -d                     # http://127.0.0.1:8765
```

Click days to mark them, **Check** to read both systems, **Sync** to file.
Dragging paints a range; **Fill Tue/Wed/Thu** does a month in one click.

The dashboard binds to `127.0.0.1` only. It files financial declarations and
has no authentication, so it must not be reachable from your network — if you
change the published port, keep the `127.0.0.1:` prefix.

For the Shuttel half, log in once. The tools run on the host rather than in
the container (the login opens a real browser), so they need the virtualenv:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tools/shuttel_login.py     # opens a browser, stores a token
.venv/bin/python tools/inspect_shuttel.py   # shows your saved routes
```

then set `SHUTTEL_COMMUTE_TEMPLATES` in `.env` to the routes that make up one
office day. See [SETUP.md](SETUP.md).

## Keeping it running

```bash
cp systemd/afas-planner.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now afas-planner
loginctl enable-linger "$USER"      # so it starts at boot without logging in
```

That unit only keeps a web server up. It files nothing and decides nothing.

> An earlier version shipped an 11:00 timer that inferred "worked from home"
> from the machine being switched on. The inference was wrong twice — once when
> systemd replayed a missed run, once when the caller's clock read UTC — and
> each time it declared a day that had not happened. Picking days explicitly
> removes the guess, so the timer was removed.

## Single day, from the command line

The original CLI still files one AFAS day, if you prefer it or want it under
your own scheduler:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python afas_thuiswerk.py --today --dry-run
```

Full walkthrough, including how to find your environment number and set up
unattended 2FA: **[SETUP.md](SETUP.md)**.

## CLI usage

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

`.env` also carries the account-specific bits, none of which belong in source:

| Variable | Meaning |
| --- | --- |
| `AFAS_TENANT` | your AFAS InSite environment number |
| `AFAS_DAYS` | which ISO weekdays are home days (Mon=1 … Sun=7; default `2,3,4`). Drives **Fill Tue/Wed/Thu**; **Fill Mon/Fri** is derived as the rest of the working week. |
| `SHUTTEL_COMMUTE_TEMPLATES` | the saved Shuttel routes making up one office day, as `transactionId` values. `tools/inspect_shuttel.py` lists yours. |
| `PLANNER_DB` | where the plan is stored (default `data/plan.db`) |

A malformed `AFAS_DAYS` stops the app rather than falling back to the default —
guessing there would mark days you never chose.

## Testing

```bash
python -m pytest tests/ -q               # everything (128 tests)
python -m pytest tests/ -q -m "not browser"   # offline only, no Chromium
```

Changing the code? See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the
architecture, the design invariants, what is proven live versus fixture-only,
and how to recapture the DOM when AFAS changes.

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
* **Shuttel stores no password at all.** `tools/shuttel_login.py` runs
  authorization-code + PKCE once in a browser and keeps the resulting
  `offline_access` refresh token in `.shuttel-token.json` — `chmod 600`,
  gitignored, dockerignored. Later runs need no browser and no password.
  (Shuttel's Keycloak realm runs a separate direct-grant flow that rejects
  credentials which log in perfectly well in a browser, so a password grant was
  never an option here.)
* `artifacts/` holds diagnostics — screenshots, DOM dumps, captured API
  responses. Those contain your own declarations, and for Shuttel your home and
  office addresses. Gitignored; redact before sharing.
* The dashboard binds to `127.0.0.1` and has no authentication. That is safe
  only for as long as it stays on loopback.
* Nothing is transmitted anywhere except to AFAS and Shuttel themselves.

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
