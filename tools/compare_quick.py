"""Quick compare hardcoded vs dynamic Wyckoff scanners on 10 symbols, 1 date."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
import pandas as pd
from myra_app.db.bulk_loader import COLUMNS_13, load_ohlcv_for_universe

SCAN_DATE = '2025-12-15'
N_SYMBOLS = 100
LOOKBACK_DAYS = 90
MIN_ROWS = max(55, int(LOOKBACK_DAYS * 0.6) + 5)

def collect_events(automaton, symbols, scan_date_str):
    scan_d = date.fromisoformat(scan_date_str)
    min_date = (scan_d - timedelta(days=LOOKBACK_DAYS)).isoformat()
    bulk = load_ohlcv_for_universe(min_date, scan_date_str, symbols=symbols)
    automaton._bulk_data = bulk
    events_out = []
    for sym in symbols:
        tech = automaton._get_tech_data(sym, min_date, max_date=scan_date_str)
        if len(tech) < MIN_ROWS:
            continue
        df = pd.DataFrame(tech, columns=list(COLUMNS_13))
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) < MIN_ROWS:
            continue
        events = automaton._detect_events(df, symbol=sym, as_on_date=scan_date_str)
        events_out.extend(events)
    return events_out

def main():
    from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton
    from myra_app.strategies.wyckoff_automaton_dynamic import WyckoffAutomaton as WyckoffDynamic

    print("Quick comparison: Hardcoded vs Dynamic on 10 symbols\n")

    for label, cls in [("Hardcoded", WyckoffAutomaton), ("Dynamic", WyckoffDynamic)]:
        print(f"=== {label} ===")
        try:
            scanner = cls(lookback_days=90)
            raw = scanner._get_universe()
            if isinstance(raw[0], tuple):
                universe = [r[0] for r in raw[:N_SYMBOLS]]
            else:
                universe = raw[:N_SYMBOLS]
            events = collect_events(scanner, universe, SCAN_DATE)
            print(f"  Events: {len(events)}")
            if events:
                df = pd.DataFrame(events)
                print(f"  Types: {df['event'].value_counts().to_dict()}")
                if 'quality' in df.columns:
                    for et in df['event'].unique():
                        s = df[df['event'] == et]
                        print(f"  {et:10} n={len(s):3}  quality mean={s['quality'].mean():.2f}  std={s['quality'].std():.2f}")
            else:
                print("  No events.")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
        print()

if __name__ == "__main__":
    main()
