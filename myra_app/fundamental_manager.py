# myra_app/fundamental_manager.py
import duckdb
from datetime import datetime, date, timedelta
from .data_sources import RateLimiter, SourceManager, normalize

class FundamentalManager:
    """
    MYRA Fundamental Manager - The Orchestration Layer for Fundamentals (v2.4)
    Orchestrates data processing and storage. Delegates acquisition to fetcher.py.
    """
    def __init__(self, db_conn=None, fetcher=None):
        self.conn = db_conn
        self.fetcher = fetcher
        self.limiter = RateLimiter(rate_per_sec=2)
        self.source_manager = SourceManager()

    def set_connection(self, conn):
        self.conn = conn

    def set_fetcher(self, fetcher):
        self.fetcher = fetcher

    def is_stale(self, symbol, days=90):
        if not self.conn: return True
        symbol_clean = symbol.split('.')[0].upper()
        try:
            res = self.conn.execute("SELECT MAX(last_updated) FROM fundamentals WHERE symbol = ?", (symbol_clean,)).fetchone()
            if not res or not res[0]: return True
            last_date = res[0]
            if isinstance(last_date, str): last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
            return (date.today() - last_date).days > days
        except Exception: return True

    def fetch_fundamentals(self, symbol):
        """Delegates fetching to fetcher.py while maintaining source-aware failure tracking"""
        if not self.fetcher: return None
        
        # DataFetcher handles its own internal prioritisation and returns (data, source_name)
        raw_data, source_name = self.fetcher.fetch_fundamentals(symbol)
        
        if raw_data:
            # We still normalize here using our unified schema
            data = normalize(raw_data, source_name)
            self.source_manager.mark_success(source_name)
            return data
        else:
            # If all failed, mark major sources as failed for cool-off
            # (Fetcher already tried them)
            self.source_manager.mark_failure("screener", is_rate_limit=True)
            return None

    def calculate_f_score(self, symbol):
        """
        PKScreener Superpower: Piotroski F-Score (Proxy).
        Evaluates 4 key dimensions from quarterly data.
        """
        if not self.conn: return 0
        symbol_clean = symbol.split('.')[0].upper()
        try:
            df = self.conn.execute("SELECT * FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC", (symbol_clean,)).df()
            if len(df) < 2: return 0
            
            score = 0
            latest = df.iloc[0]
            prev = df.iloc[1]
            
            # 1. Profitability
            if latest.get('net_profit', 0) > 0: score += 1
            if latest.get('cash_from_ops', 0) > 0: score += 1
            if latest.get('net_profit', 0) > prev.get('net_profit', 0): score += 1
            if latest.get('cash_from_ops', 0) > latest.get('net_profit', 0): score += 1 # Quality
            
            # 2. Efficiency
            l_margin = latest.get('net_profit', 0) / latest.get('revenue', 1)
            p_margin = prev.get('net_profit', 0) / prev.get('revenue', 1)
            if l_margin > p_margin: score += 1
            
            # Scale to 0-9 if possible, or return raw 0-5
            return score
        except Exception: return 0

    def get_valuation_metrics(self, symbol):
        """Calculates Graham Number and Margin of Safety."""
        if not self.conn: return {}
        symbol_clean = symbol.split('.')[0].upper()
        try:
            # Get latest quarterly EPS and Book Value
            res = self.conn.execute("SELECT eps, book_value FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC LIMIT 1", (symbol_clean,)).fetchone()
            if not res: return {}
            eps, bv = res
            
            if eps and bv and eps > 0 and bv > 0:
                graham = (22.5 * eps * bv) ** 0.5
                return {"graham_number": round(graham, 2)}
        except Exception: pass
        return {}

    def get_smart_money_status(self, symbol):
        """
        PKScreener Superpower: Smart Money Accumulation Status.
        Fetches indicators from DuckDB (Librarian).
        """
        if not self.conn: return {}
        symbol_clean = symbol.split('.')[0].upper()
        try:
            res = self.conn.execute("SELECT smart_money_score, rdv, squeeze_flag FROM calculated_indicators WHERE symbol = ? ORDER BY date DESC LIMIT 1", (symbol_clean,)).fetchone()
            if res:
                score, rdv, squeeze = res
                status = "Accumulating" if score > 0.7 else "Neutral" if score > 0.4 else "Distributing"
                return {
                    "SmartMoneyScore": round(score, 2),
                    "RDV": round(rdv, 2),
                    "Squeeze": "YES" if squeeze else "NO",
                    "SmartMoneyStatus": status
                }
        except Exception: pass
        return {}

    def update_quarterly(self, symbol):
        if not self.conn: return
        data = self.fetch_fundamentals(symbol)
        if not data: return

        symbol_clean = symbol.split('.')[0].upper()
        
        # 1. Update Detailed Quarterly Table
        for row in data:
            try:
                # strictly map to quarterly_results schema and add book_value dynamically
                try:
                    self.conn.execute("ALTER TABLE quarterly_results ADD COLUMN book_value REAL")
                except Exception:
                    pass # already exists
                
                cols = ['symbol', 'report_date', 'revenue', 'net_profit', 'eps', 'opm_pct', 'book_value']
                placeholders = ", ".join(["?" for _ in cols])
                col_names = ", ".join(cols)
                
                values = []
                for c in cols:
                    if c == 'symbol': values.append(symbol_clean)
                    else: values.append(row.get(c))

                query = f"INSERT OR REPLACE INTO quarterly_results ({col_names}) VALUES ({placeholders})"
                self.conn.execute(query, values)
            except Exception: pass

        # 2. Update Main Fundamentals Summary Table (PKScreener Superpower)
        try:
            latest = data[0]
            prev = data[1] if len(data) > 1 else None
            
            pe = latest.get("stock_pe") or latest.get("pe")
            roe = latest.get("roe")
            eps = latest.get("eps")
            bv = latest.get("book_value")
            mcap = latest.get("market_cap") # Note: Some sources might not provide this in quarterly data
            
            # Calculate Growth Metrics (YoY or QoQ based on availability)
            profit_growth = 0
            sales_growth = 0
            if prev:
                l_profit = latest.get("net_profit")
                p_profit = prev.get("net_profit")
                if l_profit and p_profit and p_profit != 0:
                    profit_growth = round(((l_profit - p_profit) / abs(p_profit)) * 100, 2)
                
                l_rev = latest.get("revenue")
                p_rev = prev.get("revenue")
                if l_rev and p_rev and p_rev != 0:
                    sales_growth = round(((l_rev - p_rev) / p_rev) * 100, 2)

            # Update summary table
            # We preserve existing sector/industry/holdings if they were already there
            self.conn.execute(f"""
                INSERT INTO fundamentals (symbol, pe, roe, eps, book_value, profit_growth, sales_growth, market_cap, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    pe = EXCLUDED.pe,
                    roe = EXCLUDED.roe,
                    eps = EXCLUDED.eps,
                    book_value = EXCLUDED.book_value,
                    profit_growth = EXCLUDED.profit_growth,
                    sales_growth = EXCLUDED.sales_growth,
                    market_cap = COALESCE(EXCLUDED.market_cap, fundamentals.market_cap),
                    last_updated = EXCLUDED.last_updated
            """, (symbol_clean, pe, roe, eps, bv, profit_growth, sales_growth, mcap, date.today()))
            
        except Exception: pass
