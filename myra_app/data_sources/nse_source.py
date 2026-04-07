# myra_app/data_sources/nse_source.py
import requests
from .base import BaseDataSource


class NSESource(BaseDataSource):
    def fetch(self, symbol):
        # NOTE: Using Yahoo Finance Quote Summary API as a robust fallback
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}.NS?modules=financialData,defaultKeyStatistics"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            raise Exception(f"NSE fallback failed: HTTP {r.status_code}")

        json_data = r.json()
        try:
            # Fix 18: Avoid chained indexing
            summary = json_data.get("quoteSummary", {})
            results_list = summary.get("result", [])
            if not results_list:
                raise Exception("No results in NSE data")
            res = results_list[0]

            fd = res.get("financialData", {})
            ks = res.get("defaultKeyStatistics", {})
        except (KeyError, IndexError):
            raise Exception("Invalid NSE data format")

        return [
            {
                "report_date": None,
                "revenue": fd.get("totalRevenue", {}).get("raw"),
                "net_profit": fd.get("netIncomeToCommon", {}).get("raw"),
                "roe": fd.get("returnOnEquity", {}).get("raw"),
                "debt": fd.get("debtToEquity", {}).get("raw"),
                "source": "nse",
            }
        ]
