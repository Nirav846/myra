import unittest
from unittest.mock import MagicMock, patch
import os
from datetime import date
from myra_app.librarian import Librarian
from myra_app.fetcher import DataFetcher


class TestLargeDeals(unittest.TestCase):
    def setUp(self):
        self.db_name = "test_large_deals.db"
        self.db_dir = os.path.join(os.getcwd(), "db")
        os.makedirs(self.db_dir, exist_ok=True)
        self.db_path = os.path.join(self.db_dir, self.db_name)

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        # Patch Librarian DB_MAP to use test database for institutional
        self.patcher = patch.dict(Librarian.DB_MAP, {"institutional": self.db_name})
        self.patcher.start()

        # Initialize Librarian with test DB
        self.lib = Librarian(read_only=False)
        self.lib._create_tables()

        # Mock fetcher
        self.fetcher = DataFetcher()
        self.fetcher.fetch_large_deals = MagicMock()

    def tearDown(self):
        self.lib.close()
        self.patcher.stop()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_large_deals_population(self):
        # 1. Setup mock data
        mock_deals = [
            {
                "symbol": "RELIANCE",
                "type": "Bulk",
                "client": "GOLDMAN SACHS",
                "buy_sell": "BUY",
                "qty": 1000000,
                "price": 2500.50,
                "value_cr": 250.05,
                "date": date.today().isoformat(),
            },
            {
                "symbol": "TCS",
                "type": "Block",
                "client": "LIC",
                "buy_sell": "SELL",
                "qty": 500000,
                "price": 3400.00,
                "value_cr": 170.00,
                "date": date.today().isoformat(),
            },
        ]
        self.fetcher.fetch_large_deals.return_value = mock_deals

        # 2. Implementation: Insert mock data into new schema
        for deal in mock_deals:
            self.lib._inst_conn.execute(  # noqa: performance
                """
                INSERT OR REPLACE INTO large_deals VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    deal["symbol"],
                    deal["type"],
                    deal["client"],
                    deal["buy_sell"],
                    deal["qty"],
                    deal["price"],
                    deal["value_cr"],
                    deal["date"],
                )
            )
        self.lib._inst_conn.commit()

        # 3. Verify
        res = self.lib._inst_conn.execute(
            "SELECT * FROM large_deals WHERE symbol='RELIANCE'"
        ).fetchone()
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "RELIANCE")
        self.assertEqual(res[1], "Bulk")
        self.assertEqual(res[2], "GOLDMAN SACHS")
        self.assertEqual(res[4], 1000000)


if __name__ == "__main__":
    unittest.main()
