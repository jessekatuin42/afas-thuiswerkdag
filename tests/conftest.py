"""Test bootstrap.

Makes the project importable without installing it, and satisfies the native
library requirement *in-process* rather than by re-exec — ``src.nixshim``
deliberately refuses to re-exec under pytest, because that would inherit
pytest's captured stdout and swallow the whole run.
"""

import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Hermetic config: tests must pass on a fresh clone with no .env. Setting this
# up front also wins over any real .env (which is loaded with override=False),
# so a developer's own environment number never leaks into test expectations.
os.environ.setdefault("AFAS_TENANT", "00000")

# On NixOS, pre-load libstdc++ so `greenlet` (and therefore Playwright) imports.
# Harmless elsewhere: if greenlet already imports, we do nothing.
try:  # pragma: no cover - environment dependent
    import greenlet  # noqa: F401
except ImportError:  # pragma: no cover
    from src.nixshim import _gcc_lib_dirs

    for _dir in _gcc_lib_dirs():
        try:
            ctypes.CDLL(os.path.join(_dir, "libstdc++.so.6"), mode=ctypes.RTLD_GLOBAL)
            break
        except OSError:
            continue
