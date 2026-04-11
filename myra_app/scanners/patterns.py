import pandas as pd


class PatternScout:
    """
    MYRA Pattern Scout - Algorithmic Candlestick Recognition.
    Detects high-conviction bullish and bearish reversals + Price Tightness.
    """

    def __init__(self):
        pass

    def get_latest_pattern(self, df: pd.DataFrame) -> str:
        """Analyzes the last few candles for major patterns."""
        if len(df) < 10:
            return "-"

        try:
            # 1. Candlestick Patterns
            patterns = df.ta.cdl_pattern(
                name=[
                    "hammer",
                    "engulfing",
                    "morningstar",
                    "doji",
                    "shootingstar",
                    "invertedhammer",
                    "marubozu",
                ]
            )
            if patterns is not None and not patterns.empty:
                latest = patterns.iloc[-1]
                active = latest[latest != 0]
                if not active.empty:
                    p_name = active.index[0].replace("CDL_", "").title()
                    val = active.iloc[0]
                    return (
                        f"[green]Bullish {p_name}[/green]"
                        if val > 0
                        else f"[red]Bearish {p_name}[/red]"
                    )

            # 2. VCP / Tightness Detection (PKScreener Superpower)
            # Look for 3-5 days of narrowing range
            ranges = (df["High"] - df["Low"]).tail(5)
            if ranges.iloc[-1] < ranges.mean() * 0.6:
                return "[bold cyan]Tight Base[/bold cyan]"

            return "-"
        except Exception:
            return "-"
