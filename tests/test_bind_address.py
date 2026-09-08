from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_compose_publishes_on_loopback_only():
    """The host-side publish is the real boundary.

    The container binds 0.0.0.0 on its own network namespace -- it has to, or a
    published port could never reach it. So this one line is the only thing
    keeping a dashboard that files financial declarations off the LAN.
    """
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "127.0.0.1:8765:8765" in compose
    assert '"8765:8765"' not in compose
    assert "- 8765:8765" not in compose


def test_main_defaults_to_loopback():
    """Running web/main.py directly on the host has no container namespace to
    hide behind, so it must bind loopback itself."""
    main = (ROOT / "web" / "main.py").read_text()
    assert '"127.0.0.1"' in main
    assert "0.0.0.0" not in main
