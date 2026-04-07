import unittest
import pandas as pd
import numpy as np
import os
from datetime import date, timedelta
from myra_app.librarian import Librarian


class TestAbsorptionLogic(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_absorption.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.lib = Librarian()
        self.lib.db_path = self.db_path
        self.lib.connect()

    def tearDown(self):
        self.lib.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_absorption_indicators(self):
        # 1. Insert 60 days of data
        symbol = "ABSORB"
        base_date = date.today() - timedelta(days=60)

        for i in range(61):
            curr_date = base_date + timedelta(days=i)
            # Standard day: High=105, Low=95, Close=100 (Range=10)
            high, low, close = 105, 95, 100

            # Day 60: Tightening day (Absorption candidate)
            # High=101, Low=99, Close=100.8 (Range=2, Closing at 90% of range)
            if i == 60:
                high, low, close = 101, 99, 100.8

            self.lib.conn.execute(
                f"""
                INSERT INTO prices (symbol, date, open, high, low, close, volume, delivery_qty, delivery_percent, exchange)
                VALUES ('{symbol}', '{curr_date}', 100, {high}, {low}, {close}, 100000, 50000, 50.0, 'NSE')
            """
            )

        # 2. Update indicators
        self.lib.update_indicator_history()

        # 3. Verify latest indicators
        res = self.lib.conn.execute(
            f"""
            SELECT rel_spread, closing_pos 
            FROM calculated_indicators 
            WHERE symbol='{symbol}' 
            ORDER BY date DESC LIMIT 1
        """
        ).fetchone()

        self.assertIsNotNone(res)
        rel_spread, closing_pos = res

        # Range on day 60 is 2. Avg range for prev 50 days was 10.
        # rel_spread should be ~ 2/10 = 0.2
        self.assertAlmostEqual(rel_spread, 0.2, places=2)

        # closing_pos = (100.8 - 99) / (101 - 99) = 1.8 / 2 = 0.9
        self.assertAlmostEqual(closing_pos, 0.9, places=2)


if __name__ == "__main__":
    unittest.main()
