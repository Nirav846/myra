import sys

path = r"D:\01screener\Myra\myra_web\routes\fund_traction.py"
with open(path, "r", encoding="utf-8", newline="") as f:
    src = f.read()

old = """    sort: str = Query(
        "score", description="score|funds|share_change|weight_delta|name|pct_vs_sma"
    ),
    search: str = Query("", description="Match name/symbol/NSE/sector"),
    include_funds: bool = Query(True, description="Include per-fund breakdown lines"),
) -> dict:
    """Traction Board payload: month tabs, filters, per-fund cards, stats."""
    from myra_app import fund_traction_sync as fts

    return fts.get_traction_board(
        month=month,
        board_filter=filter,
        sort_by=sort,
        search=search,
        include_funds=include_funds,
    )"""

new = """    sort: str = Query(
        "score", description="score|funds|share_change|weight_delta|name|pct_vs_sma|mcap"
    ),
    mcap_bucket: str = Query(
        "",
        description="Filter by market-cap class: large|mid|small|unknown (empty = all)",
    ),
    search: str = Query("", description="Match name/symbol/NSE/sector"),
    include_funds: bool = Query(True, description="Include per-fund breakdown lines"),
) -> dict:
    """Traction Board payload: month tabs, filters, per-fund cards, stats."""
    from myra_app import fund_traction_sync as fts

    return fts.get_traction_board(
        month=month,
        board_filter=filter,
        sort_by=sort,
        search=search,
        include_funds=include_funds,
        mcap_bucket=mcap_bucket,
    )"""

if old not in src:
    print("ANCHOR NOT FOUND")
    sys.exit(1)

src2 = src.replace(old, new, 1)
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src2)
print("patched", path)
