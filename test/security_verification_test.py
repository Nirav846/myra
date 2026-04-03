import unittest
from unittest.mock import MagicMock, patch
import sys

# Mock rich before importing MenuNavigator
mock_rich = MagicMock()
sys.modules["rich"] = mock_rich
sys.modules["rich.console"] = mock_rich.console
sys.modules["rich.panel"] = mock_rich.panel

from myra_app.menu_navigation import MenuNavigator

class TestSecurityFix(unittest.TestCase):
    def test_clear_screen_uses_console_clear(self):
        # Mock the console object
        mock_console = MagicMock()
        navigator = MenuNavigator(mock_console)

        # Call clear_screen
        navigator.clear_screen()

        # Verify console.clear() was called instead of os.system()
        mock_console.clear.assert_called_once()

if __name__ == "__main__":
    unittest.main()
