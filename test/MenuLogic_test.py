import unittest
from myra_app.ui_components import get_categorized_menu


class TestMenuLogic(unittest.TestCase):
    def test_menu_categorization(self):
        # Existing strategies from myra.py
        strategies = {
            "1": ("technicals", "Classical Technicals", None),
            "2": ("delivery_spikes", "Delivery Spikes", "Spike"),
            "3": ("rs_rating", "RS Rating", "RS_Raw"),
            "4": ("bb_squeeze", "BB Squeeze", "BB_Width"),
            "5": ("momentum", "MACD Momentum", "MACD"),
            "6": ("breakouts", "Breakouts", None),
            "7": ("candlesticks", "Reversal Patterns", None),
            "8": ("value", "Value", "ROE"),
            "9": ("vsa_momentum", "Volume Spread Analysis (VSA)", "Rel_Vol"),
            "12": ("super_setup", "Super-Scan (Growth+Mom)", "RS_Raw"),
            "13": ("123", "Graham Deep Value (Intrinsic)", "RS_Raw"),
            "14": ("ml_signals", "ML-Based Signals", "ML_ProbUp"),
            "15": ("whale_tracker", "Elite Whale Tracker (ML)", "Whale_Conf"),
            "16": ("large_deal_momentum", "Institutional Deals", "Inst_Intensity"),
            "23": ("smart_money", "Smart Money Accumulation", "Deliv_Grow"),
            "24": (
                "crash_resilience",
                "Crash Resilience (Underwater Ball)",
                "Absorp_Ratio",
            ),
            "25": ("insider_signals", "Insider Conviction Radar", "Insider_Buy"),
            "28": ("rs_momentum", "RS Momentum & Phelps Base", "Type"),
            "29": ("fakeout_analyzer", "Morning Fakeout Radar", "Type"),
        }

        categories = get_categorized_menu(strategies)

        # Verify categories exist
        self.assertIn("Technicals", categories)
        self.assertIn("Institutional", categories)
        self.assertIn("Experimental / ML", categories)

        # Verify specific mappings (examples)
        self.assertIn("1", [opt[0] for opt in categories["Technicals"]])
        self.assertIn("23", [opt[0] for opt in categories["Institutional"]])
        self.assertIn("14", [opt[0] for opt in categories["Experimental / ML"]])

        # Verify all strategies are present exactly once across categories
        all_mapped_options = []
        for opts in categories.values():
            all_mapped_options.extend([opt[0] for opt in opts])

        self.assertEqual(sorted(all_mapped_options), sorted(list(strategies.keys())))


if __name__ == "__main__":
    unittest.main()
