import unittest
import datetime
import pandas as pd
from unittest.mock import patch, MagicMock

from myra_core.utils.date_utils import to_date, ensure_date, parse_dataframe_dates
from myra_core.utils.trading_calendar import (
    is_trading_day,
    get_previous_trading_day,
    get_expected_trading_day,
)
from myra_core.data.data_freshness import check_data_freshness


class TestDateHandling(unittest.TestCase):
    def test_to_date_type_safety(self):
        """Phase 6: Type safety (Mix of str/date/datetime inputs -> no crash)"""
        # Test string parsing
        self.assertEqual(to_date("2026-04-02"), datetime.date(2026, 4, 2))
        self.assertEqual(to_date("2026-04-02 15:30:00"), datetime.date(2026, 4, 2))
        self.assertEqual(to_date("02-Apr-2026"), datetime.date(2026, 4, 2))

        # Test datetime object
        dt = datetime.datetime(2026, 4, 2, 15, 30)
        self.assertEqual(to_date(dt), datetime.date(2026, 4, 2))

        # Test date object
        d = datetime.date(2026, 4, 2)
        self.assertEqual(to_date(d), datetime.date(2026, 4, 2))

        # Test pandas Timestamp
        ts = pd.Timestamp("2026-04-02")
        self.assertEqual(to_date(ts), datetime.date(2026, 4, 2))

        # Test invalid inputs
        with self.assertRaises(TypeError):
            to_date(123)

    def test_ensure_date(self):
        """Test ensure_date converts various inputs to datetime.date."""
        expected = datetime.date(2026, 4, 2)

        # Test valid date object
        self.assertEqual(ensure_date(expected), expected)

        # Test valid string
        self.assertEqual(ensure_date("2026-04-02"), expected)

        # Test valid datetime object
        self.assertEqual(ensure_date(datetime.datetime(2026, 4, 2, 12, 0)), expected)

        # Test valid pandas Timestamp (mocked)
        ts = pd.Timestamp("2026-04-02")
        self.assertEqual(ensure_date(ts), expected)

        # Test edge cases and error conditions
        with self.assertRaises(ValueError):
            ensure_date(None)

        with self.assertRaises(ValueError):
            ensure_date("not-a-date")

        with self.assertRaises(ValueError):
            ensure_date(123)

        with self.assertRaises(ValueError):
            ensure_date([])

    def test_parse_dataframe_dates(self):
        df = pd.DataFrame(
            {
                "date_col1": ["2026-04-02", "2026-04-03"],
                "date_col2": [
                    datetime.datetime(2026, 4, 2),
                    datetime.datetime(2026, 4, 3),
                ],
            }
        )
        df_parsed = parse_dataframe_dates(df, ["date_col1", "date_col2"])

        self.assertIsInstance(df_parsed["date_col1"].iloc[0], datetime.date)
        self.assertIsInstance(df_parsed["date_col2"].iloc[0], datetime.date)

    @patch("myra_core.utils.trading_calendar.get_market_holidays")
    def test_weekend_case(self, mock_get_holidays):
        """Phase 6: Weekend case. Input: Sunday. Expected trading day: Friday"""
        mock_get_holidays.return_value = set()  # No holidays

        # Sunday is 2026-04-05
        sunday = datetime.date(2026, 4, 5)
        self.assertFalse(is_trading_day(sunday))

        # Should return Friday (2026-04-03)
        expected = datetime.date(2026, 4, 3)
        self.assertEqual(get_previous_trading_day(sunday), expected)

    @patch("myra_core.utils.trading_calendar.get_market_holidays")
    def test_holiday_case(self, mock_get_holidays):
        """Phase 6: Holiday case. Input: Known NSE holiday. Expected: previous trading day"""
        # Let's say Friday is a holiday
        friday_holiday = "2026-04-03"
        mock_get_holidays.return_value = {friday_holiday}

        # Friday is a holiday
        friday = datetime.date(2026, 4, 3)
        self.assertFalse(is_trading_day(friday))

        # Expected is Thursday
        expected = datetime.date(2026, 4, 2)
        self.assertEqual(get_previous_trading_day(friday), expected)

        # What if it's Sunday and Friday is a holiday? Should skip Sunday, Saturday, Friday, and return Thursday.
        sunday = datetime.date(2026, 4, 5)
        self.assertEqual(get_previous_trading_day(sunday), expected)

    def test_insider_lag_case(self):
        """Phase 6: Insider lag case. Expected date: 2026-04-02, Insider date: 2026-03-31, Result: VALID"""
        # lag is 2 days (April 2nd vs March 31st) -> assuming not a leap year logic, well, it's straightforward.
        expected_date = "2026-04-02"
        insider_date = "2026-03-31"

        result = check_data_freshness(insider_date, expected_date, "insider")

        # Allowed lag for insider is 5. Actual is 2.
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["lag_days"], 2)
        self.assertEqual(result["allowed_lag"], 5)

    def test_bhavcopy_lag_case(self):
        # Bhavcopy allowed lag is 1
        expected_date = "2026-04-02"
        bhav_date = "2026-03-31"

        result = check_data_freshness(bhav_date, expected_date, "bhavcopy")

        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["lag_days"], 2)
        self.assertEqual(result["allowed_lag"], 1)


if __name__ == "__main__":
    unittest.main()
