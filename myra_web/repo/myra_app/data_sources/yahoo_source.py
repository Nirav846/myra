# myra_app/data_sources/yahoo_source.py
import requests
from .base import BaseDataSource


class YahooSource(BaseDataSource):
    def fetch(self, symbol):
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}.NS?modules=incomeStatementHistory,cashflowStatementHistory"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            raise Exception(f"Yahoo fundamental fetch failed: HTTP {r.status_code}")

        json_data = r.json()
        try:
            # Fix 17: Avoid chained indexing
            summary = json_data.get("quoteSummary", {})
            results_list = summary.get("result", [])
            if not results_list:
                return []
            res = results_list[0]

            income = res.get("incomeStatementHistory", {}).get(
                "incomeStatementHistory", []
            )
            if not income:
                return []

            latest = income[0]
            return [
                {
                    "report_date": latest.get("endDate", {}).get("fmt"),
                    "revenue": latest.get("totalRevenue", {}).get("raw"),
                    "net_profit": latest.get("netIncome", {}).get("raw"),
                    "source": "yahoo",
                }
            ]
        except (KeyError, IndexError):
            return []
