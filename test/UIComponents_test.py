import unittest
from rich.panel import Panel
from myra_app.ui_components import get_logo_panel, get_status_footer

class TestUIComponents(unittest.TestCase):
    def test_get_logo_panel(self):
        from rich.box import DOUBLE
        panel = get_logo_panel()
        self.assertIsInstance(panel, Panel)
        self.assertEqual(panel.border_style, "magenta")
        self.assertEqual(panel.box, DOUBLE)

    def test_get_status_footer(self):
        # We'll need to mock Librarian for this
        class MockLib:
            def get_db_stats(self):
                return {"status": "Connected", "size": "10MB"}
        
        footer = get_status_footer(MockLib())
        self.assertIsInstance(footer, Panel)
        self.assertIn("Connected", str(footer.renderable))
        self.assertIn("10MB", str(footer.renderable))

if __name__ == "__main__":
    unittest.main()
