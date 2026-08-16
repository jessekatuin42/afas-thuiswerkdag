"""Make Playwright importable/launchable on NixOS.

Two NixOS-specific problems, both handled here so the documented command
(``python afas_thuiswerk.py ...``) just works:

1. ``greenlet`` (a Playwright dependency) needs ``libstdc++.so.6``, which is
   not on the default loader path. ``LD_LIBRARY_PATH`` is read by the dynamic
   loader at process start, so setting it from Python is too late — we must
   re-exec the interpreter once.
2. Playwright's *bundled* Chromium is not patched for the Nix loader and dies
   on launch; ``config._detect_chromium`` prefers the system binary instead.

On non-Nix systems both steps are no-ops.
"""

from __future__ import annotations

import glob
import os
import sys

_GUARD = "AFAS_THUISWERK_RESPAWNED"


def _gcc_lib_dirs() -> list[str]:
    dirs = sorted(glob.glob("/nix/store/*-gcc-*-lib/lib"))
    return [d for d in dirs if os.path.exists(os.path.join(d, "libstdc++.so.6"))]


def ensure_native_libs() -> None:
    """Re-exec with a usable LD_LIBRARY_PATH if native deps cannot load."""
    if os.environ.get(_GUARD):
        return  # already tried once; do not loop
    if "pytest" in sys.modules:
        # Re-exec'ing under pytest would inherit its captured stdout/stderr and
        # silently swallow the entire run. Tests must set the environment up
        # front instead (see tests/conftest.py).
        return
    try:
        import greenlet  # noqa: F401
        return
    except ImportError as exc:
        if "libstdc++" not in str(exc):
            raise

    libs = _gcc_lib_dirs()
    if not libs:
        raise RuntimeError(
            "libstdc++.so.6 not found. On NixOS run inside "
            "`nix-shell -p stdenv.cc.cc.lib`, or set LD_LIBRARY_PATH manually."
        )

    env = dict(os.environ)
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(libs + ([existing] if existing else []))
    env[_GUARD] = "1"

    # sys.orig_argv preserves the *real* command line, including forms like
    # `python -m pytest` that sys.argv would silently drop.
    argv = list(getattr(sys, "orig_argv", [])) or [sys.executable] + sys.argv
    os.execve(argv[0], argv, env)
