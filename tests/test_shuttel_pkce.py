from __future__ import annotations

import base64
import hashlib
import json
import os
import re

import httpx
import pytest

from src.adapters.shuttel import (
    SHUTTEL_CLIENT_ID,
    ShuttelAuthError,
    ShuttelCredentials,
    TokenClient,
    TokenStore,
    authorize_url,
    extract_code,
    new_verifier,
    verifier_challenge,
)


# ---- PKCE primitives ------------------------------------------------------

def test_the_challenge_is_unpadded_base64url_sha256_of_the_verifier():
    """RFC 7636 S256. Getting the padding wrong fails only at the very last
    step, after the human has already logged in."""
    v = "a" * 64
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(v.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert verifier_challenge(v) == expected
    assert "=" not in verifier_challenge(v)


def test_a_fresh_verifier_is_within_the_allowed_length_and_charset():
    v = new_verifier()
    assert 43 <= len(v) <= 128
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", v)


def test_two_verifiers_differ():
    assert new_verifier() != new_verifier()


# ---- authorization URL ----------------------------------------------------

def test_the_authorize_url_carries_everything_keycloak_needs():
    url = httpx.URL(authorize_url("VER", state="ST"))
    q = dict(url.params)
    assert url.path == "/auth/realms/shuttel/protocol/openid-connect/auth"
    assert q["client_id"] == SHUTTEL_CLIENT_ID
    assert q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256"
    assert q["code_challenge"] == verifier_challenge("VER")
    assert q["state"] == "ST"
    # Deliberately not the app's own /n/callback -- the Flutter router
    # consumes the code there before a human can read it.
    assert q["redirect_uri"] == "https://mijn.shuttel.nl/robots.txt"
    assert "/n/callback" not in q["redirect_uri"]


def test_offline_access_is_requested_so_the_token_survives_the_session():
    """Without it the refresh token dies with the browser session and the
    whole point -- unattended runs -- is lost."""
    q = dict(httpx.URL(authorize_url("VER")).params)
    assert "offline_access" in q["scope"]


# ---- pasting the callback back --------------------------------------------

def test_a_pasted_callback_url_yields_its_code():
    url = "https://mijn.shuttel.nl/robots.txt?state=x&code=THE-CODE&session_state=y"
    assert extract_code(url) == "THE-CODE"


def test_a_bare_code_is_accepted_as_is():
    assert extract_code("  THE-CODE  ") == "THE-CODE"


def test_a_callback_carrying_an_error_is_reported_not_silently_empty():
    url = "https://mijn.shuttel.nl/robots.txt?error=access_denied&error_description=nope"
    with pytest.raises(ShuttelAuthError) as exc:
        extract_code(url)
    assert "access_denied" in str(exc.value)


# ---- token store ----------------------------------------------------------

def test_the_token_store_round_trips_and_is_owner_only(tmp_path):
    """A refresh token is a credential: it must not be group or world readable."""
    path = tmp_path / "token.json"
    store = TokenStore(path)
    assert store.load() == ""
    store.save("REFRESH")
    assert store.load() == "REFRESH"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_a_corrupt_token_file_reads_as_absent_rather_than_crashing(tmp_path):
    path = tmp_path / "token.json"
    path.write_text("not json")
    assert TokenStore(path).load() == ""


# ---- exchange and reuse ---------------------------------------------------

def transport(*responses):
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request):
        seen.append(request)
        return queue.pop(0)

    t = httpx.MockTransport(handler)
    t.seen = seen
    return t


def token_response(access="a1", refresh="r1", expires_in=300):
    return httpx.Response(200, json={
        "access_token": access, "refresh_token": refresh,
        "expires_in": expires_in, "token_type": "Bearer",
    })


def form(request):
    return dict(httpx.QueryParams(request.content.decode()))


def test_exchanging_a_code_sends_the_verifier_and_stores_the_refresh_token(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    t = transport(token_response(refresh="R"))
    client = TokenClient(ShuttelCredentials(), transport=t, store=store)
    client.exchange_code("CODE", "VER")
    body = form(t.seen[0])
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "CODE"
    assert body["code_verifier"] == "VER"
    assert body["redirect_uri"] == "https://mijn.shuttel.nl/robots.txt"
    assert store.load() == "R"


def test_a_stored_refresh_token_is_used_instead_of_a_password(tmp_path):
    """The whole point of the PKCE detour: after one browser login, runs are
    unattended and no password is involved at all."""
    store = TokenStore(tmp_path / "t.json")
    store.save("STORED")
    t = transport(token_response(access="fresh"))
    client = TokenClient(ShuttelCredentials(), transport=t, store=store)
    assert client.access_token() == "fresh"
    body = form(t.seen[0])
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "STORED"


def test_a_rotated_refresh_token_is_written_back(tmp_path):
    """Keycloak rotates refresh tokens; dropping the new one means the next
    unattended run fails and you are back to a browser."""
    store = TokenStore(tmp_path / "t.json")
    store.save("OLD")
    t = transport(token_response(refresh="NEW"))
    TokenClient(ShuttelCredentials(), transport=t, store=store).access_token()
    assert store.load() == "NEW"


def test_no_credentials_and_no_stored_token_says_to_log_in(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    client = TokenClient(ShuttelCredentials(), transport=transport(), store=store)
    with pytest.raises(ShuttelAuthError) as exc:
        client.access_token()
    assert "shuttel_login" in str(exc.value)


def test_a_rejected_stored_token_is_not_reported_as_bad_credentials(tmp_path):
    """Falling back silently made an expired refresh token look like a wrong
    password, which sends you to re-check credentials that were fine."""
    store = TokenStore(tmp_path / "t.json")
    store.save("STALE")
    t = transport(httpx.Response(400, json={"error": "invalid_grant"}))
    client = TokenClient(ShuttelCredentials(), transport=t, store=store)
    with pytest.raises(ShuttelAuthError) as exc:
        client.access_token()
    msg = str(exc.value)
    assert "stored refresh token was rejected" in msg
    assert "shuttel_login" in msg


def test_no_session_at_all_points_at_the_login_tool_not_at_the_password(tmp_path):
    """The password grant is known not to work for this realm's direct flow,
    so a bare 'invalid credentials' is the wrong thing to lead with."""
    store = TokenStore(tmp_path / "t.json")
    t = transport(httpx.Response(400, json={
        "error": "invalid_grant", "error_description": "Invalid user credentials"}))
    client = TokenClient(
        ShuttelCredentials(username="u@x.invalid", password="p"),
        transport=t, store=store)
    with pytest.raises(ShuttelAuthError) as exc:
        client.access_token()
    assert "shuttel_login" in str(exc.value)


def test_a_stale_token_still_falls_back_to_a_working_password(tmp_path):
    """The fallback stays useful for accounts whose direct grant does work."""
    store = TokenStore(tmp_path / "t.json")
    store.save("STALE")
    t = transport(
        httpx.Response(400, json={"error": "invalid_grant"}),
        token_response(access="viaPassword"),
    )
    client = TokenClient(
        ShuttelCredentials(username="u@x.invalid", password="p"),
        transport=t, store=store)
    assert client.access_token() == "viaPassword"


def test_the_apps_own_callback_url_gets_a_targeted_explanation():
    """Pasting https://mijn.shuttel.nl/n/callback is the predictable mistake:
    it is where the app sits, and it never carries a readable code. Saying only
    'No code parameter' sends people to look at the wrong thing."""
    with pytest.raises(ShuttelAuthError) as exc:
        extract_code("https://mijn.shuttel.nl/n/callback")
    msg = str(exc.value)
    assert "the app's own page" in msg
    assert "robots.txt" in msg
