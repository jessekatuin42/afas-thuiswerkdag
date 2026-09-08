"""Shuttel: Keycloak-issued tokens plus a REST API. No browser involved.

The portal itself is a Flutter web app that renders to canvas, so it has no
stable DOM and the AFAS approach -- Playwright plus role selectors -- cannot be
reused here. Fortunately it does not need to be: the app talks to a REST API
behind Keycloak, and so can we.

Everything below was read from the portal's own *unauthenticated* discovery
endpoints on 2026-09-08:

    GET /api/v1/authinfo/mijn.shuttel.nl.n?client=web
    GET /auth/realms/shuttel/.well-known/openid-configuration

These are vendor-level facts, identical for every Shuttel customer, so they are
not employer-specific and belong in source. Anything account-specific (saved
trip identifiers, for instance) is configuration and must never be committed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import httpx

from .base import Entry, FileOutcome, FileResult

SHUTTEL_BASE = "https://mijn.shuttel.nl"
SHUTTEL_REALM = "shuttel"
SHUTTEL_CLIENT_ID = "shuttel-portal"
SHUTTEL_SCOPE = "openid offline_access shuttel_portal_api_user"

#: Shuttel splits journeys into commute and business. Only commute is ours.
COMMUTE_COST_TYPE = "commute"

_TOKEN_PATH = f"/auth/realms/{SHUTTEL_REALM}/protocol/openid-connect/token"
_AUTH_PATH = f"/auth/realms/{SHUTTEL_REALM}/protocol/openid-connect/auth"

#: Where Keycloak sends the authorization code back.
#:
#: NOT the portal's own /n/callback. That is the Flutter app's OIDC route: it
#: reads ?code=..., exchanges it, and rewrites the URL clean, so by the time a
#: human looks at the address bar the code is gone. Verified the hard way --
#: the paste came back as a bare "https://mijn.shuttel.nl/n/callback".
#:
#: The client accepts any https://mijn.shuttel.nl/* path (probed: /, /n/,
#: /robots.txt, /n/oidc-probe all reach the login page; localhost is rejected
#: with 400). /robots.txt serves 26 bytes of text/plain with no JavaScript, so
#: nothing runs, nothing consumes the code, and it stays in the address bar.
REDIRECT_URI = "https://mijn.shuttel.nl/robots.txt"

#: Refresh this many seconds before the token actually expires, so a slow
#: request cannot land after expiry.
_EXPIRY_MARGIN_S = 30


class ShuttelAuthError(RuntimeError):
    """Authentication failed. Never carries a credential value."""


class ShuttelNotImplementedError(NotImplementedError):
    """The Shuttel API surface has not been mapped yet."""


@dataclass(frozen=True)
class ShuttelCredentials:
    username: str = ""
    password: str = ""

    def __repr__(self) -> str:
        return (
            f"ShuttelCredentials(username={'set' if self.username else 'unset'}, "
            f"password={'set' if self.password else 'unset'})"
        )

    __str__ = __repr__

    @property
    def complete(self) -> bool:
        return bool(self.username and self.password)


# ---------------------------------------------------------------------------
# Authorization code + PKCE
#
# The password grant is a dead end for this account: Keycloak answered
# "invalid_grant: Invalid user credentials" for credentials that log in fine
# in a browser. Keycloak runs a *separate* Direct Grant Flow from the Browser
# Flow, and this realm's direct flow cannot satisfy the account -- so no
# credential would have fixed it.
#
# PKCE costs one browser login, once. After that the offline_access refresh
# token carries unattended runs and no password is involved at all, which is
# strictly better than storing one.
# ---------------------------------------------------------------------------


def new_verifier() -> str:
    """A fresh RFC 7636 code verifier (43-128 chars of the unreserved set)."""
    return secrets.token_urlsafe(64)[:96]


def verifier_challenge(verifier: str) -> str:
    """S256: unpadded base64url of the SHA-256 digest.

    The padding matters. Leaving '=' on the end fails at the very last step,
    after the human has already logged in.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorize_url(verifier: str, state: str | None = None,
                  base_url: str = SHUTTEL_BASE) -> str:
    state = state or secrets.token_urlsafe(16)
    params = {
        "client_id": SHUTTEL_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SHUTTEL_SCOPE,
        "code_challenge": verifier_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{base_url.rstrip('/')}{_AUTH_PATH}?{urlencode(params)}"


def is_callback(url: str, redirect_uri: str = REDIRECT_URI) -> bool:
    """Has the browser arrived at the redirect with a result?

    Watching for the path alone is not enough: the redirect target is a real
    page that can be reached without a code.
    """
    if not url.startswith(redirect_uri):
        return False
    query = parse_qs(urlparse(url).query)
    return "code" in query or "error" in query


def extract_code(pasted: str, expected_state: str | None = None) -> str:
    """Pull the authorization code out of a pasted callback URL, or accept a
    bare code. Reports an error callback rather than returning empty.

    ``expected_state`` is OAuth's CSRF protection, and it doubles as the check
    that this code belongs to the verifier still held in memory: a code from an
    earlier attempt otherwise fails at exchange time with an opaque PKCE error.
    """
    text = pasted.strip()
    if "?" not in text and "://" not in text:
        return text
    query = parse_qs(urlparse(text).query)
    if "error" in query:
        detail = query.get("error_description", [""])[0]
        raise ShuttelAuthError(
            f"Shuttel returned an error instead of a code: "
            f"{query['error'][0]}" + (f" ({detail})" if detail else "")
        )
    seen_state = query.get("state", [None])[0]
    if expected_state and seen_state and seen_state != expected_state:
        raise ShuttelAuthError(
            "That code is from a different login attempt.\n"
            f"  expected state {expected_state}\n"
            f"  but the URL carries {seen_state}\n"
            "  Each run generates a fresh verifier that only its own URL "
            "matches, and the old one is gone once its process exits.\n"
            "  Open the URL THIS run printed, then paste from that tab."
        )

    codes = query.get("code")
    if not codes:
        if "/n/callback" in text or urlparse(text).path.rstrip("/") == "/n":
            raise ShuttelAuthError(
                "That is the app's own page, not the login redirect.\n"
                "  The Flutter app consumes the code at /n/callback, which is\n"
                "  exactly why the login points at /robots.txt instead.\n"
                "  The tab you want shows two lines of plain text:\n"
                "      User-agent: *\n"
                "      Disallow: /\n"
                "  ...and its address bar ends with ?code=..."
            )
        raise ShuttelAuthError(
            f"No 'code' parameter in that URL. Expected something ending in "
            f"?code=... on {REDIRECT_URI}"
        )
    return codes[0]


class TokenStore:
    """Persists the refresh token. It is a credential; treat it like one."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> str:
        try:
            return json.loads(self.path.read_text()).get("refresh_token", "")
        except Exception:
            # Missing, unreadable or corrupt all mean the same thing to the
            # caller: log in again.
            return ""

    def save(self, refresh_token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"refresh_token": refresh_token}))
        os.chmod(self.path, 0o600)


class TokenClient:
    """Holds a Keycloak access token, refreshing it as needed."""

    def __init__(
        self,
        credentials: ShuttelCredentials,
        base_url: str = SHUTTEL_BASE,
        transport: httpx.BaseTransport | None = None,
        store: TokenStore | None = None,
    ):
        self._creds = credentials
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url, transport=transport, timeout=30.0
        )
        self._tokens = store
        self._access: str = ""
        self._refresh: str = store.load() if store else ""
        self._expires_at: float = 0.0

    def access_token(self) -> str:
        if self._access and time.time() < self._expires_at - _EXPIRY_MARGIN_S:
            return self._access

        # Report which path failed. Falling through silently made an expired
        # refresh token look like a wrong password, and a rejected password
        # look like the only problem -- both send you to fix the wrong thing.
        tried: list[str] = []

        if self._refresh:
            if self._try_refresh():
                return self._access
            tried.append(
                "the stored refresh token was rejected (expired, revoked, or "
                "already rotated by another run)"
            )

        if self._creds.complete:
            try:
                return self._password_grant()
            except ShuttelAuthError as exc:
                tried.append(str(exc))
        else:
            tried.append("no SHUTTEL_USERNAME / SHUTTEL_PASSWORD set")

        detail = "".join(f"\n  - {t}" for t in tried)
        raise ShuttelAuthError(
            "No usable Shuttel session." + detail +
            "\n\nRun:  python tools/shuttel_login.py"
        )

    def exchange_code(self, code: str, verifier: str) -> str:
        """Trade an authorization code for tokens (PKCE)."""
        try:
            resp = self._client.post(_TOKEN_PATH, data={
                "grant_type": "authorization_code",
                "client_id": SHUTTEL_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            })
        except httpx.HTTPError as exc:
            raise ShuttelAuthError(f"Could not reach Shuttel: {exc}") from None
        if resp.status_code != 200:
            code_, desc = "", ""
            try:
                payload = resp.json()
                code_, desc = payload.get("error", ""), payload.get("error_description", "")
            except Exception:
                pass
            raise ShuttelAuthError(
                f"Shuttel would not exchange the code (HTTP {resp.status_code} "
                f"{code_}" + (f": {desc}" if desc else "") + "). Authorization "
                "codes are single-use and short-lived -- start the login again."
            )
        return self._store(resp.json())

    def _store(self, payload: dict) -> str:
        self._access = payload.get("access_token", "")
        rotated = payload.get("refresh_token", "")
        self._refresh = rotated or self._refresh
        self._expires_at = time.time() + float(payload.get("expires_in", 0))
        # Keycloak rotates refresh tokens. Dropping the new one means the next
        # unattended run fails and you are back at a browser.
        if rotated and self._tokens is not None:
            self._tokens.save(rotated)
        return self._access

    def _try_refresh(self) -> bool:
        try:
            resp = self._client.post(_TOKEN_PATH, data={
                "grant_type": "refresh_token",
                "client_id": SHUTTEL_CLIENT_ID,
                "refresh_token": self._refresh,
            })
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            self._refresh = ""
            return False
        self._store(resp.json())
        return True

    def _password_grant(self) -> str:
        if not self._creds.complete:
            raise ShuttelAuthError(
                "Shuttel credentials are not configured. Set SHUTTEL_USERNAME "
                "and SHUTTEL_PASSWORD in .env."
            )
        try:
            resp = self._client.post(_TOKEN_PATH, data={
                "grant_type": "password",
                "client_id": SHUTTEL_CLIENT_ID,
                "scope": SHUTTEL_SCOPE,
                "username": self._creds.username,
                "password": self._creds.password,
            })
        except httpx.HTTPError as exc:
            raise ShuttelAuthError(f"Could not reach Shuttel: {exc}") from None

        if resp.status_code != 200:
            # Never include the request body, which holds the password.
            # Keycloak's own error fields are enough to act on -- and
            # error_description is the one that actually says why, so dropping
            # it (as an earlier version did) turns a precise answer into a guess.
            code = desc = ""
            try:
                payload = resp.json()
                code = payload.get("error", "")
                desc = payload.get("error_description", "")
            except Exception:
                pass

            # These two are routinely confused, and they point in opposite
            # directions:
            #   unauthorized_client -> the client forbids this grant entirely
            #   invalid_grant       -> the grant is allowed, the login was not
            if code == "unauthorized_client":
                raise ShuttelAuthError(
                    f"The shuttel-portal client does not permit the password "
                    f"grant (HTTP {resp.status_code} {code}"
                    + (f": {desc}" if desc else "")
                    + "). No credential will fix this; authorization-code + "
                    "PKCE is required instead."
                )

            raise ShuttelAuthError(
                f"Shuttel rejected the login (HTTP {resp.status_code} {code}"
                + (f": {desc}" if desc else "")
                + "). The grant type itself is permitted -- Keycloak answers "
                "'unauthorized_client' when it is not -- so this is about the "
                "credentials or the account: a wrong username format, a wrong "
                "password, or an account that requires a further step such as "
                "a one-time code."
            )
        return self._store(resp.json())


# ---------------------------------------------------------------------------
# Discovery
#
# The API surface was read out of the portal's own public Flutter bundle
# (main.dart.js), which is generated OpenAPI client code and therefore carries
# every path as a string literal. What it does NOT carry is this account's
# data: which declarationCode means "commute", which saved routes exist, how
# far they are. Those decide the contents of a financial record, so they get
# read from the account rather than guessed -- the same reason
# tools/inspect_afas.py captures real DOM instead of inventing selectors.
#
# /api/v1/openapi.json answers 401 rather than 404, so the full spec is there
# for an authenticated caller and is worth collecting first.
# ---------------------------------------------------------------------------

#: Read-only endpoints worth capturing. Deliberately excludes
#: /api/v1/profile/bankaccount and /api/v1/profile/postal_address: nothing here
#: needs them, and inspection output lands in artifacts/ as plain JSON.
INSPECT_ENDPOINTS: tuple[str, ...] = (
    "/api/v1/openapi.json",
    "/api/v1/profile/",
    "/api/v1/profile/declaration_codes",
    "/api/v1/favorites/routes",
    "/api/v1/homeworkdays/v2",
    "/api/v1/mobility_arrangement/services",
)


class ShuttelClient:
    """Authenticated read-only access, for discovery.

    Has no method that writes. That is the point: it is used to work out what
    a real declaration looks like, and a tool for that must not be able to
    create one by accident.
    """

    def __init__(
        self,
        tokens: TokenClient,
        base_url: str = SHUTTEL_BASE,
        transport: httpx.BaseTransport | None = None,
    ):
        self._tokens = tokens
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), transport=transport, timeout=60.0
        )

    def get(self, path: str) -> dict:
        token = self._tokens.access_token()
        try:
            resp = self._client.get(path, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            return {"status": None, "error": f"{type(exc).__name__}: {exc}"}
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:2000]
        return {"status": resp.status_code, "body": body}

    def inspect(self) -> dict[str, dict]:
        """Collect every read-only endpoint.

        One endpoint failing does not lose the others: entitlements differ per
        account, and a partial capture is still worth having.
        """
        return {path: self.get(path) for path in INSPECT_ENDPOINTS}


class ShuttelApi(ShuttelClient):
    """Read-only access plus the one write the planner needs.

    Separate from ShuttelClient on purpose: the inspector takes the read-only
    class, so a discovery tool still cannot create a declaration by accident.
    """

    def post(self, path: str, payload: dict) -> dict:
        token = self._tokens.access_token()
        try:
            resp = self._client.post(
                path, json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            return {"status": None, "error": f"{type(exc).__name__}: {exc}"}
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:2000]
        return {"status": resp.status_code, "body": body}


# ---------------------------------------------------------------------------
# Turning a saved favourite into a new declaration
#
# A favourite is not merely a route: the API returns it carrying every
# MobileDeclaration field, marked "type": "template". Filing a commute day is
# therefore cloning one onto a new date -- not assembling a payload by hand.
# ---------------------------------------------------------------------------

#: The clock the declarations are expressed in. The offset must be recomputed
#: per target date, never copied from the template: a February template carries
#: +01:00, and reusing that in July files the journey an hour off.
_NL = ZoneInfo("Europe/Amsterdam")

#: Fields the server owns. transactionId in particular identifies an *existing*
#: transaction; sending it back either fails or ties the new declaration to the
#: old one.
READ_ONLY_FIELDS: frozenset[str] = frozenset({
    "transactionId", "referenceId", "processed", "deletable", "editable",
    "type", "filtered", "canBeMarkedRecurring", "metaData", "co2", "iconName",
})


def _move_to(stamp: str, day: date) -> str:
    """Same wall-clock time, new date, correct Dutch UTC offset."""
    original = datetime.fromisoformat(stamp).astimezone(_NL)
    moved = datetime.combine(day, original.time(), tzinfo=_NL)
    return moved.isoformat(timespec="milliseconds")


def redate_template(template: dict, day: date) -> dict:
    """Clone a saved favourite onto ``day``, keeping every time-of-day."""
    if not template.get("startsOn"):
        raise ValueError("template has no startsOn; refusing to invent one")

    out = {k: v for k, v in template.items() if k not in READ_ONLY_FIELDS}
    out["startsOn"] = _move_to(template["startsOn"], day)
    if template.get("endsOn"):
        out["endsOn"] = _move_to(template["endsOn"], day)
    out["locations"] = [
        {**loc, "time": _move_to(loc["time"], day)} if loc.get("time") else dict(loc)
        for loc in (template.get("locations") or [])
    ]
    out["quantities"] = [dict(q) for q in (template.get("quantities") or [])]
    return out


#: Query parameters the portal itself uses. includeProcessed matters: without
#: it the list comes back empty, because settled journeys are the normal case.
_TX_PATH = "/api/v1/transaction/"

#: Journeys are POSTed here, NOT to /api/v1/transaction/declaration. That one
#: is the expense endpoint and answers 409 "Declaration does not have an
#: attachment" -- it wants a receipt, which a car journey has no business
#: carrying. Learned by having it refused.
_JOURNEY_PATH = "/api/v1/transaction/"
_FAVOURITES_PATH = "/api/v1/favorites/routes"
_PAGE_SIZE = 100
_MAX_PAGES = 20


def _month_range(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start.isoformat(), end.isoformat()


class ShuttelAdapter:
    """DayFiler for Shuttel commute journeys.

    An office day is two journeys -- out in the morning, back in the evening --
    filed by cloning the two saved favourites named in ``template_ids`` onto
    the target date. Which favourites those are is account-specific and must
    stay configuration: this repository is public.
    """

    system = "shuttel"

    def __init__(self, api: "ShuttelApi", template_ids: tuple[str, ...] = ()):
        self._api = api
        self._template_ids = tuple(template_ids)
        self._templates: list[dict] | None = None

    # -- templates --------------------------------------------------------

    def templates(self) -> list[dict]:
        if self._templates is None:
            result = self._api.get(_FAVOURITES_PATH)
            favourites = result.get("body") if result.get("status") == 200 else []
            by_id = {f.get("transactionId"): f for f in (favourites or [])}
            self._templates = [by_id[t] for t in self._template_ids if t in by_id]
        return self._templates

    # -- reading ----------------------------------------------------------

    def _journeys_by_day(self, year: int, month: int) -> dict[date, dict]:
        """Per day: how many commute journeys, their value and their distance.

        settlement_net is what Shuttel says it will pay. Deriving a
        euro-per-kilometre rate here instead would go silently wrong the day
        the rate changes, and the figure feeds a money counter.
        """
        lo, hi = _month_range(year, month)
        counts: dict[date, dict] = {}
        for page in range(_MAX_PAGES):
            result = self._api.get(
                f"{_TX_PATH}?costTypes={COMMUTE_COST_TYPE}&fromDate={lo}"
                f"&untilDate={hi}&includeProcessed=true"
                f"&pageNr={page}&pageSize={_PAGE_SIZE}"
            )
            if result.get("status") != 200:
                raise ShuttelAuthError(
                    f"Shuttel would not list transactions "
                    f"(HTTP {result.get('status')})."
                )
            body = result.get("body") or {}
            for item in body.get("content") or []:
                stamp = item.get("startsOn") or ""
                try:
                    when = datetime.fromisoformat(stamp).astimezone(_NL).date()
                except ValueError:
                    continue
                if when.year == year and when.month == month:
                    acc = counts.setdefault(when, {"n": 0, "eur": 0.0, "km": 0.0})
                    acc["n"] += 1
                    acc["eur"] += float(item.get("settlement_net") or 0.0)
                    acc["km"] += float(
                        item.get("mileage_commute")
                        or (item.get("quantities") or [{}])[0].get("amount")
                        or 0.0
                    )
            if body.get("number", 0) + 1 >= (body.get("totalPages") or 1):
                break
        return counts

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        """Days that are *completely* filed.

        A day with only one of its two journeys is deliberately not reported:
        calling it done would leave the return leg unclaimed with nothing to
        show for it -- the same silent-underclaim failure as the AFAS
        two-date-column trap.
        """
        expected = max(len(self._template_ids), 1)
        return {
            day: Entry(day=day, summary=f"{acc['n']} commute journey(s)",
                       amount=acc["eur"] or None, km=acc["km"] or None)
            for day, acc in self._journeys_by_day(year, month).items()
            if acc["n"] >= expected
        }

    # -- writing ----------------------------------------------------------

    def file(self, day: date) -> FileResult:
        templates = self.templates()
        expected = max(len(self._template_ids), 1)
        existing = self._journeys_by_day(day.year, day.month).get(day, {}).get("n", 0)

        if existing >= expected:
            return FileResult(day, self.system, FileOutcome.ALREADY,
                              f"Shuttel already has {existing} journey(s)")
        if existing:
            # Neither done nor safe to complete: posting the full set again
            # would duplicate the leg already there.
            return FileResult(
                day, self.system, FileOutcome.FAILED,
                f"Shuttel has {existing} of {expected} journeys for that day. "
                f"Fix it by hand -- filing the set again would duplicate the "
                f"journey that is already there."
            )

        if len(templates) != len(self._template_ids) or not templates:
            return FileResult(
                day, self.system, FileOutcome.FAILED,
                f"Could not resolve every configured commute template "
                f"({len(templates)} of {len(self._template_ids)} found). "
                f"Check SHUTTEL_COMMUTE_TEMPLATES."
            )

        for template in templates:
            result = self._api.post(_JOURNEY_PATH, redate_template(template, day))
            if result.get("status") not in (200, 201):
                # Stop at the first refusal. Continuing would leave a day
                # half-filed, and nothing about it would look wrong afterwards.
                #
                # Carry the server's own explanation: Shuttel answers RFC 7807
                # problem documents, and 'detail' is the whole diagnosis. A
                # bare status code sends the reader guessing.
                body = result.get("body")
                detail = ""
                if isinstance(body, dict):
                    detail = body.get("detail") or body.get("title") or ""
                elif body:
                    detail = str(body)[:300]
                return FileResult(
                    day, self.system, FileOutcome.FAILED,
                    f"Shuttel refused a journey (HTTP {result.get('status')}"
                    + (f": {detail}" if detail else "")
                    + f"); {len(templates)} were planned. Check the day by hand."
                )

        # Never trust the submit: confirm by re-reading, as the AFAS side does.
        if self._journeys_by_day(day.year, day.month).get(day, {}).get("n", 0) >= expected:
            return FileResult(day, self.system, FileOutcome.FILED,
                              f"{len(templates)} journey(s)")
        return FileResult(
            day, self.system, FileOutcome.UNVERIFIED,
            "Shuttel accepted the journeys but does not list them. Check by hand."
        )
