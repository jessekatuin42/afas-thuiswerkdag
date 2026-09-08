# Day planner: multi-day AFAS + Shuttel declarations

Status: **design approved, not implemented**
Date: 2026-09-08

---

## Problem

The tool files exactly one AFAS Thuiswerkdag per run, and infers *whether to
run* from presence — the desktop being off at 11:00 on an office day is the
whole signal. Two consequences:

* Filing a month means eleven invocations. Real bookings observed in AFAS
  covered 6 and 11 days at a time, so the tool's shape does not match how the
  work is actually done.
* Presence inference is fragile by construction. It has already been broken
  twice: once by systemd replaying a missed timer, once by a caller whose `TZ`
  read UTC while the wall clock read CEST.

Separately, office days are reimbursed through a **different system** —
[Shuttel](https://mijn.shuttel.nl), which pays per-kilometre commute costs and
is unrelated to AFAS. Nothing automates it.

## Goals

1. Pick many days at once, on a calendar, instead of one date per command.
2. Cover both systems: home days to AFAS, office days to Shuttel.
3. Keep an **editable plan** that can be re-synced, rather than fire-and-forget
   submissions.
4. **Read back** what each system already holds, so a filed day is visibly done
   and cannot be filed twice.
5. Retire presence inference. Explicit selection replaces it.

## Non-goals (v1)

* **Deleting or amending a filed declaration.** Destructive, and both systems
  treat a submitted declaration as a financial record. A mismatch is *shown*,
  never silently corrected.
* Multi-user, authentication on the dashboard, or exposure beyond loopback.
* Kubernetes deployment. Local-first; the container work already done carries
  over unchanged when that comes.
* Any change to `src/detection.py` or the existing create flow. They are
  proven live and get wrapped, not edited.

---

## The two systems are not alike

This asymmetry drives the whole architecture, and was established empirically
on 2026-09-08 rather than assumed:

| | AFAS Thuiswerkdag | Shuttel commute |
| --- | --- | --- |
| Reimburses | EUR 2.00 / work-from-home day | per-km commute cost |
| Transport | Playwright + Chromium (~2.5 GB image) | plain HTTPS client |
| Auth | scraped IdP screens + TOTP | Keycloak OIDC |
| Contract | HTML, drifts silently | versioned JSON API |
| Verification | re-read the grid after submit | read the API response |

### What was confirmed about Shuttel

From the portal's own public discovery documents (no login, no credentials):

```
GET /api/v1/authinfo/mijn.shuttel.nl.n?client=web
  realm     shuttel          clientId  shuttel-portal
  apiEndpoint  https://mijn.shuttel.nl

GET /auth/realms/shuttel/.well-known/openid-configuration
  issuer    https://mijn.shuttel.nl/auth/realms/shuttel
  grants    authorization_code, password, refresh_token, device_code, ...
  PKCE      S256
  scopes    openid, offline_access, shuttel_portal_api_user, ...
```

Two findings matter:

* **The portal is a Flutter web app.** It renders to canvas; its accessibility
  tree is empty until an "Enable accessibility" button is pressed. There is no
  stable DOM to select against, so the AFAS approach — Playwright plus role
  selectors — **cannot be reused here**. This is not a preference; it is a
  constraint.
* **There is a REST API behind Keycloak.** So Shuttel needs no browser at all.
  `offline_access` means a refresh token can carry unattended runs.

`password` appears in the *realm's* `grant_types_supported`. Whether the
`shuttel-portal` client itself has Direct Access Grants enabled is a per-client
setting and is **not yet known** — see Open questions.

---

## Core model

Three distinct things per date. Keeping them separate is what makes an
editable, re-syncable plan safe:

| Concept | Meaning | Origin |
| --- | --- | --- |
| **Intent** | `HOME` / `OFFICE` / `NONE` | the user, on the calendar |
| **State** | what each system actually holds | read back from AFAS + Shuttel |
| **Diff** | `intent - state` = actions to take | pure function, no I/O |

Day mapping, per the decision of 2026-09-08 — each system gets only what it
pays for:

* `HOME` -> AFAS Thuiswerkdag. Nothing in Shuttel.
* `OFFICE` -> Shuttel commute trip. Nothing in AFAS.
* `NONE` -> nothing anywhere.

**Resumability falls out of this model rather than being added to it.** A sync
that stops halfway is not a broken state to repair; it is simply a smaller diff
on the next run, because state is always re-read from the source of truth.

---

## Architecture

```
browser (localhost only)
   |  HTTP/JSON
FastAPI app
   |-- routes      plan CRUD, refresh state, preview diff, sync
   |-- PlanStore   SQLite
   `-- SyncEngine
         |-- AfasAdapter     wraps existing AfasInSite (Playwright)
         `-- ShuttelAdapter  Keycloak + httpx, no browser
```

### Adapter contract

```python
class DayFiler(Protocol):
    system: str                                     # "afas" | "shuttel"

    def read_month(self, year: int, month: int) -> dict[date, Entry | None]:
        """Everything this system holds for the month. Read-only."""

    def file(self, day: date) -> FileResult:
        """File one day. The caller NEVER retries this -- see invariants."""
```

No `delete`. Deliberate: see Non-goals.

`AfasAdapter` is a thin wrapper over the existing, proven `AfasInSite`. It adds
no AFAS knowledge of its own; `src/afas.py` and `src/detection.py` are not
modified.

### Proposed layout

```
src/adapters/base.py       DayFiler protocol, Entry, FileResult
src/adapters/afas.py       wraps AfasInSite
src/adapters/shuttel.py    Keycloak token handling + REST calls
src/planner/model.py       Intent, DayState, Action           (pure)
src/planner/diff.py        plan x state -> actions            (pure, critical)
src/planner/store.py       SQLite persistence
web/app.py                 FastAPI
web/static/                calendar UI
docker-compose.yml
```

---

## Sync semantics

```
refresh state  ->  compute diff  ->  SHOW the diff  ->  user confirms
               ->  execute sequentially, recording each day before the next
```

Rules, all inherited from `docs/DEVELOPMENT.md` > Design invariants and
*strengthened* because batching multiplies the blast radius:

1. **Never assume a submit worked.** Every filed day is confirmed by re-reading
   the system, exactly as the single-day flow does today.
2. **Never retry an uncertain submission.** A retry after a submit that may
   have landed is how duplicates are created.
3. **An `UNVERIFIED` day stops the entire run.** Stricter than today's
   single-day behaviour, and deliberately so: once one day's outcome is
   unknown, continuing risks compounding an unknown into a mess. The remaining
   unfiled days are trivially recoverable — they are just the next diff. A
   compounded unknown is not.
4. **Execute sequentially, never in parallel.** Both systems are stateful
   sessions; concurrent submissions against one session are untested and
   unnecessary for a workload of ~20 days.
5. **The two systems are independent *for definite failures*.** AFAS being
   refused, or unreachable, must not prevent Shuttel filing office days, and
   vice versa.

**Rules 3 and 5 draw a line that must not be blurred:** a *definite* failure is
isolated to the system that produced it; an *unknown* outcome stops everything,
both systems included. "AFAS refused me" is information and the run continues
elsewhere. "I submitted something and cannot tell whether it landed" is not
information, and no further writes should happen anywhere until a human has
looked.

### Duplicate detection

Per system, and neither trusts the other:

* **AFAS** already has real, column-aware duplicate detection. It must keep
  working exactly as it does — in particular the two-date-column trap
  (`Datum boeking` is when a declaration was *entered*, `Datum` is the day
  *declared*; one booking covers many days). Matching a date anywhere in a row
  produces a false "already exists" and **silently skips a day the user
  needs**. This risk grows with batching, not shrinks.
* **Shuttel** gets its own, from `read_month`. The API makes this cheap and
  unambiguous compared to scraping a grid.

---

## Data model

SQLite at `data/plan.db`.

| Table | Columns | Purpose |
| --- | --- | --- |
| `day_plan` | `date` PK, `intent`, `updated_at` | the editable plan |
| `state_cache` | `date`, `system`, `present`, `summary`, `read_at` | read-back, with staleness stamp |
| `sync_run` | `id`, `started_at`, `finished_at`, `outcome` | run history |
| `day_result` | `run_id`, `date`, `system`, `outcome`, `message`, `artifact` | per-day audit trail |

`state_cache` is not an optimisation, it is a requirement. Measured on
2026-09-08: reading the Thuiswerkdag-filtered grid took **16 seconds** to walk
12 pages and 24 rows, because AFAS pages roughly two rows at a time. That is
the cost of one read of the *whole* declaration history; a per-month read is
bounded by the same pagination behaviour. Re-reading that on every calendar
render is not viable, so the UI renders from cache with an explicit
"as of HH:MM" stamp and re-reads on demand.

Raising the grid page size, if the portal's `Opties` control allows it, would
cut this materially and is already noted as a win in `docs/DEVELOPMENT.md`.

---

## Error handling

| Condition | Behaviour |
| --- | --- |
| `AfasRefusedError` (entitlement withdrawn) | per-system banner: "AFAS is refusing the create page; read still works." The dashboard must look *honest*, not broken — this is the live state as of 2026-09-02. |
| AFAS DOM drift | fail closed, screenshot to `artifacts/`, that day marked failed, run stops |
| Shuttel token expired | refresh via `offline_access`; on refresh failure, prompt re-auth |
| Shuttel API error | that day marked failed; AFAS days unaffected |
| Either system unreachable | its half of the diff is skipped and reported; the other half proceeds |

---

## Security

* **Bind to `127.0.0.1`, never `0.0.0.0`.** This dashboard files financial
  declarations and must not be reachable from the LAN. A container makes
  `0.0.0.0` the accidental default, so this needs asserting in code and in
  compose, not assuming.
* Credentials stay in the gitignored `.env`, mounted read-only. Shuttel adds
  its own variables; they follow the existing rules — never logged, never
  printed, never screenshotted, `__repr__` overridden.
* **This repository is public** (`jessekatuin42/afas-thuiswerkdag`). Shuttel
  brings a second set of employer-specific values (saved-trip identifiers,
  account config). They get the same treatment the AFAS tenant number got in
  2026-08-16: configuration, never source. There should be a test asserting the
  defaults are empty, mirroring the existing `DEFAULT_TENANT == ""` test.
* Refresh tokens are credentials. They live beside `.env`, at `chmod 600`,
  never in `artifacts/`.

---

## Testing

* `src/planner/diff.py` is the new safety-critical logic and gets
  `detection.py`'s discipline: **pure, no I/O, no network, exhaustively unit
  tested.** It must be testable without a browser, an API, or a database.
* Adapters are fixture-tested. Shuttel is markedly easier than AFAS here —
  recorded JSON replays exactly, where HTML fixtures drift from the real portal.
* **No test files a real entry**, in either system. The existing suite already
  holds this line; batching does not relax it.
* The existing 142 tests keep passing unchanged. They have been confirmed green
  inside the container (126 offline + 16 real headless Chromium).

---

## Open questions

1. **Shuttel API shape** — endpoints, the payload for a commute entry, and how
   saved trips are referenced. Blocked on inspecting an authenticated session;
   the Chrome extension was not connected on 2026-09-08. The adapter is a stub
   until this is known. Nothing else in this design depends on it.
2. **Does `shuttel-portal` allow Direct Access Grants?** The realm advertises
   the `password` grant, but the client's own setting is unknown. If it is
   disabled, unattended runs need authorization-code + PKCE with a stored
   `offline_access` refresh token — more moving parts, still no browser at run
   time.
3. **Is the AFAS create page still refused?** Read access was confirmed healthy
   on 2026-09-08 (24 Thuiswerkdag rows across 12 pages). The create page was
   refused on 2026-09-02 and has not been re-tested, because `--dry-run` returns
   before `create_thuiswerkdag`. Until an entitlement is confirmed, the AFAS
   half can be built and tested but cannot be proven end to end.
4. **Repository name.** A two-vendor tool is no longer just "afas-thuiswerkdag".

---

## Deferred

* Deletion / amendment of filed declarations.
* Splitting the UI from the Playwright worker into separate images. The adapter
  boundary is drawn so this becomes moving a file rather than untangling one.
* Kubernetes. The container is built and verified locally; deploying it is a
  separate decision, and the notes below survive for whenever that happens.

### Container facts already established (2026-09-08)

Worth keeping, because each cost real time to find:

* The Playwright base image ships **browsers only**, not the Python client.
  The client must be pinned to the image tag, or it expects a different
  Chromium revision and fails at launch with "Executable doesn't exist".
* `pwuser` is uid **1001**, not 1000 — Ubuntu noble already ships a uid-1000
  `ubuntu` user. Rootless podman therefore needs
  `--userns=keep-id:uid=1001,gid=1001`; a plain `keep-id` maps the host user to
  1000 while the process runs as 1001, and reading `.env` (mode 600) fails.
* All three NixOS workarounds (`nixshim`, `_detect_chromium`, the Firefox
  dead-end) correctly no-op in the container. `AFAS_CHROMIUM` must stay unset.
* `TZ=Europe/Amsterdam` must be set explicitly. Images default to UTC, which is
  precisely the fault that let a 13:00 CEST run walk through an 11:00-11:59
  guard on 2026-08-27.
* The container gets its **own** browser profile, seeded from the host's. The
  container's Chromium is a newer build, and a newer Chromium writes profile
  state the host's older one cannot read — sharing the directory eventually
  costs the desktop session.
* Unattended password + TOTP login was **proven** in the container on
  2026-09-08 against live AFAS: the seeded session had expired, so it performed
  a genuine cold re-login. This was previously the largest unproven risk.
