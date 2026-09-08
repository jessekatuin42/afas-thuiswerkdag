from __future__ import annotations

import json

import httpx
import pytest

from src.adapters.shuttel import (
    SHUTTEL_CLIENT_ID,
    ShuttelAuthError,
    ShuttelCredentials,
    TokenClient,
)

CREDS = ShuttelCredentials(username="someone@example.invalid", password="pw")


def transport_returning(*responses):
    """Serve the given responses in order, recording each request."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def token_response(access="a1", refresh="r1", expires_in=300):
    return httpx.Response(200, json={
        "access_token": access, "refresh_token": refresh,
        "expires_in": expires_in, "token_type": "Bearer",
    })


def form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


def test_first_call_uses_the_password_grant_with_the_portal_client():
    t = transport_returning(token_response())
    assert TokenClient(CREDS, transport=t).access_token() == "a1"
    body = form(t.seen[0])
    assert body["grant_type"] == "password"
    assert body["client_id"] == SHUTTEL_CLIENT_ID
    assert "offline_access" in body["scope"]


def test_token_endpoint_is_the_shuttel_realm():
    t = transport_returning(token_response())
    TokenClient(CREDS, transport=t).access_token()
    assert t.seen[0].url.path == "/auth/realms/shuttel/protocol/openid-connect/token"


def test_a_valid_token_is_reused_rather_than_refetched():
    t = transport_returning(token_response())
    client = TokenClient(CREDS, transport=t)
    assert client.access_token() == client.access_token()
    assert len(t.seen) == 1


def test_an_expired_token_is_refreshed_not_re_passworded():
    t = transport_returning(
        token_response(access="a1", expires_in=0),
        token_response(access="a2"),
    )
    client = TokenClient(CREDS, transport=t)
    assert client.access_token() == "a1"
    assert client.access_token() == "a2"
    assert form(t.seen[1])["grant_type"] == "refresh_token"


def test_a_rejected_password_raises_without_echoing_the_password():
    t = transport_returning(httpx.Response(401, json={"error": "invalid_grant"}))
    with pytest.raises(ShuttelAuthError) as exc:
        TokenClient(CREDS, transport=t).access_token()
    assert CREDS.password not in str(exc.value)


def test_a_failed_refresh_falls_back_to_the_password_grant():
    t = transport_returning(
        token_response(access="a1", expires_in=0),
        httpx.Response(400, json={"error": "invalid_grant"}),
        token_response(access="a3"),
    )
    client = TokenClient(CREDS, transport=t)
    client.access_token()
    assert client.access_token() == "a3"
    assert form(t.seen[2])["grant_type"] == "password"


def test_a_disabled_grant_is_distinguished_from_a_bad_password():
    """Keycloak says unauthorized_client when the client forbids the grant, and
    invalid_grant when the login simply failed. Conflating them sends you to
    fix the wrong thing."""
    t = transport_returning(httpx.Response(400, json={
        "error": "unauthorized_client",
        "error_description": "Client not allowed for direct access grants",
    }))
    with pytest.raises(ShuttelAuthError) as exc:
        TokenClient(CREDS, transport=t).access_token()
    assert "does not permit the password grant" in str(exc.value)
    assert "PKCE" in str(exc.value)


def test_a_rejected_login_says_it_is_about_the_credentials():
    t = transport_returning(httpx.Response(400, json={
        "error": "invalid_grant", "error_description": "Invalid user credentials",
    }))
    with pytest.raises(ShuttelAuthError) as exc:
        TokenClient(CREDS, transport=t).access_token()
    assert "Invalid user credentials" in str(exc.value)
    assert "credentials or the account" in str(exc.value)


def test_credentials_never_reveal_the_password_in_repr_or_str():
    assert "pw" not in repr(CREDS)
    assert "pw" not in str(CREDS)
    assert "pw" not in json.dumps(repr(CREDS))


def test_no_account_specific_values_are_baked_into_source():
    """This repository is public. Mirrors the existing DEFAULT_TENANT == ""
    test: vendor-level facts may be committed, account-level ones may not."""
    from src.adapters import shuttel

    assert shuttel.ShuttelCredentials().username == ""
    assert shuttel.ShuttelCredentials().password == ""
    # Vendor-level and identical for every Shuttel customer -- read from the
    # portal's own unauthenticated discovery endpoints, so committing these is
    # fine. Anything account-scoped must live in .env instead.
    assert shuttel.SHUTTEL_CLIENT_ID == "shuttel-portal"
    assert shuttel.SHUTTEL_REALM == "shuttel"
