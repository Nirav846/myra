import sys
import unittest
from unittest.mock import MagicMock
import pandas as pd
from io import StringIO
from datetime import datetime

# Mock dependencies that might be missing
mock_scrapling = MagicMock()
sys.modules['scrapling'] = mock_scrapling
mock_rich = MagicMock()
sys.modules['rich'] = mock_rich
sys.modules['rich.console'] = mock_rich
sys.modules['rich.panel'] = mock_rich
sys.modules['rich.table'] = mock_rich
sys.modules['rich.columns'] = mock_rich

# Import the class to test
from myra_app.fetcher import DataFetcher

class TestMTOParsing(unittest.TestCase):
    def setUp(self):
        # Patch LibrarianCore since it's likely used in __init__
        with unittest.mock.patch('myra_app.librarian_core.LibrarianCore') as mock_lib:
            mock_lib.DB_MAP = {"network_cache": "network_cache.sqlite"}
            self.fetcher = DataFetcher()

    def test_merge_zip_mto_ragged(self):
        # 1. Mock ZIP content (Bhavcopy)
        # TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol
        bhav_csv = "TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\nRELIANCE,EQ,2500,2550,2490,2530,100000\nTCS,EQ,3500,3550,3490,3530,50000"
        import zipfile
        import io
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('bhav.csv', bhav_csv)
        zip_content = zip_buffer.getvalue()

        # 2. Mock Ragged MTO text
        # Format: RecType, SrNo, Symbol, Series, TotalQty, DelivQty, Deliv%
        mto_text = """10,MTO,11032024
20,RECORD TYPE,SR NO,SYMBOL,SERIES,TOTAL TRADED QUANTITY,DELIVERABLE QUANTITY,DELIVERY PERCENTAGE
50,1,RELIANCE,EQ,1000,500,50.0
50,2,TCS,EQ,2000,1000,50.0
90,2,3000""" # Ragged footer

        current_date = datetime(2024, 3, 11)

        # 3. Call the method
        result_csv = self.fetcher._merge_zip_mto(zip_content, mto_text, current_date)

        # 4. Verify
        self.assertIsNotNone(result_csv)
        df_result = pd.read_csv(io.StringIO(result_csv))

        self.assertEqual(len(df_result), 2)
        self.assertIn("DELIV_QTY", df_result.columns)
        self.assertIn("DELIV_PER", df_result.columns)

        reliance = df_result[df_result['SYMBOL'] == 'RELIANCE'].iloc[0]
        self.assertEqual(reliance['DELIV_QTY'], 500)
        self.assertEqual(reliance['DELIV_PER'], 50.0)

        tcs = df_result[df_result['SYMBOL'] == 'TCS'].iloc[0]
        self.assertEqual(tcs['DELIV_QTY'], 1000)
        self.assertEqual(tcs['DELIV_PER'], 50.0)

        print("✅ Ragged MTO Parsing Verified Successfully!")

if __name__ == "__main__":
    # We can't run this directly if pandas is missing, but we can verify it via py_compile or if we find an env with pandas
    try:
        import pandas
        unittest.main(argv=['first-arg-is-ignored'], exit=False)
    except ImportError:
        print("Skipping execution: pandas not found.")
