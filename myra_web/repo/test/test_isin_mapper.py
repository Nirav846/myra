import unittest
from unittest.mock import MagicMock, patch
import sys
import os

class TestISINMapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create mocks for missing dependencies
        cls.mock_pd = MagicMock()
        cls.mock_requests = MagicMock()
        cls.mock_urllib3 = MagicMock()

        # Use patch.dict to safely mock sys.modules without permanent pollution
        cls.modules_patcher = patch.dict('sys.modules', {
            'pandas': cls.mock_pd,
            'requests': cls.mock_requests,
            'requests.adapters': MagicMock(),
            'urllib3': cls.mock_urllib3,
            'urllib3.util.retry': MagicMock()
        })
        cls.modules_patcher.start()

        # Import the module under test AFTER mocking sys.modules
        # We use a local import to ensure it uses the mocks
        global update_isin_bridge
        from myra_app.isin_mapper import update_isin_bridge

    @classmethod
    def tearDownClass(cls):
        # Stop the patcher and clean up sys.modules to prevent leakage
        cls.modules_patcher.stop()
        if 'myra_app.isin_mapper' in sys.modules:
            del sys.modules['myra_app.isin_mapper']

    def setUp(self):
        # Reset mocks and their side effects before each test
        self.mock_pd.reset_mock()
        self.mock_pd.read_csv.side_effect = None

        self.mock_requests.reset_mock()
        self.mock_session = self.mock_requests.Session.return_value
        self.mock_session.get.side_effect = None
        self.mock_session.get.return_value = MagicMock()

        self.mock_urllib3.reset_mock()

    @patch('os.makedirs')
    @patch('os.path.exists')
    @patch('os.getcwd')
    def test_update_isin_bridge_success(self, mock_getcwd, mock_exists, mock_makedirs):
        # Setup mocks
        mock_getcwd.return_value = '/fake/dir'
        mock_exists.return_value = False

        # Mock response
        mock_response = self.mock_session.get.return_value
        mock_response.text = "SYMBOL,ISIN NUMBER\nRELIANCE,INE002A01018"
        mock_response.raise_for_status = MagicMock()

        # Mock pandas read_csv and DataFrame
        mock_df = MagicMock()
        mock_df.columns = pd_index = MagicMock()
        # Mock the behavior of df.columns.str.strip()
        pd_index.str.strip.return_value = ['SYMBOL', 'ISIN NUMBER']

        # Setup the chain: df[['SYMBOL', 'ISIN NUMBER']].rename(columns=...)
        mock_df.__getitem__.return_value = mock_df
        mock_df.rename.return_value = mock_df
        self.mock_pd.read_csv.return_value = mock_df

        # Execute
        result = update_isin_bridge()

        # Assertions
        self.assertTrue(result)
        self.mock_requests.Session.assert_called_once()
        self.mock_session.get.assert_called_once()
        self.mock_pd.read_csv.assert_called_once()
        mock_df.to_parquet.assert_called_once()

        # Verify directory creation
        mock_makedirs.assert_called_once_with('/fake/dir/data')

        # Verify parquet path
        expected_path = os.path.join('/fake/dir', 'data', 'isin_bridge.parquet')
        mock_df.to_parquet.assert_called_with(expected_path, index=False)

    def test_update_isin_bridge_request_failure(self):
        # Mock requests session to raise an exception
        self.mock_session.get.side_effect = Exception("Network error")

        # Execute and check for exception
        with self.assertRaises(Exception) as context:
            update_isin_bridge()

        self.assertEqual(str(context.exception), "Network error")
        self.mock_pd.read_csv.assert_not_called()

if __name__ == '__main__':
    unittest.main()
