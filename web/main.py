"""Container entrypoint for the dashboard.

Binds loopback only. This app can file financial declarations; it has no
authentication because it is not reachable from anywhere that would need it,
and that assumption must stay true.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.adapters.afas_lazy import LazyAfasAdapter
from src.adapters.shuttel import ShuttelAdapter, ShuttelCredentials, TokenClient
from src.config import PROJECT_ROOT, load_config
from src.planner.engine import SyncEngine
from src.planner.store import PlanStore
from web.app import create_app


def build_default_app():
    cfg = load_config()
    store = PlanStore(Path(os.environ.get("PLANNER_DB", PROJECT_ROOT / "data" / "plan.db")))

    shuttel = ShuttelAdapter(TokenClient(ShuttelCredentials(
        username=os.environ.get("SHUTTEL_USERNAME", "").strip(),
        password=os.environ.get("SHUTTEL_PASSWORD", "").strip(),
    )))

    # AFAS costs a browser, so it is opened per operation rather than held
    # open for the life of the process.
    return create_app(store, SyncEngine(store, {
        "afas": LazyAfasAdapter(cfg),
        "shuttel": shuttel,
    }))


app = build_default_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
