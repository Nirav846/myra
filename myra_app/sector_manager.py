#!/usr/bin/env python
import os
import json
import pandas as pd
import sqlite3
from datetime import datetime
from io import StringIO
from myra_app.fetcher import GhostSession

class SectorManager:
    """
    MYRA Sector Intelligence Manager (v3.0 Atomic)
    Handles hybrid fetching, normalization, and meta.db updates.
    """
    def __init__(self, db_path="db/meta.db"):
        self.db_path = db_path
        self.session = GhostSession(cache_path="db/network_cache.sqlite")
        self.config_path = "config/sources.json"
        self.map_path = "config/sector_map.json"
        self._load_config()
        self._load_mappings()

    def _load_config(self):
        try:
            with open(self.config_path, 'r') as f:
                self.config = json.load(f).get("sector_intelligence", {})
        except: self.config = {}

    def _load_mappings(self):
        try:
            with open(self.map_path, 'r') as f:
                self.mappings = json.load(f)
        except: self.mappings = {}

    def normalize(self, raw_name):
        if not raw_name: return "Other"
        clean = raw_name.strip()
        return self.mappings.get(clean, clean)

    def fetch_nse_master(self):
        """Fetches official NSE industry classification files."""
        results = {}
        urls = [self.config.get("primary", {})] + self.config.get("supplemental", [])
        
        for source in urls:
            try:
                r = self.session.get(source["url"])
                # GhostSession (scrapling) returns a response object where content is in .content or .text
                if r and r.status_code == 200:
                    # Some scrapling versions use r.body or r.text
                    text_content = getattr(r, 'text', getattr(r, 'body', ''))
                    if not text_content: continue
                    
                    df = pd.read_csv(StringIO(text_content))
                    # NiftyIndices.com columns: Macro-Economic Sector, Sector, Industry, Basic Industry, Symbol...
                    sym_col = next((c for c in df.columns if 'SYMBOL' in c.upper()), None)
                    # We prioritize "Sector" over "Macro-Economic Sector" for the main 'sector' field
                    sec_col = next((c for c in df.columns if c.strip() == 'Sector'), 
                              next((c for c in df.columns if 'MACRO' in c.upper()), None))
                    ind_col = next((c for c in df.columns if 'INDUSTRY' in c.upper() and 'BASIC' not in c.upper()), None)

                    if sym_col and ind_col:
                        for _, row in df.iterrows():
                            symbol = str(row[sym_col]).upper().strip()
                            raw_sec = str(row[sec_col]).strip() if sec_col else "Unknown"
                            raw_ind = str(row[ind_col]).strip()
                            results[symbol] = {
                                "raw_sector": raw_sec,
                                "raw_industry": raw_ind,
                                "source": "NSE_INDEX",
                                "confidence": 1.0
                            }
            except Exception as e:
                print(f"[!] Error fetching {source.get('name')}: {e}")
        return results

    def fetch_symbol_fallback(self, symbol):
        """Fallback to Screener or yfinance for a single symbol."""
        # 1. Screener.in Fallback
        try:
            url = f"https://www.screener.in/company/{symbol}/consolidated/"
            r = self.session.get(url)
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                # Screener lists sector/industry in the top profile
                p = soup.find("p", class_="sub")
                if p:
                    links = p.find_all("a")
                    if len(links) >= 2:
                        return {
                            "raw_sector": links[0].get_text().strip(),
                            "raw_industry": links[1].get_text().strip(),
                            "source": "SCREENER",
                            "confidence": 0.8
                        }
        except: pass

        # 2. yfinance Fallback
        try:
            import yfinance as yf
            # Use a clean Ticker call without the GhostSession headers
            t = yf.Ticker(f"{symbol}.NS")
            info = t.info
            if info.get("sector"):
                return {
                    "raw_sector": info.get("sector"),
                    "raw_industry": info.get("industry"),
                    "source": "YFINANCE",
                    "confidence": 0.6
                }
        except: pass
        
        return None

    def update_symbol(self, symbol, data):
        if not data: return False
        
        sector = self.normalize(data["raw_sector"])
        industry = self.normalize(data["raw_industry"])
        now = datetime.now().isoformat()
        
        conn = sqlite3.connect(self.db_path)
        try:
            # Check if locked
            res = conn.execute("SELECT sector_locked FROM symbols_master WHERE symbol = ?", (symbol,)).fetchone()
            if res and res[0] == 1:
                return False
                
            conn.execute("""
                UPDATE symbols_master SET 
                    sector = ?, industry = ?, raw_sector = ?, raw_industry = ?,
                    source = ?, confidence = ?, last_updated_sector = ?
                WHERE symbol = ?
            """, (sector, industry, data["raw_sector"], data["raw_industry"], 
                  data["source"], data["confidence"], now, symbol))
            conn.commit()
            return True
        except Exception as e:
            print(f"[!] SQL Error for {symbol}: {e}")
            return False
        finally:
            conn.close()

    def sync_all(self):
        """Full Maintenance Sweep."""
        print("[MYRA] Starting Full Sector Sync...")
        # 1. Batch fetch from NSE
        nse_data = self.fetch_nse_master()
        
        conn = sqlite3.connect(self.db_path)
        symbols = [r[0] for r in conn.execute("SELECT symbol FROM symbols_master").fetchall()]
        conn.close()
        
        updated = 0
        for symbol in symbols:
            data = nse_data.get(symbol)
            if not data:
                # Try fallback for symbols not in Nifty lists
                data = self.fetch_symbol_fallback(symbol)
            
            if self.update_symbol(symbol, data):
                updated += 1
                if updated % 100 == 0: print(f" [+] Updated {updated} symbols...")
        
        print(f"[MYRA] Sync complete. Updated {updated} symbols.")

    def fetch_nse_meta_bulk(self, symbols):
        """Fetches industry info from NSE Meta API in parallel."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import requests
        import time
        results = {}
        
        # Pre-fetch cookies from home page
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/"
        }
        try:
            session.get("https://www.nseindia.com", headers=headers, timeout=10)
        except: pass

        def fetch_single(symbol):
            try:
                url = f"https://www.nseindia.com/api/equity-meta-info?symbol={symbol}"
                # Add a small delay to be polite
                time.sleep(1)
                r = session.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    ind_info = data.get("industryInfo", {})
                    if ind_info:
                        return symbol, {
                            "raw_sector": ind_info.get("macro", ind_info.get("sector", "Unknown")),
                            "raw_industry": ind_info.get("industry", "Unknown"),
                            "source": "NSE_API",
                            "confidence": 1.0
                        }
            except: pass
            return symbol, None

        print(f"[MYRA] Launching Parallel NSE Meta Fetcher for {len(symbols)} symbols...")
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_symbol = {executor.submit(fetch_single, s): s for s in symbols}
            for future in as_completed(future_to_symbol):
                symbol, data = future.result()
                if data:
                    results[symbol] = data
                    if len(results) % 50 == 0:
                        print(f" [+] Fetched {len(results)} symbols from NSE API...")
        return results

    def fetch_morningstar_bulk(self):
        """Fetches thousands of symbols via Morningstar Rest API (PKNSETools Path)."""
        results = {}
        url = "https://lt.morningstar.com/api/rest.svc/g9vi2nsqjb/security/screener"
        # We'll fetch 4 pages of 1000 to cover 4000 symbols
        for page in range(1, 5):
            params = {
                "page": page,
                "pageSize": 1000,
                "sortOrder": "name asc",
                "outputType": "json",
                "version": 1,
                "languageId": "en",
                "currencyId": "BAS",
                "universeIds": "E0EXG$XNSE",
                "securityDataPoints": "ticker,sectorName,industryName",
                "filters": "",
                "term": ""
            }
            try:
                # Use standard requests for this API as it's very reliable
                import requests
                r = requests.get(url, params=params, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    rows = data.get("rows", [])
                    if not rows: break
                    for row in rows:
                        symbol = str(row.get("ticker")).upper().strip()
                        if symbol:
                            results[symbol] = {
                                "raw_sector": row.get("sectorName", "Unknown"),
                                "raw_industry": row.get("industryName", "Unknown"),
                                "source": "MORNINGSTAR",
                                "confidence": 1.0
                            }
                    print(f" [+] Morningstar: Fetched {len(results)} symbols so far...")
                else: break
            except: break
        return results

    def incremental_sync(self):
        """Daily Hook: NULLs and Stale data (>90 days)."""
        conn = sqlite3.connect(self.db_path)
        sql = """
            SELECT symbol FROM symbols_master 
            WHERE (sector IS NULL OR sector = '' OR 
                  julianday('now') - julianday(last_updated_sector) > 90)
            AND (sector_locked = 0 OR sector_locked IS NULL)
        """
        stale_symbols = [r[0] for r in conn.execute(sql).fetchall()]
        conn.close()
        
        if not stale_symbols: return
        
        print(f"[MYRA] Incremental Sync: Found {len(stale_symbols)} symbols to refresh.")
        
        # 1. PRIMARY BULK FETCH: Morningstar (Fastest & most comprehensive)
        ms_data = self.fetch_morningstar_bulk()
        
        # 2. SECONDARY BULK FETCH: Official Index CSVs (High Fidelity)
        nse_data = self.fetch_nse_master()
        
        # Merge sources: NSE Master overrides Morningstar if both exist
        master_map = {**ms_data, **nse_data}
        
        # 3. BATCH UPDATE: Symbols found in Master maps
        in_master = [s for s in stale_symbols if s in master_map]
        remaining = [s for s in stale_symbols if s not in master_map]
        
        if in_master:
            self._batch_update_symbols(in_master, master_map)

        # 4. FALLBACK: Try yfinance/Screener for anything still missing (SMEs/New Listings)
        if remaining:
            print(f"[MYRA] Processing {len(remaining)} symbols via slow fallbacks...")
            chunk_size = 50
            for i in range(0, len(remaining), chunk_size):
                chunk = remaining[i:i+chunk_size]
                yf_results = {}
                for s in chunk:
                    data = self.fetch_symbol_fallback(s)
                    if data: yf_results[s] = data
                
                if yf_results:
                    self._batch_update_symbols(list(yf_results.keys()), yf_results)
                
        print("[MYRA] Sector Sync Logic complete.")

    def _batch_update_symbols(self, symbols, data_map):
        """Helper for high-speed SQL batching."""
        conn = sqlite3.connect(self.db_path)
        now = datetime.now().isoformat()
        updated = 0
        try:
            for symbol in symbols:
                data = data_map.get(symbol)
                if not data: continue
                sector = self.normalize(data["raw_sector"])
                industry = self.normalize(data["raw_industry"])
                conn.execute("""
                    UPDATE symbols_master SET 
                        sector = ?, industry = ?, raw_sector = ?, raw_industry = ?,
                        source = ?, confidence = ?, last_updated_sector = ?
                    WHERE symbol = ? AND (sector_locked = 0 OR sector_locked IS NULL)
                """, (sector, industry, data["raw_sector"], data["raw_industry"], 
                      data["source"], data["confidence"], now, symbol))
                updated += 1
            conn.commit()
            print(f"[MYRA] Batch updated {updated} symbols from {data_map[symbols[0]]['source']}.")
        except Exception as e: print(f"[!] Batch Error: {e}")
        finally: conn.close()

if __name__ == "__main__":
    mgr = SectorManager()
    mgr.sync_all()
