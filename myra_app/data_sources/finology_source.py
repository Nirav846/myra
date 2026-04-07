# myra_app/data_sources/finology_source.py
import requests
from bs4 import BeautifulSoup
import re
from .base import BaseDataSource

class FinologySource(BaseDataSource):
    BASE_URL = "https://ticker.finology.in/company/{}"

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
            
            # Finology has clear table sections
            # We look for Quarterly Results
            data_map = {}
            
            tables = soup.find_all("table")
            for table in tables:
                # Find the parent or heading to identify the table
                heading = table.find_previous(["h2", "h3", "h4"])
                if heading and "Quarterly" in heading.get_text():
                    rows = table.find_all("tr")
                    if not rows: continue
                    
                    # Headers are usually the first row (Dates)
                    header_cells = rows[0].find_all(["th", "td"])
                    dates = [c.get_text().strip() for c in header_cells[1:]]
                    
                    for row in rows[1:]:
                        cells = row.find_all(["th", "td"])
                        if not cells: continue
                        metric_name = cells[0].get_text().strip().upper()
                        
                        for i, date_val in enumerate(dates):
                            if date_val not in data_map: data_map[date_val] = {"report_date": date_val}
                            val = self._clean_val(cells[i+1].get_text())
                            
                            # Fix 51, 53, 55: Avoid chained indexing
                            entry = data_map[date_val]
                            if "REVENUE" in metric_name or "SALES" in metric_name:
                                entry["revenue"] = val
                            elif "NET PROFIT" in metric_name:
                                entry["net_profit"] = val
                            elif "EPS" in metric_name:
                                entry["eps"] = val
                    break
            
            if not data_map: return None
            
            # Map into list, latest first
            # Optimized with list comprehension (Fix 66: Avoid .append in loop)
            def _to_result(d):
                row = data_map[d]
                row["source"] = "finology"
                return row

            return [_to_result(d) for d in sorted(data_map.keys(), reverse=True)]
        except Exception:
            return None

    def _clean_val(self, val):
        if not val: return None
        clean = val.replace(",", "").replace("%", "").replace("\xa0", "").strip()
        if clean == "-" or not clean: return None
        try:
            return float(clean)
        except ValueError:
            return None
