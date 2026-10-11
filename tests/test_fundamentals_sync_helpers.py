"""Unit tests for the resumable sync helpers (parking, backoff, pause)."""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from myra_app.utils import fundamentals_sync as fs


def test_partition_index_rows_keeps_real_tickers():
    equities, index_rows = fs.partition_index_rows(
        ["RELIANCE", "NIFTY 100", "BSE", "INDIA VIX", "HDFCBANK"]
    )
    assert index_rows == ["NIFTY 100", "INDIA VIX"]
    assert equities == ["RELIANCE", "BSE", "HDFCBANK"]


def test_record_failure_parks_at_threshold_and_expires():
    now = datetime.now(timezone.utc)
    state = {}
    for _ in range(fs.PARK_AFTER_FAILURES):
        entry = fs.record_failure(state, "FAILCO", now)
    assert state["FAILCO"]["fails"] == fs.PARK_AFTER_FAILURES
    assert fs.is_parked(state, "FAILCO", now)
    # Still parked a week later...
    assert fs.is_parked(state, "FAILCO", now + timedelta(days=7))
    # ...but wakes up after the park window.
    assert not fs.is_parked(state, "FAILCO", now + timedelta(days=fs.PARK_DAYS + 1))
    # Symbols with a sub-threshold streak are never parked.
    fs.record_failure({}, "NOPARK", now)
    assert not fs.is_parked(
        {"NOPARK": {"fails": 1, "last_attempt": now.isoformat()}}, "NOPARK", now
    )


def test_park_state_save_load_roundtrip():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    now = datetime.now(timezone.utc)
    fs.save_park_state(
        conn,
        {"BADCO": {"fails": 5, "parked_until": (now + timedelta(days=30)).isoformat()}},
    )
    loaded = fs.load_park_state(conn)
    assert loaded["BADCO"]["fails"] == 5
    assert fs.is_parked(loaded, "BADCO", now)
    # Corrupt payload degrades to empty, never raises.
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('fundamentals_park_state', 'not json')"
    )
    assert fs.load_park_state(conn) == {}


def test_backoff_sleep_wakes_early_on_cancel():
    ev = threading.Event()
    ev.set()
    t0 = time.monotonic()
    fs._backoff_sleep(60.0, ev)
    assert time.monotonic() - t0 < 1.0


def test_wait_if_paused_returns_promptly_when_unpaused():
    ev = threading.Event()
    ev.set()
    assert fs.wait_if_paused(ev, None) is True
    assert fs.wait_if_paused(None, None) is True


def test_wait_if_paused_blocks_until_resumed():
    pause = threading.Event()
    cancel = threading.Event()
    result = {}

    def waiter():
        result["value"] = fs.wait_if_paused(pause, cancel)

    t = threading.Thread(target=waiter)
    t0 = time.monotonic()
    t.start()
    time.sleep(0.15)
    pause.set()
    t.join(timeout=2)
    assert t.is_alive() is False
    assert result["value"] is True
    assert time.monotonic() - t0 >= 0.1


def test_wait_if_paused_cancel_while_waiting():
    pause = threading.Event()  # stays cleared
    cancel = threading.Event()
    result = {}

    def waiter():
        result["value"] = fs.wait_if_paused(pause, cancel)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.1)
    cancel.set()
    t.join(timeout=2)
    assert result["value"] is False


def test_load_active_symbols_cutoff(tmp_path):
    from myra_app.librarian_core import LibrarianCore

    path = tmp_path / LibrarianCore.DB_MAP["technical"]
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT)")
    dates = [f"2026-10-{d:02d}" for d in (9, 8, 7, 6, 5, 4, 3, 2)]
    for d in dates:
        conn.execute("INSERT INTO technical_data VALUES ('AAA', ?)", (d,))
    # BBB printed a price only before the 7-session window.
    conn.execute("INSERT INTO technical_data VALUES ('BBB', '2026-10-02')")
    conn.commit()
    conn.close()

    active = fs.load_active_symbols(db_dir=str(tmp_path))
    assert "AAA" in active
    assert "BBB" not in active


def test_load_active_symbols_missing_db_is_empty_not_error():
    assert fs.load_active_symbols(db_dir="C:\\definitely\\missing") == set()
