import pandas as pd
import time
from myra_app.fetcher import SchemaContractEnforcer


def generate_data(rows=100000):
    data = {
        "symbol": ["REL"] * rows,
        "close_price": [str(i % 100) for i in range(rows)],
        "total_traded_qty": [str(i % 1000) for i in range(rows)],
        "delivery_qty": [str(i % 500) for i in range(rows)],
        "open_price": [str(i % 100) for i in range(rows)],
        "high_price": [str(i % 105) for i in range(rows)],
        "low_price": [str(i % 95) for i in range(rows)],
        "sector": ["FINANCE"] * rows,
    }
    return pd.DataFrame(data)


def run_benchmark():
    contract = {
        "required_columns": {
            "symbol": str,
            "close_price": float,
            "total_traded_qty": float,
        },
        "optional_columns": {
            "delivery_qty": float,
            "open_price": float,
            "high_price": float,
            "low_price": float,
            "sector": str,
        },
    }
    enforcer = SchemaContractEnforcer(contract)

    df = generate_data(200000)

    start_time = time.time()
    errors = enforcer.validate(df)
    end_time = time.time()

    print(f"Validation took: {end_time - start_time:.4f} seconds")
    print(f"Errors found: {len(errors)}")
    # print(errors)


if __name__ == "__main__":
    run_benchmark()
