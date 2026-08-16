"""Credential handling and TOTP generation.

The most important property here is negative: credentials must never be
representable as text, so an accidental print or a stack trace cannot leak
them.
"""

from __future__ import annotations

import time

import pytest

from src.auth import Credentials, load_credentials, seconds_until_next_totp, totp_now

# RFC 4648 base32; this is a throwaway test vector, not a real secret.
TEST_SECRET = "JBSWY3DPEHPK3PXP"


class TestCredentialsDoNotLeak:
    def test_repr_hides_values(self):
        c = Credentials(username="me@example.com", password="hunter2",
                        totp_secret=TEST_SECRET)
        assert "hunter2" not in repr(c)
        assert TEST_SECRET not in repr(c)
        assert "me@example.com" not in repr(c)
        assert "set" in repr(c)

    def test_str_hides_values(self):
        c = Credentials(password="hunter2")
        assert "hunter2" not in str(c)

    def test_format_hides_values(self):
        c = Credentials(password="hunter2")
        assert "hunter2" not in f"{c}"

    def test_describe_names_fields_not_values(self):
        c = Credentials(username="me@example.com", password="hunter2")
        described = c.describe()
        assert described == "username, password"
        assert "hunter2" not in described

    def test_describe_empty(self):
        assert Credentials().describe() == "none"


class TestAutologinGate:
    def test_password_enables_autologin(self):
        assert Credentials(password="x").can_autologin

    def test_no_password_disables_autologin(self):
        assert not Credentials(username="me", totp_secret=TEST_SECRET).can_autologin
        assert not Credentials().can_autologin


class TestLoadCredentials(object):
    def test_reads_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFAS_USERNAME", "me@example.com")
        monkeypatch.setenv("AFAS_PASSWORD", "hunter2")
        monkeypatch.setenv("AFAS_TOTP_SECRET", TEST_SECRET)
        c = load_credentials(tmp_path)
        assert c.username == "me@example.com"
        assert c.can_autologin

    def test_missing_values_are_empty_not_errors(self, tmp_path, monkeypatch):
        for var in ("AFAS_USERNAME", "AFAS_PASSWORD", "AFAS_TOTP_SECRET"):
            monkeypatch.delenv(var, raising=False)
        c = load_credentials(tmp_path)
        assert not c.can_autologin
        assert c.describe() == "none"

    def test_totp_secret_spaces_are_stripped(self, tmp_path, monkeypatch):
        """Authenticator apps display the key in space-separated groups."""
        monkeypatch.setenv("AFAS_TOTP_SECRET", "JBSW Y3DP EHPK 3PXP")
        assert load_credentials(tmp_path).totp_secret == TEST_SECRET

    def test_reads_dotenv_file(self, tmp_path, monkeypatch):
        for var in ("AFAS_USERNAME", "AFAS_PASSWORD", "AFAS_TOTP_SECRET"):
            monkeypatch.delenv(var, raising=False)
        (tmp_path / ".env").write_text(
            "AFAS_USERNAME=file@example.com\nAFAS_PASSWORD=frompw\n", encoding="utf-8"
        )
        c = load_credentials(tmp_path)
        assert c.username == "file@example.com"
        assert c.can_autologin


class TestTotp:
    def test_generates_six_digits(self):
        code = totp_now(TEST_SECRET)
        assert len(code) == 6 and code.isdigit()

    def test_matches_reference_implementation(self):
        import pyotp

        assert pyotp.TOTP(TEST_SECRET).verify(totp_now(TEST_SECRET))

    def test_rejects_a_non_base32_secret(self):
        with pytest.raises(Exception):
            totp_now("not-a-valid-secret!!")

    def test_seconds_until_next_code_is_within_window(self):
        remaining = seconds_until_next_totp()
        assert 0 < remaining <= 30

    def test_code_is_stable_within_the_same_window(self):
        if seconds_until_next_totp() < 3:
            time.sleep(3.5)  # avoid straddling a rotation
        assert totp_now(TEST_SECRET) == totp_now(TEST_SECRET)
