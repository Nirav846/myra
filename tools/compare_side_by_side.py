"""Side-by-side backtest: hardcoded vs dynamic Wyckoff thresholds."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
import pandas as pd
from myra_app.db.bulk_loader import COLUMNS_13, load_ohlcv_for_universe

SCAN_DATES = ['2025-10-15', '2025-12-15', '2026-02-15']
N_SYMBOLS = 30
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

    print(f"Side-by-side: Hardcoded vs Dynamic thresholds")
    print(f"Scan dates: {SCAN_DATES}, Symbols: {N_SYMBOLS}\n")

    for label, cls in [("Hardcoded", WyckoffAutomaton), ("Dynamic", WyckoffDynamic)]:
        t0 = time.time()
        all_events = []
        for date_str in SCAN_DATES:
            scanner = cls(lookback_days=90)
            raw = scanner._get_universe()
            syms = [r[0].strip() for r in raw[:N_SYMBOLS]]
            events = collect_events(scanner, syms, date_str)
            all_events.extend(events)
            print(f"  [{label}] {date_str}: {len(events)} events")

        elapsed = time.time() - t0
        df = pd.DataFrame(all_events) if all_events else pd.DataFrame()
        print(f"\n--- {label} ---")
        print(f"  Total events: {len(df)}, Time: {elapsed:.1f}s")
        if not df.empty:
            print(f"  Event types:")
            for et, count in df['event'].value_counts().items():
                sub = df[df['event'] == et]
                q = sub['quality'].mean() if 'quality' in sub.columns else 0
                print(f"    {et:10} count={count:3}  avg_quality={q:.1f}")
        print()

if __name__ == "__main__":
    main()
