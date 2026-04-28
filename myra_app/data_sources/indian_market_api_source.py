# myra_app/data_sources/indian_market_api_source.py
import requests
from urllib.parse import quote
from .base import BaseDataSource
import logging

class IndianMarketAPISource(BaseDataSource):
    """
    Primary fundamentals source – free, no API key, batch-capable.
    API documentation: https://github.com/0xramm/Indian-Stock-Market-API
    Base URL: http://65.0.104.9/
    """
    BASE_URL = "http://65.0.104.9/"

    def fetch(self, symbol):
        """Single-symbol fetch (used by fallback chain)."""
        clean_symbol = symbol.split(".")[0].upper()
        yf_symbol = f"{clean_symbol}.NS"
        
        url = f"{self.BASE_URL}/stock?symbol={yf_symbol}&res=num"
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not data or "error" in data:
                return None
                
            # Single endpoint wraps metrics in a "data" object
            metrics = data.get("data", data)
            
            return [self._normalize(metrics, clean_symbol)]
        except Exception as e:
            logging.debug(f"Fetch failed for {symbol}: {e}")
            return None

    def fetch_batch(self, symbols, max_per_request=20):
        """
        Fetch fundamentals for multiple symbols in batches.
        Returns list of dicts, each dict representing one symbol's data.
        """
        all_results = []
        yf_symbols = [f"{s.split('.')[0].upper()}.NS" for s in symbols]
        
        for i in range(0, len(yf_symbols), max_per_request):
            batch = yf_symbols[i:i+max_per_request]
            symbols_param = ",".join(quote(s) for s in batch)
            url = f"{self.BASE_URL}/stock/list?symbols={symbols_param}&res=num"
            try:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                data = r.json()
                
                items = []
                
                # CRITICAL FIX: The batch endpoint wraps the data in "stocks", not "data"
                if isinstance(data, dict):
                    if "stocks" in data and isinstance(data["stocks"], list):
                        items = data["stocks"]
                    # Fallback if it returns the single stock format
                    elif "data" in data:
                        if isinstance(data["data"], list):
                            items = data["data"]
                        else:
                            items = [data]
                    else:
                        items = [data]
                elif isinstance(data, list):
                    items = data

                for item in items:
                    # Extract the symbol correctly depending on what the API sent back
                    raw_sym = item.get("symbol") or item.get("ticker") or ""
                    ret_symbol = raw_sym.split(".")[0].upper()
                    
                    if not ret_symbol:
                        if len(batch) == 1:
                            ret_symbol = batch[0].split(".")[0].upper()
                        else:
                            continue
                            
                    # Extract metrics wrapper if it exists (for single endpoint fallback)
                    metrics = item.get("data", item)
                    all_results.append(self._normalize(metrics, ret_symbol))

            except Exception as e:
                logging.error(f"Batch fetch error for {url}: {e}")
                continue
        
        return all_results

    def _normalize(self, raw, symbol):
        """Convert API response into MYRA standard format."""
        # The API wraps metrics in a "data" dict with flat values
        if isinstance(raw, dict) and "data" in raw:
            metrics = raw["data"]
        else:
            metrics = raw

        # If symbol is missing, use the one passed
        sym = metrics.get("symbol") or symbol

        return {
            "symbol": sym.split(".")[0].upper() if "." in str(sym) else sym.upper(),
            "report_date": "LATEST",
            "period_end": None,
            "revenue": None,
            "net_profit": None,
            "eps": metrics.get("earnings_per_share"),
            "roe": metrics.get("roe"),
            "debt": metrics.get("total_debt") or metrics.get("debt_to_equity"),
            "book_value": metrics.get("book_value"),
            "market_cap": metrics.get("market_cap"),
            "stock_pe": (metrics.get("pe_ratio")
                         or metrics.get("trailing_pe")
                         or metrics.get("forward_pe")),
            "industry_pe": None,
            "dividend_yield": metrics.get("dividend_yield"),
            "sector": metrics.get("sector") or metrics.get("industry"),
            "source": "indian_market_api",
        }