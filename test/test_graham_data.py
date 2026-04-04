import unittest
import duckdb
import os
from datetime import date
from myra_app.fundamental_manager import FundamentalManager

class TestGrahamData(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_graham.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.conn = duckdb.connect(self.db_path)
        
        # Create table with new schema
        self.conn.execute("""
            CREATE TABLE quarterly_results (
                symbol VARCHAR, report_date VARCHAR, revenue DOUBLE, net_profit DOUBLE, 
                eps DOUBLE, roce DOUBLE, roe DOUBLE, debt DOUBLE,
                opm_pct DOUBLE, interest DOUBLE, borrowings DOUBLE, cash_from_ops DOUBLE,
                debtor_days DOUBLE, inventory_days DOUBLE, cwip DOUBLE,
                promoter_holding DOUBLE, pledged_pct DOUBLE,
                industry_pe DOUBLE, stock_pe DOUBLE, book_value DOUBLE,
                sales_per_share DOUBLE, dividend_yield DOUBLE,
                source VARCHAR, last_updated DATE,
                PRIMARY KEY (symbol, report_date)
            )
        """)
        self.fm = FundamentalManager(db_conn=self.conn)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_graham_calculation(self):
        symbol = "TEST"
        # Mock quarterly data: EPS=10, BVPS=100
        # Graham = (22.5 * 10 * 100) ** 0.5 = (22500) ** 0.5 = 150
        self.conn.execute(f"""
            INSERT INTO quarterly_results (symbol, report_date, eps, book_value, last_updated)
            VALUES ('{symbol}', 'Dec 2025', 10.0, 100.0, '{date.today()}')
        """)
        
        metrics = self.fm.get_valuation_metrics(symbol)
        self.assertIn("graham_number", metrics)
        self.assertEqual(metrics["graham_number"], 150.0)

if __name__ == "__main__":
    unittest.main()
