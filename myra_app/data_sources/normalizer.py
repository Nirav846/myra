from datetime import datetime
import calendar

def safe_float(val):
    if val is None: return None
    try:
        # Handle cases like "1,200.50", "15%", or "-"
        clean_val = str(val).replace(",", "").replace("%", "").strip()
        if clean_val == "-" or not clean_val:
            return None
        return float(clean_val)
    except (ValueError, TypeError):
        return None

def derive_period_end(report_date):
    """Converts 'Dec 2025' or '2025-12' to '2025-12-31'."""
    if not report_date: return None
    try:
        # Handle 'Dec 2025'
        if len(report_date.split()) == 2:
            mon, year = report_date.split()
            # Convert month name to number
            month_num = list(calendar.month_abbr).index(mon[:3].title())
            last_day = calendar.monthrange(int(year), month_num)[1]
            return f"{year}-{month_num:02d}-{last_day}"
        # Handle ISO-like dates
        if "-" in report_date:
            parts = report_date.split("-")
            if len(parts) >= 2:
                year, month = int(parts[0]), int(parts[1])
                last_day = calendar.monthrange(year, month)[1]
                return f"{year}-{month:02d}-{last_day}"
    except Exception: pass
    return report_date

def normalize(data, source):
    """
    Normalizes raw data from various sources into a standard MYRA format.
    """
    # Optimized with list comprehension (Fix 50, 77, 93: Avoid .append in loop)
    def _to_normalized(row):
        # Standardize field names across different potential raw inputs before specific source logic
        # Some sources might return 'book_value' while others 'Book Value'
        std_row = {str(k).lower().replace(" ", "_"): v for k, v in row.items()}
        report_date = row.get("report_date") or row.get("date")
        period_end = derive_period_end(report_date)
        
        if source in ["screener", "screener_in"]:
            return {
                "report_date": report_date,
                "period_end": period_end,
                "revenue": safe_float(row.get("revenue")),
                "net_profit": safe_float(row.get("net_profit")),
                "eps": safe_float(row.get("eps")),
                "roce": safe_float(row.get("roce")),
                "roe": safe_float(row.get("roe")),
                "debt": safe_float(row.get("debt")),
                "opm_pct": safe_float(row.get("opm_pct")),
                "interest": safe_float(row.get("interest")),
                "borrowings": safe_float(row.get("borrowings")),
                "cash_from_ops": safe_float(row.get("cash_from_ops")),
                "debtor_days": safe_float(row.get("debtor_days")),
                "inventory_days": safe_float(row.get("inventory_days")),
                "cwip": safe_float(row.get("cwip")),
                "promoter_holding": safe_float(row.get("promoter_holding")),
                "pledged_pct": safe_float(row.get("pledged_pct")),
                "industry_pe": safe_float(row.get("industry_pe")),
                "stock_pe": safe_float(row.get("stock_pe")),
                "book_value": safe_float(row.get("book_value")),
                "market_cap": safe_float(row.get("market_cap")),
                "sales_per_share": safe_float(row.get("sales_per_share")),
                "dividend_yield": safe_float(row.get("dividend_yield")),
                "source": source
            }
        elif source in ["google_finance", "finology", "google"]:
            return {
                "report_date": report_date,
                "period_end": period_end,
                "revenue": safe_float(row.get("revenue")),
                "net_profit": safe_float(row.get("net_profit")),
                "eps": safe_float(row.get("eps")),
                "roce": safe_float(row.get("roce")),
                "roe": safe_float(row.get("roe")),
                "debt": safe_float(row.get("debt")),
                "book_value": safe_float(row.get("book_value")),
                "market_cap": safe_float(row.get("market_cap")),
                "source": source
            }
        
        # Fallback for other sources (simplified)
        else:
            return {
                "report_date": report_date,
                "period_end": period_end,
                "revenue": safe_float(row.get("revenue") or row.get("totalRevenue")),
                "net_profit": safe_float(row.get("profit") or row.get("netProfit")),
                "eps": safe_float(row.get("eps")),
                "roce": safe_float(row.get("roce")),
                "roe": safe_float(row.get("roe")),
                "debt": safe_float(row.get("debt")),
                "book_value": safe_float(row.get("book_value")),
                "market_cap": safe_float(row.get("market_cap")),
                "source": source
            }

    return [_to_normalized(row) for row in data]
