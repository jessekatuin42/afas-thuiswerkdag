from __future__ import annotations

import httpx

from src.adapters.shuttel import (
    INSPECT_ENDPOINTS,
    ShuttelClient,
    ShuttelCredentials,
    TokenClient,
)


def recording_transport(status=200, payload=None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={
                "access_token": "tok", "refresh_token": "r",
                "expires_in": 300, "token_type": "Bearer",
            })
        return httpx.Response(status, json=payload if payload is not None else {"ok": True})

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def client_with(transport):
    creds = ShuttelCredentials(username="u@example.invalid", password="p")
    return ShuttelClient(TokenClient(creds, transport=transport), transport=transport)


def test_inspection_never_issues_anything_but_a_get():
    """This tool exists so payloads are never guessed. It must not be able to
    create a declaration by accident while exploring."""
    t = recording_transport()
    client_with(t).inspect()
    api_calls = [r for r in t.seen if not r.url.path.endswith("/token")]
    assert api_calls
    assert {r.method for r in api_calls} == {"GET"}


def test_every_api_call_carries_the_bearer_token():
    t = recording_transport()
    client_with(t).inspect()
    api_calls = [r for r in t.seen if not r.url.path.endswith("/token")]
    assert all(r.headers.get("authorization") == "Bearer tok" for r in api_calls)


def test_bank_and_address_endpoints_are_not_collected():
    """They exist in the API but nothing here needs them, and inspection output
    lands in artifacts/ as plain JSON."""
    joined = " ".join(INSPECT_ENDPOINTS)
    assert "bankaccount" not in joined
    assert "postal_address" not in joined


def test_the_openapi_spec_is_collected_because_it_answers_everything_else():
    assert any("openapi" in p for p in INSPECT_ENDPOINTS)


def test_a_failing_endpoint_is_reported_rather_than_aborting_the_run():
    """A 401 on one endpoint must not lose the others -- entitlements differ
    per account and a partial capture is still useful."""
    t = recording_transport(status=401, payload={"error": "nope"})
    result = client_with(t).inspect()
    assert set(result) == set(INSPECT_ENDPOINTS)
    assert all(v["status"] == 401 for v in result.values())


def test_a_successful_body_is_captured():
    t = recording_transport(payload={"codes": [{"code": "km_commute"}]})
    result = client_with(t).inspect()
    assert result["/api/v1/profile/declaration_codes"]["body"] == {
        "codes": [{"code": "km_commute"}]
    }
