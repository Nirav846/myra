# myra_app/data_sources/normalizer.py

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

def normalize(data, source):
    """
    Normalizes raw data from various sources into a standard MYRA format.
    """
    normalized = []
    
    for row in data:
        if source in ["screener", "screener_in"]:
            normalized.append({
                "report_date": row.get("report_date"),
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
                "sales_per_share": safe_float(row.get("sales_per_share")),
                "dividend_yield": safe_float(row.get("dividend_yield")),
                "source": source
            })
        elif source in ["google_finance", "finology", "google"]:
            normalized.append({
                "report_date": row.get("report_date"),
                "revenue": safe_float(row.get("revenue")),
                "net_profit": safe_float(row.get("net_profit")),
                "eps": safe_float(row.get("eps")),
                "roce": safe_float(row.get("roce")),
                "roe": safe_float(row.get("roe")),
                "debt": safe_float(row.get("debt")),
                "source": source
            })
        
        # Fallback for other sources (simplified)
        else:
            normalized.append({
                "report_date": row.get("date") or row.get("report_date"),
                "revenue": safe_float(row.get("revenue") or row.get("totalRevenue")),
                "net_profit": safe_float(row.get("profit") or row.get("netProfit")),
                "eps": safe_float(row.get("eps")),
                "roce": safe_float(row.get("roce")),
                "roe": safe_float(row.get("roe")),
                "debt": safe_float(row.get("debt")),
                "source": source
            })

    return normalized
