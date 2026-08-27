"""Profile hygiene performed before Chromium is launched.

No browser is started here — these are the checks that must hold *before* one
can be, so they stay fast and run in the default suite.
"""

from __future__ import annotations

from src.browser import prune_volatile_state


def test_sync_state_is_dropped_before_launch(tmp_path):
    """Regression: a stale sync LevelDB aborts Chromium 143 at startup.

    Symptom was ``crashpad ... read out of range`` followed by SIGTRAP, which
    failed the daily timer run two days running while the profile itself looked
    perfectly healthy. This profile is never signed into a browser account, so
    the state is worthless — it may not survive into a launch.
    """
    leveldb = tmp_path / "Default" / "Sync Data" / "LevelDB"
    leveldb.mkdir(parents=True)
    (leveldb / "000003.log").write_bytes(b"\x02\x08\x01poisoned")

    prune_volatile_state(tmp_path)

    assert not (tmp_path / "Default" / "Sync Data").exists()


def test_the_logged_in_session_is_left_alone(tmp_path):
    """Pruning must not cost the AFAS session — re-login is the untested path."""
    default = tmp_path / "Default"
    (default / "Sync Data").mkdir(parents=True)
    (default / "Cookies").write_bytes(b"session")
    (default / "Preferences").write_text("{}")
    (tmp_path / "Local State").write_text("{}")

    prune_volatile_state(tmp_path)

    assert (default / "Cookies").read_bytes() == b"session"
    assert (default / "Preferences").exists()
    assert (tmp_path / "Local State").exists()


def test_missing_profile_is_not_an_error(tmp_path):
    """First run has no profile at all; pruning must be a no-op, not a crash."""
    prune_volatile_state(tmp_path / "does-not-exist")
