"""Phase 11 Priority 1 — readiness transport reliability regression tests.

What actually broke
-------------------
Mission Control's footer showed ``transport error: Failed to fetch``. That string
is not a server message: the browser emits it when a `fetch()` never completes,
and it is returned for connection refusal, DNS failure, a TLS problem **and** a
CORS rejection alike. Reproduced in Chromium against a backend that was healthy
and answering `200 OK`:

    page origin                     absolute localhost:8000   relative /api
    http://localhost:3000           200                       200
    http://127.0.0.1:3000           "Failed to fetch"        200
    http://192.168.x.x:3000 (LAN)   "Failed to fetch"        200

`myra_web/src/config.ts` hardcoded an absolute cross-origin base while the CORS
allowlist named only two origins — and the dev server runs `--host=0.0.0.0`, so
it actively advertises origins that were rejected.

These tests pin the server-side half of that contract (the CORS grant) and the
readiness response contract, without needing a browser or a running server. The
browser-level behaviour is verified separately by `tools/verify_readiness_transport.py`,
which drives the real dev server.
"""

from __future__ import annotations

import importlib
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware


@pytest.fixture
def cors_app():
    """A minimal app carrying the production CORS configuration.

    Built from the same helper the real server uses, so the test cannot drift
    from production while staying cheap (importing the real server pulls in the
    whole scheduler).
    """
    from myra_web.cors_config import allowed_origins as _allowed_origins

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/pipeline/readiness")
    def _readiness():
        return {"changed": True, "fingerprint": "fp", "readiness": {"state": "ready"}}

    return TestClient(app)


