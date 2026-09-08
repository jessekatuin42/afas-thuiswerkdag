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
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .base import Entry, FileOutcome, FileResult

SHUTTEL_BASE = "https://mijn.shuttel.nl"
SHUTTEL_REALM = "shuttel"
SHUTTEL_CLIENT_ID = "shuttel-portal"
SHUTTEL_SCOPE = "openid offline_access shuttel_portal_api_user"

_TOKEN_PATH = f"/auth/realms/{SHUTTEL_REALM}/protocol/openid-connect/token"
_AUTH_PATH = f"/auth/realms/{SHUTTEL_REALM}/protocol/openid-connect/auth"

#: Registered on the client; Keycloak rejects anything else.
REDIRECT_URI = "https://mijn.shuttel.nl/n/callback"

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
    params = {
        "client_id": SHUTTEL_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SHUTTEL_SCOPE,
        "code_challenge": verifier_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state or secrets.token_urlsafe(16),
    }
    return f"{base_url.rstrip('/')}{_AUTH_PATH}?{urlencode(params)}"


def extract_code(pasted: str) -> str:
    """Pull the authorization code out of a pasted callback URL, or accept a
    bare code. Reports an error callback rather than returning empty."""
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
    codes = query.get("code")
    if not codes:
        raise ShuttelAuthError("No 'code' parameter in that URL.")
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
        if self._refresh and self._try_refresh():
            return self._access
        if self._creds.complete:
            return self._password_grant()
        raise ShuttelAuthError(
            "No usable Shuttel session. Run:  python tools/shuttel_login.py"
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


class ShuttelAdapter:
    """DayFiler for Shuttel. API surface not yet mapped -- see spec Q1.

    Deliberately raises rather than returning empty results: an adapter that
    quietly reported "nothing here" would make unfiled office days look filed.
    """

    system = "shuttel"

    def __init__(self, token_client: TokenClient):
        self._tokens = token_client

    def read_month(self, year: int, month: int) -> dict[date, Entry]:
        raise ShuttelNotImplementedError(
            "Shuttel read_month is not implemented: the commute-entry API has "
            "not been mapped yet (spec open question 1)."
        )

    def file(self, day: date) -> FileResult:
        return FileResult(
            day, self.system, FileOutcome.FAILED,
            "Shuttel adapter not implemented yet (spec open question 1).",
        )
