import unittest
from myra_app.librarian import Librarian
from unittest.mock import MagicMock


class TestIsStale(unittest.TestCase):
    def test_is_stale_dcal(self):
        console = MagicMock()
        lib = Librarian(console=console)
        # Ensure it runs without exception and returns True since DCAL is not in db
        stale = lib.fundamental_manager.is_stale("DCAL")
        self.assertTrue(stale)


if __name__ == "__main__":
    unittest.main()
