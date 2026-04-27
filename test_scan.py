from myra_app.librarian import Librarian
from myra_app.engine import Engine

lib = Librarian()
lib.connect()
e = Engine(librarian=lib)
symbols = lib.get_all_symbols()
results, payload = e.run_scan(symbols[:500], "106")
print(f"Found: {len(results)}")
for r in results:
    stock = r.get("Stock", "-")
    ltp = r.get("LTP", "-")
    stage = r.get("Stage", "-")
    deliv = r.get("Deliv_Pct", "-")
    stars = r.get("Stars", "-")
    print(f"{stock:15} LTP:{ltp:8} Stage:{stage:8} Deliv:{deliv} Stars:{stars}")
