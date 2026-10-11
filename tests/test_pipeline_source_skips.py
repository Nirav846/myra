"""Pipeline tasks must report an operator-disabled source, not claim success.

When a source is switched off in the Data Sources panel the affected pipeline
tasks have nothing to fetch. They must surface that on-screen (a stage/note),
rather than reporting a clean "complete" — the repo invariant that pipeline
warnings are user-visible.
"""

import myra_app.pipeline_control as pc


class _FakeCtx:
    shutdown_event = None
    pause_event = None


class _FakeTaskRun:
    def __init__(self):
        self.ctx = _FakeCtx()
        self.stages: list[str] = []
        self.notes: list[str] = []
        self.progress_pct: list[float] = []

    def stage(self, message):
        self.stages.append(message)

    def note(self, message):
        self.notes.append(message)

    def progress(self, pct):
        self.progress_pct.append(pct)


def test_market_cap_sync_reports_screener_disabled(monkeypatch):
    import myra_app.utils.fundamentals_sync as fs

    monkeypatch.setattr(
        fs, "sync_fundamentals", lambda **_: {"skipped": "screener_disabled"}
    )
    tr = _FakeTaskRun()
    pc._run_market_cap_sync(tr)
    assert any("Screener.in is disabled" in s for s in tr.stages)


def test_shares_sync_reports_yfinance_disabled_and_skips_freshness_bump(monkeypatch):
    import myra_app.fundamental_sync as fsc

    monkeypatch.setattr(
        fsc.FundamentalSync,
        "_refresh_stale_shares_outstanding",
        lambda self, **_: {"updated": 0, "total": 0, "skipped": "yfinance_disabled"},
    )
    # Guard: the freshness rewrite must NOT run on a skipped refresh.
    import myra_app.pipeline_control as pcm

    def _boom(*_a, **_k):
        raise AssertionError("must not touch the DB when yfinance is disabled")

    monkeypatch.setattr(pcm.sqlite3, "connect", _boom)
    tr = _FakeTaskRun()
    pc._run_shares_outstanding_sync(tr)
    assert any("yfinance is disabled" in s for s in tr.stages)


def test_fundamentals_sync_flags_disabled_sources(monkeypatch):
    import myra_app.fundamental_sync as fsc

    monkeypatch.setattr(fsc, "_source_enabled", lambda name: name == "screener_in")
    monkeypatch.setattr(fsc.FundamentalSync, "_fetch_morningstar_bulk", lambda self: {})
    monkeypatch.setattr(fsc.FundamentalSync, "_get_nifty_500_symbols", lambda self: [])
    monkeypatch.setattr(
        fsc.FundamentalSync, "_fetch_nse_all", lambda self, *_a, **_k: {}
    )
    monkeypatch.setattr(
        fsc.FundamentalSync, "_merge_and_insert", lambda self, *_a, **_k: None
    )
    monkeypatch.setattr(fsc.FundamentalSync, "_log_summary", lambda self: None)

    tr = _FakeTaskRun()
    pc._run_fundamentals_sync(tr)
    assert any("Disabled in Data Sources" in n for n in tr.notes)
    assert any("morningstar" in n and "yfinance" in n for n in tr.notes)
    assert any("sources off" in s for s in tr.stages)
