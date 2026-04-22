import sys
import traceback
from myra_app.librarian import Librarian

TARGETS = sys.argv[1:] if len(sys.argv) > 1 else ["DCAL","WABAG","PARKHOTELS","DIVISLAB","METROPOLIS"]

if __name__ == '__main__':
    lib = Librarian(read_only=False)
    # Monkeypatch active universe
    lib.get_active_universe = lambda: TARGETS
    try:
        print(f"[MYRA] Updating Virtual Indicator Lake for {len(TARGETS)} symbols...")
        lib.update_indicator_history()

        # Validation
        for sym in TARGETS:
            df = lib.loader.indicators.load_indicators("precomputed", sym)
            if df is None or df.empty:
                print(f"[!] No indicators saved for {sym}")
                continue
            dup = df.index.duplicated().any()
            print(f"{sym}: rows={len(df)} duplicate_index={dup}")
            if sym.upper() == 'DIVISLAB':
                # attempt to read last IAS
                if 'ias' in df.columns:
                    last_ias = df['ias'].iloc[-1]
                    print(f"DIVISLAB last IAS={last_ias}")
                else:
                    print("DIVISLAB: 'ias' not found in indicators")
        print('Smoke targets completed without exceptions')
    except Exception:
        print('Smoke targets FAILED:')
        traceback.print_exc()
