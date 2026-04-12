import unittest
from unittest.mock import MagicMock, patch
import sys

class TestSecurityFix(unittest.TestCase):
    def test_clear_screen_uses_console_clear(self):
        # We need to temporarily mock rich and its submodules
        # We use patch.dict on sys.modules to ensure safe cleanup
        mock_rich = MagicMock()
        mock_console = MagicMock()
        mock_panel = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "rich": mock_rich,
                "rich.console": mock_console,
                "rich.panel": mock_panel
            }
        ):
            # Import inside the context manager
            from myra_app.menu_navigation import MenuNavigator

            # Mock the console object passed to navigator
            mock_navigator_console = MagicMock()
            navigator = MenuNavigator(mock_navigator_console)

            # Call clear_screen
            navigator.clear_screen()

            # Verify console.clear() was called instead of os.system()
            mock_navigator_console.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
