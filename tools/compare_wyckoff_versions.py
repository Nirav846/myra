"""Compare hardcoded vs dynamic threshold Wyckoff scanners."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import date, timedelta

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import COLUMNS_13, load_ohlcv_for_universe

SCAN_DATES = ['2025-10-15', '2025-12-15', '2026-02-15', '2026-04-15']
N_SYMBOLS = 50
LOOKBACK_DAYS = 90
MIN_ROWS = max(55, int(LOOKBACK_DAYS * 0.6) + 5)

def collect_events(automaton, sampled_symbols, scan_date_str):
    scan_date = date.fromisoformat(scan_date_str)
    min_date = (scan_date - timedelta(days=LOOKBACK_DAYS)).isoformat()
    symbols = [s.strip() for s in sampled_symbols]
    bulk = load_ohlcv_for_universe(min_date, scan_date_str, symbols=symbols)
    automaton._bulk_data = bulk

    events_out = []
    for symbol in symbols:
        tech = automaton._get_tech_data(symbol, min_date, max_date=scan_date_str)
        if len(tech) < MIN_ROWS:
            continue
        df = pd.DataFrame(tech, columns=list(COLUMNS_13))
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) < MIN_ROWS:
            continue
        events = automaton._detect_events(df, symbol=symbol, as_on_date=scan_date_str)
        events_out.extend(events)
    return events_out

def main():
    from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton
    from myra_app.strategies.wyckoff_automaton_dynamic import WyckoffAutomaton as WyckoffDynamic

    print(f"Running backtest: Hardcoded vs Dynamic thresholds")
    print(f"Scan dates: {SCAN_DATES}")
    print(f"Symbols per date: {N_SYMBOLS}")
    print()

    for label, cls in [("Hardcoded", WyckoffAutomaton), ("Dynamic", WyckoffDynamic)]:
        print(f"\n=== {label} ===")
        all_events = []
        for date_str in SCAN_DATES:
            try:
                scanner = cls(lookback_days=90)
                raw_universe = scanner._get_universe()
                if isinstance(raw_universe[0], tuple):
                    universe = [r[0] for r in raw_universe[:N_SYMBOLS]]
                else:
                    universe = raw_universe[:N_SYMBOLS]
                events = collect_events(scanner, universe, date_str)
                all_events.extend(events)
                print(f"  {date_str}: {len(events)} events")
            except Exception as e:
                print(f"  {date_str}: ERROR - {e}")

        if not all_events:
            print("  No events found.")
            continue

        df = pd.DataFrame(all_events)
        print(f"\n  Total events: {len(df)}")
        print(f"  Event types:")
        for etype, count in df['event'].value_counts().items():
            print(f"    {etype:10} {count:5}")

        if 'quality' in df.columns:
            print(f"  Quality stats:")
            for etype in df['event'].unique():
                subset = df[df['event'] == etype]
                print(f"    {etype:10} mean={subset['quality'].mean():.2f}  std={subset['quality'].std():.2f}  n={len(subset)}")

        if 'vol_ratio' in df.columns:
            print(f"  Vol ratio: mean={df['vol_ratio'].mean():.2f}, max={df['vol_ratio'].max():.2f}")

    print("\nDone.")

if __name__ == "__main__":
    main()
