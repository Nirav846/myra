# myra_app/data_sources/indstocks_source.py
from .base import BaseDataSource


class INDStocksSource(BaseDataSource):
    def fetch(self, symbol):
        try:
            from indstocks import Stock
        except ImportError:
            raise Exception("indstocks not installed")

        clean_symbol = symbol.split(".")[0].upper()
        try:
            stock = Stock(clean_symbol)
            financials = stock.financials()
        except Exception as e:
            raise Exception(f"INDStocks error: {e}")

        if not financials:
            return None

        # Optimized with list comprehension (Fix 23: Avoid .append in loop)
        return [
            {
                "date": row.get("date"),
                "revenue": row.get("revenue"),
                "profit": row.get("net_profit"),
                "eps": row.get("eps"),
                "roce": row.get("roce"),
                "roe": row.get("roe"),
                "debt": row.get("debt"),
            }
            for row in financials
        ]
