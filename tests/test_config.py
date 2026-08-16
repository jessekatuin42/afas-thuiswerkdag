"""Configuration: URL derivation from the tenant, and overrides."""

from __future__ import annotations

import pytest

import src.config as config_module
from src.config import (
    PATH_DECLARATIONS,
    PATH_THUISWERKDAG,
    ConfigError,
    base_url_for,
    load_config,
)


@pytest.fixture
def clean_root(tmp_path, monkeypatch):
    """Pretend we are a fresh clone: no .env, no config.local.toml."""
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    return tmp_path


class TestBaseUrl:
    def test_builds_portal_hostname(self):
        assert base_url_for("12345") == "https://12345.afasinsite.nl/"


class TestTenantRequired:
    def test_missing_tenant_raises_with_guidance(self, clean_root, monkeypatch):
        monkeypatch.setenv("AFAS_TENANT", "")
        with pytest.raises(ConfigError) as exc:
            load_config()
        assert "AFAS_TENANT" in str(exc.value)

    def test_no_organisation_is_hard_coded(self):
        """A public repo must not ship somebody's environment number."""
        assert config_module.DEFAULT_TENANT == ""


class TestUrlDerivation:
    def test_urls_follow_the_tenant(self, clean_root, monkeypatch):
        monkeypatch.setenv("AFAS_TENANT", "12345")
        cfg = load_config()
        assert cfg.base_url == "https://12345.afasinsite.nl/"
        assert cfg.declarations_url == "https://12345.afasinsite.nl" + PATH_DECLARATIONS
        assert cfg.thuiswerkdag_url == "https://12345.afasinsite.nl" + PATH_THUISWERKDAG

    def test_declarations_url_is_the_grid_not_the_tile_page(self, clean_root, monkeypatch):
        """The menu page has no rows; detection must read the overview grid."""
        monkeypatch.setenv("AFAS_TENANT", "12345")
        cfg = load_config()
        assert cfg.declarations_url != cfg.declarations_portal_url
        assert cfg.declarations_url.endswith("/mijn-declaraties-prs/overzicht")

    def test_tenant_read_from_env_file(self, clean_root, monkeypatch):
        monkeypatch.delenv("AFAS_TENANT", raising=False)
        (clean_root / ".env").write_text("AFAS_TENANT=54321\n", encoding="utf-8")
        assert load_config().base_url == "https://54321.afasinsite.nl/"


class TestLocalTomlOverrides:
    def test_toml_tenant_wins_over_env(self, clean_root, monkeypatch):
        monkeypatch.setenv("AFAS_TENANT", "11111")
        (clean_root / "config.local.toml").write_text(
            '[afas]\ntenant = "22222"\n', encoding="utf-8"
        )
        assert load_config().base_url == "https://22222.afasinsite.nl/"

    def test_explicit_url_overrides_derived_one(self, clean_root, monkeypatch):
        monkeypatch.setenv("AFAS_TENANT", "12345")
        (clean_root / "config.local.toml").write_text(
            '[afas]\ndeclarations_url = "https://example.test/grid"\n', encoding="utf-8"
        )
        cfg = load_config()
        assert cfg.declarations_url == "https://example.test/grid"
        # Untouched URLs still derive from the tenant.
        assert cfg.thuiswerkdag_url.startswith("https://12345.afasinsite.nl")

    def test_browser_settings_override(self, clean_root, monkeypatch):
        monkeypatch.setenv("AFAS_TENANT", "12345")
        (clean_root / "config.local.toml").write_text(
            "[browser]\nheadless = true\nslow_mo_ms = 250\n", encoding="utf-8"
        )
        cfg = load_config()
        assert cfg.headless is True and cfg.slow_mo_ms == 250

    def test_headed_is_the_default(self, clean_root, monkeypatch):
        monkeypatch.setenv("AFAS_TENANT", "12345")
        assert load_config().headless is False
