"""Container entrypoint for the dashboard.

Binds loopback only. This app can file financial declarations; it has no
authentication because it is not reachable from anywhere that would need it,
and that assumption must stay true.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.adapters.afas_lazy import LazyAfasAdapter
from src.adapters.shuttel import (
    ShuttelAdapter,
    ShuttelApi,
    ShuttelCredentials,
    TokenClient,
    TokenStore,
)
from src.config import PROJECT_ROOT, load_config
from src.planner.engine import SyncEngine
from src.planner.model import parse_home_days
from src.planner.store import PlanStore
from web.app import create_app


def build_default_app():
    cfg = load_config()
    store = PlanStore(Path(os.environ.get("PLANNER_DB", PROJECT_ROOT / "data" / "plan.db")))

    tokens = TokenClient(
        ShuttelCredentials(
            username=os.environ.get("SHUTTEL_USERNAME", "").strip(),
            password=os.environ.get("SHUTTEL_PASSWORD", "").strip(),
        ),
        store=TokenStore(PROJECT_ROOT / ".shuttel-token.json"),
    )
    shuttel = ShuttelAdapter(
        ShuttelApi(tokens),
        template_ids=tuple(
            t.strip()
            for t in os.environ.get("SHUTTEL_COMMUTE_TEMPLATES", "").split(",")
            if t.strip()
        ),
    )

    # AFAS costs a browser, so it is opened per operation rather than held
    # open for the life of the process.
    # Same AFAS_DAYS the systemd timer reads. A malformed value raises here
    # and the app refuses to start, rather than quietly marking days you did
    # not choose.
    home_days = parse_home_days(os.environ.get("AFAS_DAYS"))

    return create_app(store, SyncEngine(store, {
        "afas": LazyAfasAdapter(cfg),
        "shuttel": shuttel,
    }), home_days=home_days)


app = build_default_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
