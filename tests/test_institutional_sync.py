"""Tests for institutional_sync.py — metadata tracking, idempotency, failure isolation."""
import sqlite3
import sys
import tempfile
import os
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
import pytest


@contextmanager
def mocked_capital_market():
    """Stand-in for nselib.capital_market.

    nselib binds `capital_market` as a submodule only after first import, and it
    is an optional runtime dep (not in requirements.txt), so plain attribute
    patching is unreliable. Injecting a fake 'nselib' into sys.modules makes the
    local `from nselib import capital_market` resolve to our mock everywhere.
    """
    fake_nselib = MagicMock()
    with patch.dict(sys.modules, {"nselib": fake_nselib}):
        yield fake_nselib.capital_market


@pytest.fixture
def sync_instance(tmp_path):
    """Create an InstitutionalSync instance with a temp DB."""
    from myra_app.utils.institutional_sync import InstitutionalSync
    db_path = str(tmp_path / "test_institutional.db")
    s = InstitutionalSync.__new__(InstitutionalSync)
    s.db_path = db_path
    s._ensure_tables()
    return s


def test_sync_metadata_table_exists(sync_instance):
    """sync_metadata table should be created by _ensure_tables."""
    conn = sqlite3.connect(sync_instance.db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    assert "sync_metadata" in tables


def test_get_set_last_sync_date_roundtrip(sync_instance):
    """Setting and getting a sync date should roundtrip correctly."""
    assert sync_instance._get_last_sync_date("bulk_deals") is None
    sync_instance._set_last_sync_date("bulk_deals", "2026-08-21")
    assert sync_instance._get_last_sync_date("bulk_deals") == "2026-08-21"


def test_set_last_sync_date_overwrite(sync_instance):
    """Setting the same key twice should overwrite."""
    sync_instance._set_last_sync_date("bulk_deals", "2026-01-01")
    sync_instance._set_last_sync_date("bulk_deals", "2026-08-21")
    assert sync_instance._get_last_sync_date("bulk_deals") == "2026-08-21"


def test_bulk_deals_failure_does_not_update_metadata(sync_instance):
    """If nselib fails, sync date should NOT be updated."""
    with mocked_capital_market() as mock_cm:
        mock_cm.bulk_deal_data.side_effect = ConnectionError("NSE firewall")
        result = sync_instance.sync_bulk_deals()

    assert result == 0
    assert sync_instance._get_last_sync_date("bulk_deals") is None


def test_block_deals_failure_does_not_update_metadata(sync_instance):
    """If nselib fails for block deals, sync date should NOT be updated."""
    with mocked_capital_market() as mock_cm:
        mock_cm.block_deals_data.side_effect = ConnectionError("NSE firewall")
        result = sync_instance.sync_block_deals()

    assert result == 0
    assert sync_instance._get_last_sync_date("block_deals") is None


def test_bulk_deals_empty_result_updates_metadata(sync_instance):
    """If nselib returns empty DataFrame, sync date should still be updated."""
    import pandas as pd
    with mocked_capital_market() as mock_cm:
        mock_cm.bulk_deal_data.return_value = pd.DataFrame()
        result = sync_instance.sync_bulk_deals()

    assert result == 0
    assert sync_instance._get_last_sync_date("bulk_deals") is not None


def test_bulk_deals_idempotent(sync_instance):
    """Running sync twice should not create duplicates."""
    import pandas as pd
    fake_df = pd.DataFrame({
        "Symbol": ["TESTCORP"],
        "Date": ["21-Aug-2026"],
        "SecurityName": ["Test Corp Ltd"],
        "ClientName": ["Test Client"],
        "Buy/Sell": ["Buy"],
        "QuantityTraded": [1000],
        "TradePrice/Wght.Avg.Price": [100.50],
    })
    with mocked_capital_market() as mock_cm:
        mock_cm.bulk_deal_data.return_value = fake_df
        count1 = sync_instance.sync_bulk_deals()
        count2 = sync_instance.sync_bulk_deals()

    # Both should report inserts, but DB should have only 1 row
    conn = sqlite3.connect(sync_instance.db_path)
    rows = conn.execute("SELECT COUNT(*) FROM bulk_deals").fetchone()[0]
    conn.close()
    assert rows == 1  # no duplicates
