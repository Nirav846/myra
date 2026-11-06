"""Cross-buy task: downloader invocation, failure surfacing, processor fallback.

The monthly task used to abort the whole sync whenever a single fund failed:
the sibling downloader is fail-closed by default (``--max-failures 0``), and
``cross_buy.py`` never passed the budget its workflow sibling uses. On
2026-09-23 that produced ``Done: 61 saved, 1 failed`` -> rc=1 -> "CSV download
failed", discarding 61 good CSVs and leaving ``fund_cross_buy`` stale.

These tests pin the caller contract without touching the network or a live DB.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import myra_app.cross_buy_processor as proc
import myra_app.tasks.cross_buy as cross_buy
from myra_app.tasks.context import TaskContext

FAIL_LINE = "FAIL 'ICICI Pru Flexicap Fund(G)': no matching fund on RupeeVest"
DONE_PARTIAL = "Done: 61/62 saved, 1 failed (max allowed failures: 3)"
DONE_CLEAN = "Done: 62/62 saved, 0 failed (max allowed failures: 3)"


def _ctx() -> TaskContext:
    return TaskContext(shutdown_event=threading.Event(), logger=cross_buy.logger)


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=stderr
    )


@pytest.fixture()
def script(tmp_path: Path) -> Path:
    path = tmp_path / "download_rupeevest_funds.py"
    path.write_text("# stub\n", encoding="utf-8")
    return path


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    path = tmp_path / "temp_holdings"
    path.mkdir()
    return path


@pytest.fixture()
def funds_file(tmp_path: Path) -> Path:
    path = tmp_path / "funds.txt"
    path.write_text("Demo Fund-Reg(G)\n", encoding="utf-8")
    return path


@pytest.fixture()
def wired(monkeypatch, script, out_dir, funds_file):
    """Point the task at tmp paths so nothing real is touched."""
    monkeypatch.setattr(cross_buy, "CROSS_BUY_DOWNLOAD_SCRIPT", script)
    monkeypatch.setattr(cross_buy, "CROSS_BUY_OUT_DIR", out_dir)
    monkeypatch.setattr(cross_buy, "CROSS_BUY_FUNDS_FILE", funds_file)
    monkeypatch.setattr(
        cross_buy, "CROSS_BUY_FUNDS_LOCAL", funds_file.parent / "missing.txt"
    )
    return cross_buy


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_download_passes_max_failures_and_overwrite(monkeypatch, wired, funds_file):
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _completed(0, DONE_CLEAN)

    monkeypatch.setattr(cross_buy.subprocess, "run", fake_run)

    assert cross_buy._download_cross_buy_csvs() is True

    cmd = captured["cmd"]
    assert "--overwrite" in cmd
    assert "--max-failures" in cmd
    assert cmd[cmd.index("--max-failures") + 1] == str(cross_buy.CROSS_BUY_MAX_FAILURES)
    assert cmd[cmd.index("--funds-file") + 1] == str(funds_file)


def test_download_uses_local_funds_list_when_present(monkeypatch, wired, tmp_path):
    local = tmp_path / "rupeevest_funds.txt"
    local.write_text("Local Fund(G)\n", encoding="utf-8")
    monkeypatch.setattr(cross_buy, "CROSS_BUY_FUNDS_LOCAL", local)

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _completed(0, DONE_CLEAN)

    monkeypatch.setattr(cross_buy.subprocess, "run", fake_run)
    cross_buy._download_cross_buy_csvs()

    assert captured["cmd"][captured["cmd"].index("--funds-file") + 1] == str(local)


def test_funds_file_falls_back_to_template(monkeypatch, tmp_path):
    missing = tmp_path / "nope.txt"
    template = tmp_path / "example.txt"
    template.write_text("Demo\n", encoding="utf-8")
    monkeypatch.setattr(cross_buy, "CROSS_BUY_FUNDS_LOCAL", missing)
    monkeypatch.setattr(cross_buy, "CROSS_BUY_FUNDS_FILE", template)

    assert cross_buy._funds_file() == template


# ---------------------------------------------------------------------------
# Result handling
# ---------------------------------------------------------------------------


def test_clean_run_returns_true(monkeypatch, wired):
    monkeypatch.setattr(
        cross_buy.subprocess, "run", lambda *a, **k: _completed(0, DONE_CLEAN)
    )
    assert cross_buy._download_cross_buy_csvs() is True


def test_partial_within_budget_is_true_and_warns(monkeypatch, wired, caplog):
    monkeypatch.setattr(
        cross_buy.subprocess,
        "run",
        lambda *a, **k: _completed(0, f"{FAIL_LINE}\n{DONE_PARTIAL}\n"),
    )

    with caplog.at_level(logging.WARNING, logger=cross_buy.logger.name):
        assert cross_buy._download_cross_buy_csvs() is True

    assert "ICICI Pru Flexicap Fund(G)" in caplog.text
    assert "1 failed fund(s)" in caplog.text


def test_rc_nonzero_returns_false_and_names_the_failure(monkeypatch, wired, caplog):
    """The old 500-char tail cut off exactly the FAIL line."""
    monkeypatch.setattr(
        cross_buy.subprocess,
        "run",
        lambda *a, **k: _completed(1, f"{FAIL_LINE}\n{DONE_PARTIAL}\n"),
    )

    with caplog.at_level(logging.WARNING, logger=cross_buy.logger.name):
        assert cross_buy._download_cross_buy_csvs() is False

    assert "Done: 61/62 saved, 1 failed" in caplog.text
    assert "ICICI Pru Flexicap Fund(G)" in caplog.text


def test_timeout_returns_false(monkeypatch, wired):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(cross_buy.subprocess, "run", boom)
    assert cross_buy._download_cross_buy_csvs() is False


def test_missing_script_returns_false_without_running(monkeypatch, wired, tmp_path):
    monkeypatch.setattr(cross_buy, "CROSS_BUY_DOWNLOAD_SCRIPT", tmp_path / "gone.py")
    called = MagicMock()
    monkeypatch.setattr(cross_buy.subprocess, "run", called)

    assert cross_buy._download_cross_buy_csvs() is False
    called.assert_not_called()


def test_missing_funds_file_returns_false(monkeypatch, wired, tmp_path):
    monkeypatch.setattr(cross_buy, "CROSS_BUY_FUNDS_FILE", tmp_path / "gone.txt")
    called = MagicMock()
    monkeypatch.setattr(cross_buy.subprocess, "run", called)

    assert cross_buy._download_cross_buy_csvs() is False
    called.assert_not_called()


# ---------------------------------------------------------------------------
# run(): download -> process -> mark
# ---------------------------------------------------------------------------


@pytest.fixture()
def task(monkeypatch):
    """Stub the tracker and the processor imports that run() does lazily."""
    import myra_app.task_tracker as tracker

    marks: list[dict] = []
    monkeypatch.setattr(tracker, "register", lambda *a, **k: 1)
    monkeypatch.setattr(tracker, "unregister", lambda *a, **k: None)
    monkeypatch.setattr(
        cross_buy,
        "_mark_task_run",
        lambda name, status="success", error_message=None: marks.append(
            {"name": name, "status": status, "error": error_message}
        ),
    )
    return marks


def test_run_returns_early_on_shutdown(monkeypatch, task):
    ctx = _ctx()
    ctx.shutdown_event.set()
    download = MagicMock()
    monkeypatch.setattr(cross_buy, "_download_cross_buy_csvs", download)

    cross_buy.run(ctx)

    download.assert_not_called()
    assert task == []


def test_run_raises_when_download_fails(monkeypatch, task):
    monkeypatch.setattr(cross_buy, "_download_cross_buy_csvs", lambda: False)
    process = MagicMock()
    monkeypatch.setattr(proc, "detect_available_months", lambda: ["2026-09"])
    monkeypatch.setattr(proc, "process_month", process)

    with pytest.raises(cross_buy.CrossBuyDownloadFailed, match="CSV download failed"):
        cross_buy.run(_ctx())

    process.assert_not_called()


def test_run_processes_latest_month_and_marks_success(monkeypatch, task):
    monkeypatch.setattr(cross_buy, "_download_cross_buy_csvs", lambda: True)
    monkeypatch.setattr(proc, "detect_available_months", lambda: ["2026-08", "2026-09"])
    calls: list[str] = []

    def fake_process(month):
        calls.append(month)
        return {"success": True, "month": month, "symbols": 10}

    monkeypatch.setattr(proc, "process_month", fake_process)

    cross_buy.run(_ctx())

    assert calls == ["2026-09"]
    assert task == [{"name": "cross_buy_sync", "status": "success", "error": None}]


def test_run_marks_failed_when_process_month_reports_failure(monkeypatch, task):
    monkeypatch.setattr(cross_buy, "_download_cross_buy_csvs", lambda: True)
    monkeypatch.setattr(proc, "detect_available_months", lambda: ["2026-09"])
    monkeypatch.setattr(
        proc,
        "process_month",
        lambda month: {"success": False, "reason": "no rows", "month": month},
    )

    cross_buy.run(_ctx())

    assert len(task) == 1
    assert task[0]["status"] == "failed"
    assert task[0]["error"] == "no rows"


def test_run_skips_processing_when_no_months(monkeypatch, task):
    monkeypatch.setattr(cross_buy, "_download_cross_buy_csvs", lambda: True)
    monkeypatch.setattr(proc, "detect_available_months", lambda: [])
    process = MagicMock()
    monkeypatch.setattr(proc, "process_month", process)

    cross_buy.run(_ctx())

    process.assert_not_called()
    assert task == []


# ---------------------------------------------------------------------------
# Processor fallback (the second dead path)
# ---------------------------------------------------------------------------


def test_processor_download_uses_fallback_funds_file(monkeypatch, tmp_path):
    real = tmp_path / "rupeevest_funds.txt"  # deliberately absent
    fallback = tmp_path / "rupeevest_funds.example.txt"
    fallback.write_text("Demo Fund-Reg(G)\n", encoding="utf-8")
    script = tmp_path / "download_rupeevest_funds.py"
    script.write_text("# stub\n", encoding="utf-8")

    monkeypatch.setattr(proc, "FUNDS_LIST_PATH", real)
    monkeypatch.setattr(proc, "FUNDS_LIST_FALLBACK_PATH", fallback)
    monkeypatch.setattr(proc, "DOWNLOAD_SCRIPT_PATH", script)
    monkeypatch.setattr(proc, "RAW_HOLDINGS_DIR", tmp_path / "out")

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _completed(0, DONE_CLEAN)

    monkeypatch.setattr(proc.subprocess, "run", fake_run)

    assert proc.download_holdings("2026-09") is True
    cmd = captured["cmd"]
    assert cmd[cmd.index("--funds-file") + 1] == str(fallback)
    assert cmd[cmd.index("--max-failures") + 1] == str(proc.DOWNLOAD_MAX_FAILURES)


def test_processor_download_prefers_real_funds_file(monkeypatch, tmp_path):
    real = tmp_path / "rupeevest_funds.txt"
    real.write_text("Real Fund(G)\n", encoding="utf-8")
    fallback = tmp_path / "rupeevest_funds.example.txt"
    fallback.write_text("Demo\n", encoding="utf-8")
    script = tmp_path / "download_rupeevest_funds.py"
    script.write_text("# stub\n", encoding="utf-8")

    monkeypatch.setattr(proc, "FUNDS_LIST_PATH", real)
    monkeypatch.setattr(proc, "FUNDS_LIST_FALLBACK_PATH", fallback)
    monkeypatch.setattr(proc, "DOWNLOAD_SCRIPT_PATH", script)
    monkeypatch.setattr(proc, "RAW_HOLDINGS_DIR", tmp_path / "out")

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _completed(0, DONE_CLEAN)

    monkeypatch.setattr(proc.subprocess, "run", fake_run)

    assert proc.download_holdings("2026-09") is True
    cmd = captured["cmd"]
    assert cmd[cmd.index("--funds-file") + 1] == str(real)


def test_processor_download_false_when_no_funds_file(monkeypatch, tmp_path):
    monkeypatch.setattr(proc, "FUNDS_LIST_PATH", tmp_path / "a.txt")
    monkeypatch.setattr(proc, "FUNDS_LIST_FALLBACK_PATH", tmp_path / "b.txt")
    monkeypatch.setattr(proc, "DOWNLOAD_SCRIPT_PATH", tmp_path / "s.py")
    called = MagicMock()
    monkeypatch.setattr(proc.subprocess, "run", called)

    assert proc.download_holdings("2026-09") is False
    called.assert_not_called()
