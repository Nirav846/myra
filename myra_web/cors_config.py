"""CORS allow-list for the MYRA API.

Kept in its own module, with no imports from the application, so the policy can
be tested without constructing the whole FastAPI app (whose module-level
routers touch the database and therefore trip the live-database test guard).
"""

from __future__ import annotations

import os

#: Local development origins. The Vite dev server runs `--host=0.0.0.0`, so it
#: serves - and advertises - loopback-by-IP and LAN origins as well as
#: `localhost`. Naming only `localhost` meant a page opened on any other origin
#: got no `Access-Control-Allow-Origin` and the browser reduced the whole
#: request to "Failed to fetch", even though the API was healthy.
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://0.0.0.0:3000",
    "http://0.0.0.0:5173",
)


def allowed_origins() -> list[str]:
    """Origins the browser may call this API from.

    `MYRA_ALLOWED_ORIGINS` (comma-separated) overrides the defaults for a split
    deployment. Trailing slashes are stripped because the browser compares the
    Origin header literally and a trailing slash would never match.

    This is a single-user local tool that also binds 0.0.0.0, so origin checks
    are defence in depth for the dev-proxy arrangement rather than a security
    boundary; the listener's reach is the real boundary.
    """
    configured = os.environ.get("MYRA_ALLOWED_ORIGINS", "").strip()
    if configured:
        origins = [o.strip().rstrip("/") for o in configured.split(",") if o.strip()]
        if origins:
            return origins
    return list(DEFAULT_ALLOWED_ORIGINS)
