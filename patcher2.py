import io

p = r"D:\01screener\Myra\myra_app\fund_traction_sync.py"
with open(p, "r", encoding="utf-8", newline="") as f:
    src = f.read()

src_n = src.replace("\r\n", "\n")

# --- 1) comment + type + prior-month range ---
old1 = """    # Latest close from technical_data (technical DB, read-only).
    price_map: dict[str, float] = {}
"""
new1 = """    # Latest close + prior-month close from technical_data (read-only).
    price_map: dict[str, dict[str, float | None]] = {}
"""
assert old1 in src_n, "1"
src = src_n.replace(old1, new1, 1)
print("ok1")

# --- 2) window query: latest + prior-month ---
old2 = """                    for sym, close in tconn.execute(
                        f"""\nSELECT symbol, close FROM (
                                SELECT symbol, close,
                                       ROW_NUMBER() OVER (
                                           PARTITION BY symbol ORDER BY date DESC
                                       ) AS rn
                                FROM technical_data
                                WHERE symbol IN ({ph}) AND close IS NOT NULL
                            ) WHERE rn = 1""",
                        chunk,
                    ).fetchall():
                        price_map[sym] = close"""
new2 = """                    base = f"""\nSELECT symbol, close FROM (
                            SELECT symbol, close,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY symbol ORDER BY date DESC
                                   ) AS rn_latest,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY symbol, strftime('%Y-%m', date)
                                   ) AS rn_prior
                            FROM technical_data
                            WHERE symbol IN ({ph}) AND close IS NOT NULL
                        ) WHERE rn_latest = 1"""
                    if prior_month:
                        base += f""" OR rn_prior = 1 AND strftime('%Y-%m', date) = {prior_month!r}"""
                    for sym, close in tconn.execute(base, chunk).fetchall():
                        if sym not in price_map:
                            price_map[sym] = {}
                        price_map[sym]["latest"] = close"""
assert old2 in src_n, "2"
src = src_n.replace(old2, new2, 1)
print("ok2")

# --- 3) prior-month store + comparison in final loop ---
old3 = """    for r in rows:
        mc = mcap_map.get(r["symbol"])
        r["market_cap_cr"] = round(mc / 1e7, 1) if mc else None
        r["mcap_bucket"] = _mcap_bucket(mc)
        closes = price_map.get(r["symbol"], {})
        r["price"] = price_map.get(r["symbol"])
"""
new3 = """    for r in rows:
        mc = mcap_map.get(r["symbol"])
        r["market_cap_cr"] = round(mc / 1e7, 1) if mc else None
        r["mcap_bucket"] = _mcap_bucket(mc)
        closes = price_map.get(r["symbol"], {})
        r["price"] = closes.get("latest")
        r["prev_month_close"] = closes.get("prior")
        latest = r["price"]
        prior = r["prev_month_close"]
        if latest and prior:
            r["pct_vs_prev"] = round((latest - prior) / prior * 100, 2)
        else:
            r["pct_vs_prev"] = None
"""
assert old3 in src_n, "3"
src = src_n.replace(old3, new3, 1)
print("ok3")

with open(p, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("patched")
