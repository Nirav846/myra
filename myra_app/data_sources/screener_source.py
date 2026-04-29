# myra_app/data_sources/screener_source.py
import re
import statistics

import requests
from bs4 import BeautifulSoup

from .base import BaseDataSource


class ScreenerSource(BaseDataSource):
    BASE_URL = "https://www.screener.in/company/{}/consolidated/"
    PEERS_API = "https://www.screener.in/api/company/{}/peers/"

    def _clean_val(self, val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        clean = str(val).replace(",", "").replace("%", "").replace("\xa0", "").strip()
        clean = clean.split("+")[0].strip()
        try:
            return float(clean) if clean and clean != "-" else None
        except ValueError:
            return None

    def fetch(self, symbol):
        clean_symbol = symbol.split(".")[0].upper()
        url = self.BASE_URL.format(clean_symbol)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        }

        session = requests.Session()
        r = session.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            raise Exception(f"Screener fetch failed: {r.status_code}")

        soup = BeautifulSoup(r.text, "html.parser")

        company_id = None
        body = soup.find("body")
        if body:
            match = re.search(r'data-company-id="(\d+)"', str(body))
            if match:
                company_id = match.group(1)

        top_metrics = {}
        list_items = soup.find_all("li", class_=re.compile(r"flex.*"))
        mcap = 0
        price = 0

        for li in list_items:
            name_span = li.find("span", class_="name")
            val_span = li.find("span", class_="number")
            if name_span and val_span:
                name = name_span.get_text().strip().upper()
                val = val_span.get_text().strip()
                c_val = self._clean_val(val)

                if "MARKET CAP" in name:
                    mcap = c_val
                if "CURRENT PRICE" in name:
                    price = c_val
                if "DEBT TO EQUITY" in name:
                    top_metrics["debt"] = c_val
                if "ROCE" in name:
                    top_metrics["roce"] = c_val
                if "ROE" in name or "RETURN ON EQUITY" in name:
                    top_metrics["roe"] = c_val
                if "STOCK P/E" in name:
                    top_metrics["stock_pe"] = c_val
                if "BOOK VALUE" in name:
                    top_metrics["book_value"] = c_val
                if "DIVIDEND YIELD" in name:
                    top_metrics["dividend_yield"] = c_val
                if "PLEDGED" in name:
                    top_metrics["pledged_pct"] = c_val
                if "PROMOTER" in name:
                    top_metrics["promoter_holding"] = c_val

        total_shares = (mcap * 10000000) / price if mcap and price else None

        data_map = {}

        # 3. Quarterly Results
        tables = soup.find_all("table")
        for table in tables:
            section = table.find_parent("section")
            section_text = section.get_text() if section else ""
            if "Quarterly" in section_text or "Quarter" in section_text:
                rows = table.find_all("tr")
                if not rows:
                    continue

                # Headers: [Name, Date1, Date2, ...]
                headers_row = rows[0].find_all(["th", "td"])
                # Optimized with list comprehension (Fix 86: Avoid .append in loop)
                date_headers = [h.get_text().strip() for h in headers_row[1:]]

                for row in rows[1:]:
                    m_tag = row.find(["th", "td"])
                    if not m_tag:
                        continue
                    m_name = m_tag.get_text().strip().upper()

                    tds = row.find_all("td")
                    for i, td in enumerate(tds):
                        if i >= len(date_headers):
                            break
                        d_val = date_headers[i]
                        if not d_val:
                            continue

                        if d_val not in data_map:
                            data_map[d_val] = {"report_date": d_val}
                        val = self._clean_val(td.get_text())

                        # Fix 102-106: Avoid chained indexing
                        entry = data_map[d_val]
                        if "SALES" in m_name or "REVENUE" in m_name:
                            entry["revenue"] = val
                        elif "NET PROFIT" in m_name:
                            entry["net_profit"] = val
                        elif "EPS" in m_name:
                            entry["eps"] = val
                        elif "OPM" in m_name:
                            entry["opm_pct"] = val
                        elif "INTEREST" in m_name:
                            entry["interest"] = val
                break

        # 4. Final Derived Merging & Sorting
        def sort_key(d_val):
            m = re.search(r"(\w+)\s+(\d{4})", d_val)
            if m:
                return f"{m.group(2)} {m.group(1)}"
            return d_val

        sorted_dates = sorted(data_map.keys(), key=sort_key, reverse=True)

        # Optimized with list comprehension (Fix 128: Avoid .append in loop)
        def _to_final(d):
            entry = data_map[d]
            # Merge top level metrics
            for k, v in top_metrics.items():
                if entry.get(k) is None:
                    entry[k] = v

            rev = entry.get("revenue")
            if rev and total_shares:
                entry["sales_per_share"] = round((rev * 10000000) / total_shares, 2)
            return entry

        final_list = [_to_final(d) for d in sorted_dates]

        if not final_list and top_metrics:
            top_metrics["report_date"] = "LATEST"
            final_list.append(top_metrics)

        return final_list
