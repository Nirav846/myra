import unittest
import sqlite3
import os
from datetime import date
from myra_app.fundamental_manager import FundamentalManager


class TestGrahamData(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_graham.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.conn = sqlite3.connect(self.db_path)

        # Create table with new schema
        self.conn.execute(
            """
            CREATE TABLE quarterly_results (
                symbol TEXT, report_date TEXT, revenue REAL, net_profit REAL,
                eps REAL, roce REAL, roe REAL, debt REAL,
                opm_pct REAL, interest REAL, borrowings REAL, cash_from_ops REAL,
                debtor_days REAL, inventory_days REAL, cwip REAL,
                promoter_holding REAL, pledged_pct REAL,
                industry_pe REAL, stock_pe REAL, book_value REAL,
                sales_per_share REAL, dividend_yield REAL,
                source TEXT, last_updated TEXT,
                PRIMARY KEY (symbol, report_date)
            )
        """
        )
        self.fm = FundamentalManager()
        self.fm.set_connection(self.conn)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_graham_calculation(self):
        symbol = "TEST"
        # Mock quarterly data: EPS=10, BVPS=100
        # Graham = (22.5 * 10 * 100) ** 0.5 = (22500) ** 0.5 = 150
        self.conn.execute(
            f"""
            INSERT INTO quarterly_results (symbol, report_date, eps, book_value, last_updated)
            VALUES ('{symbol}', 'Dec 2025', 10.0, 100.0, '{date.today()}')
        """
        )

        metrics = self.fm.get_valuation_metrics(symbol)
        self.assertIn("graham_number", metrics)
        self.assertEqual(metrics["graham_number"], 150.0)


if __name__ == "__main__":
    unittest.main()
