from myra_app.librarian import Librarian
from myra_app.engine import Engine

lib = Librarian()
lib.connect()
e = Engine(librarian=lib)
results, payload = e.run_scan(lib.get_all_symbols()[:100], "2")
print(f"Scanner 2 (Delivery Spikes) found: {len(results)}")
for r in results[:5]:
    stock = r.get("Stock", "-")
    ltp = r.get("LTP", "-")
    deliv = r.get("Deliv_Pct", "-")
    print(f"{stock:15} LTP:{ltp:8} Deliv:{deliv}")
