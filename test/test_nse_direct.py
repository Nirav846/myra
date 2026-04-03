
try:
    from nsepython import nse_get_index_quote
    print("nsepython imported successfully.")
    data = nse_get_index_quote("NIFTY 50")
    print(f"Raw NIFTY 50 Data keys: {data.keys() if data else 'None'}")
    if data:
        print(f"Advances: {data.get('advances')}")
        print(f"Declines: {data.get('declines')}")
        print(f"Full Data Snippet: {str(data)[:500]}")
except Exception as e:
    print(f"Error: {e}")
