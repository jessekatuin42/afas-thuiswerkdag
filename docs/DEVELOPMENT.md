# Development & handoff notes

Written so this project can be picked up cold, months later, without
re-deriving what took the longest to learn. User-facing docs are
[README.md](../README.md) and [SETUP.md](../SETUP.md); this file is about
*changing* the code.

---

## Where things are

| Path | Responsibility |
| --- | --- |
| `afas_thuiswerk.py` | CLI: arg parsing, date resolution, output blocks, exit codes. No AFAS knowledge. |
| `src/config.py` | URLs derived from `AFAS_TENANT`; `.env` + `config.local.toml` precedence. |
| `src/dates.py` | ISO↔Dutch parsing and date extraction from text. Pure. |
| `src/detection.py` | Duplicate detection. **Pure — no Playwright.** The safety-critical logic. |
| `src/afas.py` | All AFAS page interaction: grid reading, quick filter, the two-step create flow. |
| `src/browser.py` | Persistent-profile Chromium, login orchestration, screenshots/traces. |
| `src/auth.py` | Credential loading + TOTP. Importable without Playwright. |
| `src/models.py` | `Declaration`, `Result`, `Outcome`, exit codes. |
| `src/nixshim.py` | NixOS `libstdc++` re-exec. No-op elsewhere. |
| `tools/inspect_*.py` | Read-only DOM capture helpers. Never submit. |

The deliberate split: **`detection.py` never imports Playwright**, so the logic
that decides "does this already exist?" is exhaustively testable offline.
`afas.py`'s only job is to hand it cell text plus column headers. Keep it that
way — putting matching logic into `afas.py` would make it untestable without a
browser and is how this kind of tool rots.

---

## Design invariants

Break these and the tool becomes unsafe rather than merely broken.

1. **Never assume a click worked.** Creation is confirmed by re-reading the
   declarations grid. `Outcome.UNVERIFIED` (exit `2`) exists precisely so
   "probably fine" is never reported as success.
2. **Never retry an uncertain submission.** A retry after a submit that may
   have landed is how duplicates get created. Read-only verification retries
   are fine; the submit is not.
3. **Duplicate detection needs type *and* date.** Both, from their own columns.
   See "The two-date-column trap" below.
4. **Never type the amount.** AFAS derives `Totaalbedrag` and the field is
   disabled. Read it; never fill it.
5. **Never log, print or screenshot credentials.** `Credentials.__repr__` is
   overridden. Diagnostics are only captured on portal pages, never login
   screens. If you add logging near `auth.py`, re-check this.
6. **Fail closed.** On anything unrecognised: screenshot, non-zero exit, no
   clicking around to "recover".
7. **No organisation hard-coded.** The environment number is config. There is a
   test asserting `DEFAULT_TENANT == ""`.

---

## The two-date-column trap

The single most important thing to know. The declarations grid has:

```
Datum boeking │ Datum │ Status │ Soort declaratie │ Omschrijving │ …
11-02-2026    │ 10-02-2026 │ Afgehandeld │ Thuiswerkdag │ Thuiswerkdag │ …
11-02-2026    │ 05-02-2026 │ Afgehandeld │ Thuiswerkdag │ Thuiswerkdag │ …
11-02-2026    │ 27-01-2026 │ Afgehandeld │ Thuiswerkdag │ Thuiswerkdag │ …
```

`Datum boeking` is when the declaration was *entered*; `Datum` is the day being
*declared*. People book many days in one sitting, so booking dates repeat
across rows.

A naive `if target_date in row_text` reports "already exists" for 11-02-2026
against all three rows — and the tool then **silently skips a declaration the
user actually needs**. This is a false negative in the worst direction: no
error, no output, just a missing declaration nobody notices until payroll.

Columns are resolved by header *name* (`resolve_columns`), not index, so AFAS
reordering columns does not break it. Header text is normalised because AFAS
appends sort/filter affordances (`"Datum boeking 1 31"`).

`find_thuiswerkdag_in_texts` is the fallback when headers cannot be resolved.
It is deliberately *stricter*: it accepts a row only if it contains exactly one
distinct date. Ambiguous rows are skipped rather than guessed at. Do not
"improve" this by making it more permissive.

---

## Verification status

Be honest about this when changing things — not everything is equally proven.

| Area | Status |
| --- | --- |
| Duplicate detection (both directions) | ✅ Proven live: existing dates found, adjacent dates correctly not matched |
| Creation, two-step flow | ✅ Proven live: real declaration created and verified |
| Idempotency | ✅ Proven live: re-run refused to duplicate |
| Automated password + TOTP login | ✅ Proven live |
| Column-aware matching | ✅ Unit + browser tests; the *booking-only* date case is fixture-tested (real data had no such date at capture time) |
| Text fallback path | ⚠️ Unit-tested only — never triggered against live AFAS |
| `--headless` | ⚠️ Fixture-tested only — never run against live AFAS |
| Other AFAS environments | ❌ Never tested. Page paths may differ. |
| Pagination beyond ~20 pages | ❌ `MAX_PAGES` guard untested at the limit |

