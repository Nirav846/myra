"""
tools/derive_sector_from_industry.py

For NIFTY 500 symbols where sector was reset to NULL but industry is intact,
derives the correct sector using an NSE industry → sector mapping.

This restores the 482 blue-chip stocks that lost their sector classification.
Run after check_industry_data.py confirms industry data is present.
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

# NSE industry → normalized sector mapping
# Based on NiftyIndices classification + Morningstar sector alignment
INDUSTRY_TO_SECTOR = {
    # Financials
    "Financials":                   "Financials",
    "Financial Services":           "Financials",
    "Banks":                        "Financials",
    "Insurance":                    "Financials",
    "Diversified Financials":       "Financials",
    "Asset Management":             "Financials",

    # Technology
    "IT":                           "Technology",
    "Information Technology":       "Technology",
    "Software & Services":          "Technology",
    "Technology":                   "Technology",

    # Consumer Cyclical
    "Consumer Discretionary":       "Consumer Cyclical",
    "Consumer Services":            "Consumer Cyclical",
    "Retailing":                    "Consumer Cyclical",
    "Automobiles":                  "Consumer Cyclical",
    "Auto Components":              "Consumer Cyclical",
    "Textiles":                     "Consumer Cyclical",
    "Media & Entertainment":        "Consumer Cyclical",
    "Hotels Restaurants & Leisure": "Consumer Cyclical",

    # Consumer Defensive
    "FMCG":                         "Consumer Defensive",
    "Consumer Staples":             "Consumer Defensive",
    "Food Products":                "Consumer Defensive",
    "Beverages":                    "Consumer Defensive",
    "Tobacco":                      "Consumer Defensive",
    "Household Products":           "Consumer Defensive",

    # Industrials
    "Capital Goods":                "Industrials",
    "Industrials":                  "Industrials",
    "Industrial Conglomerates":     "Industrials",
    "Machinery":                    "Industrials",
    "Electrical Equipment":         "Industrials",
    "Construction":                 "Industrials",
    "Infrastructure":               "Industrials",
    "Transportation":               "Industrials",
    "Aerospace & Defense":          "Industrials",
    "Services":                     "Industrials",
    "Logistics":                    "Industrials",

    # Basic Materials
    "Metals":                       "Basic Materials",
    "Mining":                       "Basic Materials",
    "Chemicals":                    "Basic Materials",
    "Fertilisers & Agrochemicals":  "Basic Materials",
    "Cement":                       "Basic Materials",
    "Construction Materials":       "Basic Materials",
    "Packaging":                    "Basic Materials",
    "Paper":                        "Basic Materials",

    # Pharma / Healthcare
    "Pharma":                       "Pharma",
    "Pharmaceuticals":              "Pharma",
    "Healthcare":                   "Pharma",
    "Biotechnology":                "Pharma",
    "Healthcare Equipment":         "Pharma",
    "Medical Devices":              "Pharma",

    # Energy
    "Oil Gas & Consumable Fuels":   "Energy",
    "Oil & Gas":                    "Energy",
    "Energy":                       "Energy",
    "Refineries":                   "Energy",
    "Petroleum Products":           "Energy",

    # Utilities
    "Utilities":                    "Utilities",
    "Power":                        "Utilities",
    "Gas Distribution":             "Utilities",
    "Water":                        "Utilities",

    # Real Estate
    "Real Estate":                  "Real Estate",
    "Realty":                       "Real Estate",

    # Communication Services
    "Communication Services":       "Communication Services",
    "Telecom":                      "Communication Services",
    "Media":                        "Communication Services",
}


def derive_sector(industry: str) -> str | None:
    if not industry:
        return None
    # Exact match first
    if industry in INDUSTRY_TO_SECTOR:
        return INDUSTRY_TO_SECTOR[industry]
    # Partial match fallback
    industry_lower = industry.lower()
    for key, sector in INDUSTRY_TO_SECTOR.items():
        if key.lower() in industry_lower or industry_lower in key.lower():
            return sector
    return None


meta_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
conn = sqlite3.connect(meta_db)

# Fetch all null-sector symbols that have industry data
rows = conn.execute("""
    SELECT symbol, industry
    FROM symbols_master
    WHERE (sector IS NULL OR sector = '')
      AND industry IS NOT NULL AND industry != ''
      AND sector_locked = 0
""").fetchall()

print(f"Symbols with industry but no sector: {len(rows)}")
print("Deriving sectors...\n")

updates = []
unmapped = []
for symbol, industry in rows:
    sector = derive_sector(industry)
    if sector:
        updates.append((sector, "NSE_DERIVED", symbol))
    else:
        unmapped.append((symbol, industry))

# Apply updates
if updates:
    conn.executemany("""
        UPDATE symbols_master
        SET sector = ?, source = ?
        WHERE symbol = ? AND (sector_locked = 0 OR sector_locked IS NULL)
    """, updates)
    conn.commit()
    print(f"  Updated : {len(updates)} symbols with derived sector")

if unmapped:
    print(f"  Unmapped: {len(unmapped)} symbols (industry not in mapping)")
    for sym, ind in unmapped[:20]:
        print(f"    {sym:<22} industry='{ind}'")
    if len(unmapped) > 20:
        print(f"    ... and {len(unmapped) - 20} more")

# Final report
print("\n=== SECTOR COVERAGE AFTER DERIVATION ===")
total = conn.execute("SELECT COUNT(*) FROM symbols_master").fetchone()[0]
with_sector = conn.execute(
    "SELECT COUNT(*) FROM symbols_master WHERE sector IS NOT NULL AND sector != ''"
).fetchone()[0]
n500_gaps = conn.execute("""
    SELECT COUNT(*) FROM symbols_master
    WHERE in_nifty500 = 1 AND (sector IS NULL OR sector = '')
""").fetchone()[0]

print(f"Total symbols : {total}")
print(f"With sector   : {with_sector} ({with_sector / total * 100:.1f}%)")
print(f"Missing sector: {total - with_sector}")
print(f"NIFTY 500 gaps: {n500_gaps}")

print("\nSector breakdown:")
rows = conn.execute("""
    SELECT sector, COUNT(*) as cnt
    FROM symbols_master
    WHERE sector IS NOT NULL AND sector != ''
    GROUP BY sector ORDER BY cnt DESC
""").fetchall()
for r in rows:
    print(f"  {r[1]:>5}  {r[0]}")

conn.close()
print("\nDone. Run check_sectors.py to confirm.")
