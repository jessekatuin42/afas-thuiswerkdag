# Setup guide

Start to finish, roughly ten minutes. Nothing here sends data anywhere except to
AFAS itself.

---

## 1. Install

```bash
git clone git@github.com:jessekatuin42/afas-thuiswerkdag.git
cd afas-thuiswerkdag
```

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

<details>
<summary>NixOS users — read this instead</summary>

Skip `playwright install chromium`: Playwright's bundled Chromium is not patched
for the Nix dynamic loader and will not start. The tool auto-detects your system
Chromium instead. Make sure you have one:

```bash
which chromium || nix-shell -p chromium
```

The `libstdc++.so.6` problem that breaks `greenlet` is handled automatically —
the entrypoint re-execs itself once with a corrected `LD_LIBRARY_PATH`.
</details>

Check it worked:

```bash
python -m pytest tests/ -q
```

All tests should pass without an AFAS account, network access, or a `.env`.

---

## 2. Find your AFAS environment number

Log into AFAS InSite in your normal browser and look at the address bar:

```
https://12345.afasinsite.nl/
        ^^^^^
        this is your environment number
```

---

## 3. Configure

```bash
cp .env.example .env
chmod 600 .env
```

Open `.env` and set **just this one line** for now:

```ini
AFAS_TENANT=12345
```

`.env` is gitignored. Never commit it.

Verify:

```bash
python afas_thuiswerk.py --today --dry-run
```

A Chromium window opens. Log into AFAS in that window — including SSO and 2FA if
prompted. The script waits (up to 15 minutes), then continues on its own and
prints whether a declaration already exists.

**`--dry-run` never submits anything**, so this is safe to run repeatedly.

---

## 4. First real run

Once the dry run reports sensibly:

```bash
python afas_thuiswerk.py --today
```

The tool will refuse to create a second declaration for a date that already has
one, so re-running is safe.

---

## 5. Optional: unattended login (2FA)

The session in `.browser-profile/` is reused, but AFAS's SSO cookies are
short-lived in practice, so you will be asked to log in fairly often. To avoid
that, add credentials to `.env`.

### Getting your TOTP secret

You need the **setup key** behind the 2FA QR code, not the 6-digit code your app
shows. In AFAS's 2FA setup screen, choose the *"can't scan the code"* / manual
entry option. It looks like:

```
JBSWY3DPEHPK3PXP
```

If 2FA is already enabled and the key is not shown, you must re-enrol 2FA to see
it again.

### Fill in `.env`

```ini
AFAS_TENANT=12345
AFAS_USERNAME=you@example.com
AFAS_PASSWORD=your-password
AFAS_TOTP_SECRET=JBSWY3DPEHPK3PXP
```

Then:

```bash
python afas_thuiswerk.py --today --dry-run
```

You should see:

```
[10:31:42] Attempting automated login (have: username, password, TOTP secret)
[10:31:44] Submitting password
[10:31:48] Submitting 2FA code
[10:31:53] Authenticated automatically
```

Every field is independent. Set only `AFAS_TOTP_SECRET` and you type the
password yourself while the code is filled in automatically.

> ⚠️ **Think about this one.** Storing your password *and* your TOTP secret in
> the same file puts both factors in one place — anyone who reads that file has
> full access, so it is effectively single-factor auth. That is a real reduction
> in your account's security, accepted deliberately for unattended runs. Keep
> `.env` at `chmod 600`, out of git, and out of any backup that leaves your
> machine. If you would rather not, set only the TOTP secret.

With credentials configured you can run without a visible browser:

```bash
python afas_thuiswerk.py --today --headless
```

---

## 6. Filing more than one day

Use the dashboard rather than a scheduler:

```bash
docker compose up -d      # then open http://127.0.0.1:8765
```

Click days, press **Check**, press **Sync**. It reads both systems back before
filing anything, so a day already declared is never declared twice.

> An earlier version shipped an 11:00 timer that inferred "I worked from home"
> from the machine being switched on. That inference was wrong twice — once
> when a scheduler replayed a missed run, once when the caller's clock read
> UTC — and each failure declared a day that had not happened. Picking days
> explicitly removes the guess, so the timer was dropped.
>
> The CLI still runs a single day (`--today`, `--date`) if you want it under
> your own scheduler. Duplicate detection makes repeated runs safe, and it
> exits non-zero when anything is unconfirmed, so alert on the exit code:

| Code | Meaning |
| --- | --- |
| `0` | Created, already existed, or dry run. |
| `1` | Failed; nothing submitted. |
| `2` | **Submitted but unconfirmed — check AFAS by hand.** |
| `3` | Usage error. |

Only run it on days you actually worked from home. The tool checks whether a
declaration exists; it cannot know whether you were entitled to one.

---

## Troubleshooting

**"AFAS environment not configured"**
`AFAS_TENANT` is missing. See step 2.

**"Could not find the 'Nieuw' control"** or similar
Your portal's layout differs, or AFAS changed it. Capture what you actually have
and compare against the structure described in the README:

```bash
python tools/inspect_afas.py --open-form
```

Output goes to `artifacts/`. It contains your own declaration data — review
before sharing it in an issue, and redact your name.

**"Not authenticated and running headless"**
`--headless` needs either a live session or credentials in `.env`. Run once
without `--headless` first.

**Login stops at an unrecognised screen**
Passkey prompts, consent pages and unexpected challenges are handed back to you
on purpose rather than guessed at. Complete it in the browser window.

**Chromium will not launch**
Point at a specific binary:

```bash
export AFAS_CHROMIUM=/usr/bin/chromium
```

**Different page paths in your environment**
Override them in `config.local.toml` (gitignored) — see the README's
Configuration section.
