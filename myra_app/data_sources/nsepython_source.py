# myra_app/data_sources/nsepython_source.py
from .base import BaseDataSource

class NSEPythonSource(BaseDataSource):
    def fetch(self, symbol):
        try:
            from nsepython import nse_get_financial_results
        except ImportError:
            raise Exception("nsepython not installed")

        clean_symbol = symbol.split('.')[0].upper()
        data = nse_get_financial_results(clean_symbol)
        
        if not data or not isinstance(data, list):
            return None

        # Optimized with list comprehension (Fix 19: Avoid .append in loop)
        return [{
            "date": item.get("period"),
            "totalRevenue": item.get("revenue"),
            "netProfit": item.get("profit"),
            "eps": item.get("eps")
        } for item in data]
