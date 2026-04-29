import os

from myra_app.librarian import Librarian


def repair():
    lib = Librarian()
    print("--- [REPAIR] Dropping and Recreating calculated_indicators ---")
    try:
        lib.conn.execute("DROP TABLE IF EXISTS calculated_indicators")
        # _create_tables is called inside __init__ if Librarian is not read_only,
        # but since we already initialized, we call it manually
        lib._create_tables()
        print("Table recreated. Recalculating data...")
        lib.update_indicator_history()
        print("Success: Database repaired and indicators refreshed.")

        # Verify
        res = lib.conn.execute(
            "SELECT symbol, std20, d_poc, smc_phase FROM calculated_indicators LIMIT 5"
        ).df()
        print("Sample Results:")
        print(res)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        lib.close()


if __name__ == "__main__":
    repair()
