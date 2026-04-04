from myra_app.librarian import Librarian

def force_refresh():
    lib = Librarian()
    print("--- [MAINTENANCE] Forcing indicator recalculation for entire database ---")
    try:
        lib.update_indicator_history()
        print("Success: Indicators recalculated.")
        
        # Verify
        max_p = lib.conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        max_i = lib.conn.execute("SELECT MAX(date) FROM calculated_indicators").fetchone()[0]
        print(f"Prices Max: {max_p}")
        print(f"Indicators Max: {max_i}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        lib.close()

if __name__ == "__main__":
    force_refresh()
