# myra_app/data_sources/google_finance_source.py
import requests
from bs4 import BeautifulSoup
import re
from .base import BaseDataSource

class GoogleFinanceSource(BaseDataSource):
    BASE_URL = "https://www.google.com/finance/quote/{}:NSE"

    def fetch(self, symbol):
        clean_symbol = symbol.split('.')[0].upper()
        url = self.BASE_URL.format(clean_symbol)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                return None
            
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Google Finance typically shows 4 quarterly columns in a table
            # We look for the table with financial metrics
            tables = soup.find_all("table")
            if not tables: return None
            
            financials = {}
            # Simplified heuristic: find rows with 'Revenue' or 'Net income'
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if not cells: continue
                    
                    row_name = cells[0].get_text().strip()
                    # Values are in cells 1, 2, 3, 4 (Quarterly)
                    if "Revenue" in row_name:
                        values = [self._clean_val(c.get_text()) for c in cells[1:]]
                        financials["revenue"] = values
                    if "Net income" in row_name:
                        values = [self._clean_val(c.get_text()) for c in cells[1:]]
                        financials["net_profit"] = values
                    if "EPS" in row_name:
                        values = [self._clean_val(c.get_text()) for c in cells[1:]]
                        financials["eps"] = values

            if not financials: return None
            
            # Map into standard MYRA rows (latest first)
            revs = financials.get("revenue", [])
            profs = financials.get("net_profit", [])
            eps_vals = financials.get("eps", [])
            
            # Optimized with list comprehension (Fix 63: Avoid .append in loop)
            def _to_result(i):
                r_val = revs[i] / 10000000 if revs[i] else None
                p_val = profs[i] / 10000000 if profs[i] else None
                return {
                    "report_date": f"Q-{i+1} (GF)",
                    "revenue": r_val,
                    "net_profit": p_val,
                    "eps": eps_vals[i] if i < len(eps_vals) else None,
                    "source": "google"
                }

            return [_to_result(i) for i in range(len(revs))]
        except Exception:
            return None

    def _clean_val(self, val):
        if not val: return None
        # Handle cases like "1.23T", "456.78B", "12.34M"
        val = val.replace("\xa0", "").strip()
        multiplier = 1
        if "T" in val: multiplier = 1000000000000
        elif "B" in val: multiplier = 1000000000
        elif "M" in val: multiplier = 1000000
        elif "K" in val: multiplier = 1000
        
        clean = re.sub(r'[^\d.]', '', val)
        try:
            return float(clean) * multiplier if clean else None
        except ValueError:
            return None
