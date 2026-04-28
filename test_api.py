from myra_app.data_sources.indian_market_api_source import IndianMarketAPISource
import traceback

src = IndianMarketAPISource()
failing = ['AHCL','ALOKINDS','ASTRAZEN','ATHERENERG','AUBANK','BAJAJHFL']

for sym in failing:
    try:
        data = src.fetch(sym)
        if data:
            print(f"{sym}: OK -> PE={data[0].get('stock_pe')}, ROE={data[0].get('roe')}, MCap={data[0].get('market_cap')}")
        else:
            print(f"{sym}: FAILED (returned None)")
    except Exception as e:
        print(f"{sym}: EXCEPTION - {type(e).__name__}: {e}")
        traceback.print_exc()
    print()
