import sys
import unittest
from unittest.mock import MagicMock
import pandas as pd
from io import StringIO
from datetime import datetime

# Mock dependencies
mock_scrapling = MagicMock()
sys.modules['scrapling'] = mock_scrapling
mock_rich = MagicMock()
sys.modules['rich'] = mock_rich
sys.modules['rich.console'] = mock_rich
sys.modules['rich.panel'] = mock_rich
sys.modules['rich.table'] = mock_rich
sys.modules['rich.columns'] = mock_rich

from myra_app.fetcher import DataFetcher

class TestMTOParsing(unittest.TestCase):
    def setUp(self):
        with unittest.mock.patch('myra_app.librarian_core.LibrarianCore') as mock_lib:
            mock_lib.DB_MAP = {"network_cache": "network_cache.sqlite"}
            self.fetcher = DataFetcher()

    def test_merge_zip_mto_ragged_header_fix(self):
        # 1. Mock ZIP content (Bhavcopy)
        bhav_csv = "TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\nRELIANCE,EQ,2500,2550,2490,2530,100000"
        import zipfile
        import io
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('bhav.csv', bhav_csv)
        zip_content = zip_buffer.getvalue()

        # 2. Mock Ragged MTO text with SHORT FIRST LINE
        # Rec 10 has 3 cols, Rec 50 has 7 cols.
        mto_text = "10,MTO,11032024\n50,1,RELIANCE,EQ,1000,500,50.0\n90,2,3000"

        current_date = datetime(2024, 3, 11)

        # 3. Call the method
        result_csv = self.fetcher._merge_zip_mto(zip_content, mto_text, current_date)

        # 4. Verify
        self.assertIsNotNone(result_csv)
        df_result = pd.read_csv(io.StringIO(result_csv))

        self.assertEqual(len(df_result), 1)
        reliance = df_result[df_result['SYMBOL'] == 'RELIANCE'].iloc[0]
        self.assertEqual(reliance['DELIV_QTY'], 500)
        self.assertEqual(reliance['DELIV_PER'], 50.0)

        print("✅ Ragged MTO Parsing with Short Header Verified Successfully!")

if __name__ == "__main__":
    try:
        import pandas
        unittest.main(argv=['first-arg-is-ignored'], exit=False)
    except ImportError:
        print("Skipping execution: pandas not found.")