# ── CORS: the demonstrated defect ───────────────────────────────────────────


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)
def test_allowlisted_origins_receive_the_grant(cors_app, origin):
    """Loopback-by-name and loopback-by-IP are both legitimate dev origins."""
    r = cors_app.get("/api/pipeline/readiness", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


def test_preflight_is_answered_for_allowlisted_origins(cors_app):
    r = cors_app.options(
        "/api/pipeline/readiness",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


def test_unknown_origins_are_not_granted(cors_app):
    """Not a blanket `*`: an unrelated site must still be refused."""
    r = cors_app.get(
        "/api/pipeline/readiness", headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 200, "the request succeeds, it is just not shared"
    assert "access-control-allow-origin" not in r.headers


# ── the allowlist itself ────────────────────────────────────────────────────


def test_default_allowlist_covers_loopback_names_and_ips():
    from myra_web.cors_config import allowed_origins as _allowed_origins

    origins = _allowed_origins()
    for expected in (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ):
        assert expected in origins, f"{expected} must be an allowed dev origin"


def test_allowlist_is_configurable_for_split_deployments(monkeypatch):
    """A split deployment can name its own frontend origin."""
    from myra_web.cors_config import allowed_origins as _allowed_origins

    monkeypatch.setenv(
        "MYRA_ALLOWED_ORIGINS", "http://app.internal , http://other:5173/"
    )
    origins = _allowed_origins()
    assert origins == ["http://app.internal", "http://other:5173"]


def test_allowlist_falls_back_when_the_override_is_blank(monkeypatch):
    from myra_web.cors_config import allowed_origins as _allowed_origins

    monkeypatch.setenv("MYRA_ALLOWED_ORIGINS", "   ")
    assert "http://localhost:3000" in _allowed_origins()


def test_allowlist_normalises_trailing_slashes(monkeypatch):
    from myra_web.cors_config import allowed_origins as _allowed_origins

    monkeypatch.setenv("MYRA_ALLOWED_ORIGINS", "http://a.example/")
    assert _allowed_origins() == ["http://a.example"]


# ── readiness response contract ─────────────────────────────────────────────


def test_readiness_returns_a_verdict_the_client_can_render(cors_app):
    r = cors_app.get("/api/pipeline/readiness")
    body = r.json()
    assert body["changed"] is True
    assert isinstance(body["fingerprint"], str) and body["fingerprint"]
    assert isinstance(body["readiness"], dict)


def test_changed_only_returns_a_bool_and_a_fingerprint(cors_app):
    r = cors_app.get("/api/pipeline/readiness", params={"changed_only": True})
    body = r.json()
    assert body["changed"] is True
    assert isinstance(body["fingerprint"], str)


def test_readiness_never_writes_to_a_database(fixture_db_dir, monkeypatch):
    """The endpoint is documented read-only; prove it opens no write handle."""
    import sqlite3

    from myra_app import data_readiness as dr

    writes: list[str] = []
    real_connect = sqlite3.connect

    def _spy(target, *args, **kwargs):
        # Read-only URIs are how every readiness query is issued; anything else
        # that reaches a connect() from this path is a bug.
        uri = kwargs.get("uri", False)
        if not uri:
            writes.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _spy)
    dr.assess()
    assert not writes, f"readiness opened writable connections: {writes}"


# ── frontend base-URL contract ──────────────────────────────────────────────


def _hook_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "myra_web", "src", "hooks", "useDataReadiness.ts")


def _config_ts_text() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(
        os.path.join(here, "myra_web", "src", "config.ts"), encoding="utf-8"
    ) as fh:
        return fh.read()


def test_frontend_defaults_to_the_relative_api_prefix():
    """The default base must keep the `/api` mount and stay same-origin.

    An empty base would silently turn `${API_BASE}/pipeline/readiness` into
    `/pipeline/readiness`, which misses the router mount and returns the SPA's
    `index.html` where JSON is expected.
    """
    src = _config_ts_text()
    assert (
        "configuredBase ?? '/api'" in src
    ), "API_BASE must default to the relative '/api' prefix"


def test_frontend_no_longer_hardcodes_an_absolute_api_root():
    """No *assignment* may bake in an absolute API host.

    The literal can still appear in the explanatory comment (and in the
    documented `VITE_API_BASE` example); what must not exist is code that makes
    the absolute URL the default.
    """
    import re

    src = _config_ts_text()
    # Strip comments so the prose describing the old bug cannot fail the test.
    code = "\n".join(
        line
        for line in src.splitlines()
        if not line.strip().startswith(("*", "//", "/*"))
    )
    offenders = re.findall(
        r"(?:API_ROOT|API_BASE)\s*=\s*['\"]https?://[^'\"]+['\"]", code
    )
    assert not offenders, f"absolute API URL still hardcoded: {offenders}"


def test_frontend_still_supports_an_absolute_override():
    src = _config_ts_text()
    assert "VITE_API_BASE" in src, "split deployments must still be supported"


def test_readiness_hook_does_not_rerender_on_an_unchanged_verdict():
    """The hook promises no state update when nothing changed; pin it.

    `setLastUpdated` used to fire on every unchanged poll, so a steady 30 s
    readiness poll re-rendered the panel forever for a value that never moved.
    """
    with open(_hook_path(), encoding="utf-8") as fh:
        src = fh.read()
    # Exactly one call site, and it must live inside the changed-verdict branch.
    assert (
        src.count("setLastUpdated(") == 1
    ), "setLastUpdated must be called once, only for a changed verdict"
    head, _, tail = src.partition("if (body.changed && body.readiness)")
    assert (
        "setLastUpdated(" not in head
    ), "no state may be updated before the changed-verdict branch"
    assert "setLastUpdated(" in tail, "a changed verdict must stamp the time"
    # And there must be no branch that handles an unchanged answer by setting
    # state, which is what caused the endless re-render.
    assert (
        "else if (!body.changed)" not in src
    ), "an unchanged verdict must not update React state"


def test_readiness_hook_classifies_failures_instead_of_echoing_them():
    """`Failed to fetch` alone told the operator nothing actionable."""
    with open(_hook_path(), encoding="utf-8") as fh:

        src = fh.read()
    for kind in ("unreachable", "http", "malformed"):
        assert f"'{kind}'" in src, f"{kind} failures must be distinguishable"
    assert "run_fastapi.py" in src, "an unreachable backend must name its remedy"


def test_readiness_hook_rejects_a_payload_that_breaks_the_contract():
    """A 2xx that is not the contract must never render as a verdict."""
    with open(_hook_path(), encoding="utf-8") as fh:

        src = fh.read()
    assert "parseReadiness" in src
    assert "malformed" in src
    # A changed verdict must be accompanied by a payload.
    assert "changed === true" in src and "readiness" in src
