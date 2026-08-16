"""Configuration for the AFAS Thuiswerkdag automation.

All AFAS-specific URLs and tunables live here (or in an optional
``config.local.toml`` next to the project root) so they are not scattered
through the code. Nothing secret belongs in this file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# AFAS InSite URLs
#
# The environment number is *not* hard-coded, so this repository is not tied to
# any one organisation's AFAS environment. Set AFAS_TENANT in .env (or
# afas.tenant in config.local.toml) to the leading digits of your portal
# hostname: https://12345.afasinsite.nl => "12345".
# --------------------------------------------------------------------------
DEFAULT_TENANT = ""

# AFAS's own page paths. Identical across the environments seen so far; override
# them in config.local.toml if yours differ.
#
# Note the first one is a *tile* page listing declaration types — it contains no
# declaration rows. The real grid is PATH_DECLARATIONS, which is what duplicate
# detection reads.
PATH_DECLARATIONS_PORTAL = "/portal-medewerker-declaratie-prs/mijn-declaraties"
PATH_DECLARATIONS = "/mijn-declaraties-prs/overzicht"
PATH_THUISWERKDAG = (
    "/aanmaken-verzameldeclaratie-ess-incl-autorisatie-prs"
    "/verzameldeclaratie-thuiswerkdag"
)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def base_url_for(tenant: str) -> str:
    return f"https://{tenant}.afasinsite.nl/"

# Amount the AFAS UI derives itself for a Thuiswerkdag. Display-only: the
# script never types an amount into the form.
DEFAULT_AMOUNT = "€2.00"

# Text AFAS uses to label this declaration type. Used for duplicate detection,
# so that a same-date declaration of a *different* type is not mistaken for a
# Thuiswerkdag. Matching is case-insensitive and accent-insensitive.
THUISWERKDAG_LABELS: tuple[str, ...] = ("thuiswerkdag", "thuiswerken", "thuiswerk")


@dataclass(frozen=True)
class Config:
    tenant: str = DEFAULT_TENANT
    base_url: str = ""
    declarations_url: str = ""
    declarations_portal_url: str = ""
    thuiswerkdag_url: str = ""
    default_amount: str = DEFAULT_AMOUNT
    thuiswerkdag_labels: tuple[str, ...] = THUISWERKDAG_LABELS

    # Filesystem
    profile_dir: Path = field(default=PROJECT_ROOT / ".browser-profile")
    artifacts_dir: Path = field(default=PROJECT_ROOT / "artifacts")

    # Browser
    headless: bool = False
    # Explicit Chromium binary. Empty => auto-detect (needed on NixOS, where
    # Playwright's bundled Chromium is not dynamically linkable).
    chromium_executable: str = ""
    slow_mo_ms: int = 0

    # Timeouts (milliseconds)
    nav_timeout_ms: int = 45_000
    action_timeout_ms: int = 20_000
    # How long to wait for the human to finish login/MFA on first run.
    # Generous on purpose: SSO + MFA can take a while, and timing out here
    # costs a full re-run.
    login_timeout_ms: int = 900_000

    def with_overrides(self, **kwargs) -> "Config":
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean)


def _detect_chromium() -> str:
    """Find a usable Chromium.

    Playwright's downloaded Chromium does not run on NixOS (it is not patched
    for the Nix dynamic loader), so prefer a system binary when one exists.
    Returning "" lets Playwright use its own bundled browser.
    """
    override = os.environ.get("AFAS_CHROMIUM")
    if override:
        return override
    for candidate in (
        "/run/current-system/sw/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if Path(candidate).exists():
            return candidate
    return ""


def _load_env_file() -> None:
    """Make .env values visible in os.environ (no-op if absent)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:  # pragma: no cover
        pass


def load_config() -> Config:
    """Build config from defaults, ``.env`` and ``config.local.toml``.

    Precedence (later wins): built-in defaults < .env < config.local.toml.
    """
    _load_env_file()

    tenant = os.environ.get("AFAS_TENANT", DEFAULT_TENANT).strip()
    overrides: dict = {}

    local = PROJECT_ROOT / "config.local.toml"
    if local.exists():
        data = tomllib.loads(local.read_text(encoding="utf-8"))
        afas = data.get("afas", {})
        browser = data.get("browser", {})
        tenant = (afas.get("tenant") or tenant).strip()
        overrides = {
            "base_url": afas.get("base_url"),
            "declarations_url": afas.get("declarations_url"),
            "declarations_portal_url": afas.get("declarations_portal_url"),
            "thuiswerkdag_url": afas.get("thuiswerkdag_url"),
            "default_amount": afas.get("default_amount"),
            "headless": browser.get("headless"),
            "chromium_executable": browser.get("chromium_executable"),
            "slow_mo_ms": browser.get("slow_mo_ms"),
        }

    cfg = Config(tenant=tenant, chromium_executable=_detect_chromium())

    # Derive URLs from the tenant unless explicitly overridden.
    if tenant:
        base = base_url_for(tenant)
        cfg = cfg.with_overrides(
            base_url=base,
            declarations_url=base.rstrip("/") + PATH_DECLARATIONS,
            declarations_portal_url=base.rstrip("/") + PATH_DECLARATIONS_PORTAL,
            thuiswerkdag_url=base.rstrip("/") + PATH_THUISWERKDAG,
        )

    cfg = cfg.with_overrides(**overrides)

    if not cfg.base_url or not cfg.declarations_url or not cfg.thuiswerkdag_url:
        raise ConfigError(
            "AFAS environment not configured.\n"
            "Set AFAS_TENANT in .env to your AFAS InSite environment number — "
            "the leading digits of your portal hostname\n"
            "  (https://12345.afasinsite.nl  =>  AFAS_TENANT=12345)\n"
            "See README.md > Setup."
        )
    return cfg
