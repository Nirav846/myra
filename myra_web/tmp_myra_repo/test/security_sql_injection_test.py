import unittest
from unittest.mock import MagicMock, patch
import sqlite3
import os
import sys

# Mocking pandas and rich before they are imported by UI_Manager
mock_pd = MagicMock()
sys.modules["pandas"] = mock_pd
sys.modules["rich"] = MagicMock()
sys.modules["rich.console"] = MagicMock()
sys.modules["rich.table"] = MagicMock()
sys.modules["rich.panel"] = MagicMock()
sys.modules["rich.layout"] = MagicMock()
sys.modules["rich.text"] = MagicMock()

# Mocking LibrarianCore before importing UI_Manager
mock_librarian_core_mod = MagicMock()
mock_librarian_core_class = MagicMock()
mock_librarian_core_class.DB_MAP = {"meta": "meta.db"}
mock_librarian_core_mod.LibrarianCore = mock_librarian_core_class
sys.modules["myra_app.librarian_core"] = mock_librarian_core_mod

# Mock other potential dependencies
sys.modules["myra_app.data_adapter"] = MagicMock()
sys.modules["myra_app.strategies.ias_timing_engine"] = MagicMock()

class TestSQLInjectionSecurity(unittest.TestCase):
    def test_parameterized_attach(self):
        """Verifies that ATTACH DATABASE works with parameters in sqlite3."""
        conn = sqlite3.connect(":memory:")
        db_path = "dummy_test.db"
        try:
            # This should not raise a syntax error
            conn.execute("ATTACH DATABASE ? AS test_db", (db_path,))
        except sqlite3.OperationalError as e:
            # It might fail because the file doesn't exist, but it shouldn't be a syntax error
            self.assertIn("unable to open database file", str(e).lower())
        except Exception as e:
            self.fail(f"ATTACH DATABASE with parameter raised unexpected exception: {e}")
        finally:
            conn.close()

    @patch("sqlite3.connect")
    def test_ui_manager_ias_leaderboard_uses_parameterized_attach(self, mock_connect):
        """Verifies get_ias_leaderboard calls ATTACH with parameters."""
        from myra_app.UI_Manager import MYRA_UI

        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        librarian = MagicMock()
        librarian._gov_conn = mock_conn

        # mock_pd.read_sql needs to return something that doesn't crash the rest of the function
        mock_pd.read_sql.return_value = MagicMock(empty=True)
        MYRA_UI.get_ias_leaderboard(librarian)

        # Check if execute was called with ATTACH DATABASE ?
        # It might be called multiple times, we look for ATTACH
        attach_calls = [call for call in mock_conn.execute.call_args_list if "ATTACH DATABASE" in str(call)]
        self.assertTrue(len(attach_calls) >= 1, "ATTACH DATABASE was not called")

        # Verify first argument is the SQL with ? and second is the tuple with path
        first_attach_call = attach_calls[0]
        sql = first_attach_call[0][0]
        params = first_attach_call[0][1]

        self.assertIn("?", sql)
        self.assertIn("meta.db", params[0])

    @patch("sqlite3.connect")
    def test_ui_manager_timing_triggers_uses_parameterized_attach(self, mock_connect):
        """Verifies get_timing_triggers_panel calls ATTACH with parameters."""
        from myra_app.UI_Manager import MYRA_UI

        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        librarian = MagicMock()
        librarian._gov_conn = mock_conn

        mock_pd.read_sql.return_value = MagicMock(empty=True)
        MYRA_UI.get_timing_triggers_panel(librarian)

        attach_calls = [call for call in mock_conn.execute.call_args_list if "ATTACH DATABASE" in str(call)]
        self.assertTrue(len(attach_calls) >= 1, "ATTACH DATABASE was not called")

        first_attach_call = attach_calls[0]
        sql = first_attach_call[0][0]
        params = first_attach_call[0][1]

        self.assertIn("?", sql)
        self.assertIn("meta.db", params[0])

if __name__ == "__main__":
    unittest.main()
