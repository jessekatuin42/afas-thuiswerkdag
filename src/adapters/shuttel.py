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

import time
from dataclasses import dataclass
from datetime import date

import httpx

from .base import Entry, FileOutcome, FileResult

SHUTTEL_BASE = "https://mijn.shuttel.nl"
SHUTTEL_REALM = "shuttel"
SHUTTEL_CLIENT_ID = "shuttel-portal"
SHUTTEL_SCOPE = "openid offline_access shuttel_portal_api_user"

_TOKEN_PATH = f"/auth/realms/{SHUTTEL_REALM}/protocol/openid-connect/token"

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


class TokenClient:
    """Holds a Keycloak access token, refreshing it as needed."""

    def __init__(
        self,
        credentials: ShuttelCredentials,
        base_url: str = SHUTTEL_BASE,
        transport: httpx.BaseTransport | None = None,
    ):
        self._creds = credentials
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url, transport=transport, timeout=30.0
        )
        self._access: str = ""
        self._refresh: str = ""
        self._expires_at: float = 0.0

    def access_token(self) -> str:
        if self._access and time.time() < self._expires_at - _EXPIRY_MARGIN_S:
            return self._access
        if self._refresh and self._try_refresh():
            return self._access
        return self._password_grant()

    def _store(self, payload: dict) -> str:
        self._access = payload.get("access_token", "")
        self._refresh = payload.get("refresh_token", "") or self._refresh
        self._expires_at = time.time() + float(payload.get("expires_in", 0))
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
            # Deliberately not including the request body, which holds the
            # password. Keycloak's own error code is enough to act on.
            code = ""
            try:
                code = resp.json().get("error", "")
            except Exception:
                pass
            raise ShuttelAuthError(
                f"Shuttel rejected the login (HTTP {resp.status_code} {code}). "
                "If the shuttel-portal client has Direct Access Grants "
                "disabled, this flow cannot work and authorization-code + PKCE "
                "is required instead."
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