---

## When AFAS changes the UI

Symptom is usually `Could not find the 'Nieuw' control` or
`Grid has no 'Datum' column`. Do **not** start guessing selectors — recapture:

```bash
python tools/inspect_afas.py --open-form   # thuiswerkdag page + the modal
python tools/inspect_overview.py           # declarations grid + quick filters
python tools/inspect_login.py              # login form structure only
```

Output lands in `artifacts/`: `.aria.txt` (accessibility tree — start here),
`.html`, `.text.txt`, `.frames.json`, plus screenshots. The ARIA snapshot is
usually enough to fix a selector in one pass.

> `artifacts/` contains real declaration data and your name. It is gitignored;
> redact before attaching to an issue.

Then update the fixtures in `tests/fixtures/` to match the new DOM, so the
browser tests keep meaning something. **Fixtures mirroring reality is the whole
point** — a fixture that drifts from the real portal gives false confidence.

---

## Testing

```bash
python -m pytest tests/ -q                    # 128 tests
python -m pytest tests/ -q -m "not browser"   # offline only, no Chromium
```

Tests are hermetic: `conftest.py` pins `AFAS_TENANT=00000` which wins over any
real `.env` (loaded with `override=False`). They need no network, no
credentials and no AFAS account. Keep it that way.

The `browser` marker launches real headless Chromium against local fixtures.
There are deliberately **no tests that create real declarations**; use
`--dry-run` against live AFAS instead.

---

## Known rough edges

* **Orphan line on partial failure.** Creation is two submits. If the modal's
  `Aanmaken` succeeds but the page's `Aanmaken` fails, a line sits in the form
  unsubmitted. Nothing is recorded in AFAS (safe), but the run aborts
  mid-flow. Not currently detected or cleaned up.
* **Pagination is slow.** AFAS pages ~2 rows at a time; 17 rows took 9 page
  clicks (~15s). Filtering by `Soort declaratie` already helps. Raising the
  grid's page size — if AFAS's `Opties` button allows it — would be a real win.
* **Session cookies are short-lived.** `.browser-profile/` alone does not keep
  you logged in for long; this is why `.env` credentials exist.
* **`_looks_logged_out()` is heuristic** (host mismatch, password field, small
  login-flavoured body). It has not misfired in practice, but it is the most
  likely thing to break on an AFAS login redesign.
* **One declaration per run.** See below.

---

## Next steps, roughly by value

1. **Batch mode.** The biggest real-world gap. AFAS's whole point of a
   *verzameldeclaratie* is multiple lines in one submission, and that is how
   people actually use it — observed bookings covered 6 and 11 days at once.
   The create flow already loops naturally: click `Nieuw` / fill / dialog
   `Aanmaken` per date, then one page-level `Aanmaken` for all of them. Would
   need `--date-range`, or repeatable `--date`, plus per-date duplicate
   filtering before adding lines. This turns a 15s-per-day tool into a
   one-command month.
2. **Verify `--headless` against live AFAS.** Needed before anyone trusts the
   cron recipe in SETUP.md. Cheap to do, currently an unproven claim.
3. **Detect and clear an orphan line** if the page-level submit fails.
4. **Raise the grid page size** to cut the pagination cost.
5. **`--list` command** to print your Thuiswerkdagen. Trivial given
   `read_grid_rows()` already exists, and genuinely useful for reconciliation.
6. **CI** running the offline suite. Tests are already hermetic, so this is
   nearly free — just do not attempt browser tests without a Chromium in CI.

---

## Environment notes (NixOS)

Three separate problems, all handled, all easy to trip over again:

* Playwright's **bundled Chromium will not launch** — not patched for the Nix
  loader. `config._detect_chromium()` prefers the system binary.
* **`greenlet` needs `libstdc++.so.6`**, absent from the default loader path.
  `LD_LIBRARY_PATH` is read at process start, so Python cannot fix it after the
  fact — `nixshim.ensure_native_libs()` re-execs once, using `sys.orig_argv` so
  `python -m pytest` survives.
* That re-exec **must be skipped under pytest**. `execve` inherits pytest's
  captured stdout, so the run vanishes: zero output, exit code 0, no tests
  reported. `nixshim` checks `"pytest" in sys.modules`; `conftest.py` preloads
  the library with `ctypes` instead. If tests ever go mysteriously silent, this
  is why.

**Playwright cannot drive Firefox here at all** — its Firefox build fails the
host requirement check, and unlike Chromium it cannot be pointed at a stock
binary. Do not spend time on this.
