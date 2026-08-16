"""
MYRA Full Fundamentals Fetcher

Deep per-symbol fundamental data combining three sources:

1. Scrapling (headless-browser) scrape of Screener.in company pages:
   company id, top ratios (PE, ROE, ROCE, dividend yield, face value,
   market cap) and the latest shareholding pattern.
2. Screener.in chart API (plain requests): multi-year time series for
   Price-to-Book, P/E, ROCE and Market-Cap-to-Sales.
3. yfinance: analyst recommendations, debt/equity, growth, beta, sector.

Every step degrades gracefully. If Scrapling is unavailable or its fetch
fails, the module falls back to the existing requests + BS4 Screener chart
API (PBV / ROCE) plus yfinance only, and marks the result with a warning.

The module also owns the persistent cache (full_fundamental_cache table in
myra_valuation.db) used by the API router.
"""

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime

import requests

from myra_app.constants import DB_DIR

try:
    from scrapling.fetchers import StealthyFetcher

    StealthyFetcher.adaptive = True
    SCRAPLING_AVAILABLE = True
except ImportError:  # pragma: no cover - environment specific
    StealthyFetcher = None
    SCRAPLING_AVAILABLE = False

try:
    import yfinance as yf

    YFINANCE_AVAILABLE = True
except ImportError:  # pragma: no cover - environment specific
    yf = None
    YFINANCE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Polite delay (seconds) enforced between Scrapling page fetches.
SCRAPING_DELAY_SECONDS = 1.5
# Cap on the in-memory company-page cache.
PAGE_CACHE_MAX = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.screener.in/company/",
}

# Chart-API query names verified to return data (case-sensitive).
TIMESERIES_METRICS = {
    "price_to_book": "Price to book value",
    "pe": "Price to Earning",
    "roce": "Return on capital employed",
    "market_cap_to_sales": "Market Cap to Sales",
    "roe": "Return on equity",
}

YF_SUFFIX = ".NS"

CACHE_TABLE = "full_fundamental_cache"

# module-level page cache + throttle bookkeeping
_page_cache = {}
_last_fetch_ts = 0.0


def _throttle():
    """Sleep so that consecutive Scrapling fetches stay at least
    SCRAPING_DELAY_SECONDS apart."""
    global _last_fetch_ts
    elapsed = time.monotonic() - _last_fetch_ts
    if elapsed < SCRAPING_DELAY_SECONDS:
        time.sleep(SCRAPING_DELAY_SECONDS - elapsed)
    _last_fetch_ts = time.monotonic()


def _get_page(symbol: str):
    """Fetch (or reuse cached) the Screener.in consolidated company page
    for a symbol, returning the Scrapling response object (with .css support)
    or None on failure. Raises RuntimeError when Scrapling is unavailable."""
    if not SCRAPLING_AVAILABLE:
        raise RuntimeError(
            "Scrapling is not installed. Install it with: "
            "pip install scrapling playwright patchright"
        )
    symbol = symbol.strip().upper()
    if symbol in _page_cache:
        return _page_cache[symbol]
    _throttle()
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    try:
        resp = StealthyFetcher.fetch(
            url,
            headless=True,
            timeout=45000,
            network_idle=True,
            retries=2,
        )
    except Exception as e:
        logger.warning("Scrapling fetch failed for %s: %s", symbol, e)
        return None
    if resp is None or getattr(resp, "status", 0) != 200:
        logger.warning("Scrapling returned status %s for %s", getattr(resp, "status", None), symbol)
        return None
    _page_cache[symbol] = resp
    if len(_page_cache) > PAGE_CACHE_MAX:
        _page_cache.pop(next(iter(_page_cache)))
    return resp


# --------------------------------------------------------------------------- #
# Scrapling page parsing helpers
# --------------------------------------------------------------------------- #

def _text(node) -> str:
    if node is None:
        return ""
    if hasattr(node, "get_all_text"):
        return (node.get_all_text() or "").strip()
    return (node.text or "").strip()


def _first(nodes):
    """scrapling 0.4.x returns plain lists from .css(); pick the first match."""
    if not nodes:
        return None
    return nodes[0]


def _to_float(value) -> float:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _meta_description(page) -> str:
    """Extract the meta description (single-string fundamentals summary)."""
    tag = _first(page.css("meta[name='description']"))
    if tag is None:
        return ""
    return (tag.attrib.get("content") or "") if hasattr(tag, "attrib") else ""


def _parse_meta_market_cap(page) -> float:
    """Market cap in Crores from the meta description: 'Mkt Cap: 17,72,762 Crore'."""
    desc = _meta_description(page)
    m = re.search(r"Mkt Cap:\s*([0-9,]+)\s*Crore", desc)
    if m:
        return _to_float(m.group(1))
    return None


def _parse_top_ratios(page) -> dict:
    """Parse the #top-ratios list into a {label: float} dict."""
    metrics = {}
    for li in page.css("#top-ratios li"):
        name = _text(_first(li.css("span.name")))
        number = _text(_first(li.css("span.number")))
        if not name or not number:
            continue
        key = name.lower().replace(" ", "_").replace("/", "_").replace("%", "").replace(".", "")
        val = _to_float(number)
        if val is not None:
            metrics[key] = val
    return metrics


def _parse_shareholding(page) -> dict:
    """Parse #quarterly-shp table. Rows are holder groups, columns are
    quarters (last column = most recent quarter)."""
    out = {}
    rows = page.css("#quarterly-shp table.data-table tbody tr")
    for tr in rows:
        cells = tr.css("td")
        if not cells:
            continue
        label = _text(cells[0]).lower()
        if not label or len(cells) < 2:
            continue
        if label.startswith("promoter"):
            key = "promoters"
        elif label.startswith("fii"):
            key = "fii"
        elif label.startswith("dii"):
            key = "dii"
        elif label.startswith("govern"):
            key = "government"
        elif label.startswith("public"):
            key = "public"
        else:
            continue
        latest = _text(cells[-1])
        val = _to_float(latest)
        if val is not None:
            out[key] = val
    return out


def _parse_ratios_table(page) -> dict:
    """Parse the #ratios annual table into {row_label: {date: value}}."""
    out = {}
    table = _first(page.css("#ratios table.data-table"))
    if table is None:
        return out
    headers = [_to_float(th.attrib.get("data-date-key")) for th in table.css("th[data-date-key]")]
    dates = [th.attrib.get("data-date-key") for th in table.css("th[data-date-key]")]
    if not dates:
        return out
    for tr in table.css("tbody tr"):
        cells = tr.css("td")
        if len(cells) < 2:
            continue
        label = _text(cells[0]).strip()
        if not label:
            continue
        series = {}
        for i, td in enumerate(cells[1:]):
            if i >= len(dates):
                break
            val = _to_float(_text(td))
            if val is not None:
                series[dates[i]] = val
        if series:
            out[label] = series
    return out


# --------------------------------------------------------------------------- #
# Individual source fetchers
# --------------------------------------------------------------------------- #

def get_company_id(symbol: str) -> str:
    """Scrape the company page (via Scrapling) for the numeric company id."""
    page = _get_page(symbol)
    if page is None:
        return None
    body = page.body
    raw = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    m = re.search(r'data-company-id="(\d+)"', raw)
    if m:
        return m.group(1)
    m = re.search(r'company_id["\']?\s*[:=]\s*["\']?(\d+)', raw)
    if m:
        return m.group(1)
    return None


def _get_company_id_requests(symbol: str) -> str:
    """Fallback company-id lookup using plain requests + BS4."""
    try:
        from bs4 import BeautifulSoup

        url = f"https://www.screener.in/company/{symbol}/consolidated/"
        session = requests.Session()
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.find("div", {"data-company-id": True})
        if div:
            return div["data-company-id"]
        for script in soup.find_all("script"):
            if script.string and "company_id" in script.string:
                m = re.search(r'company_id["\']?\s*[:=]\s*["\']?(\d+)', script.string)
                if m:
                    return m.group(1)
    except Exception as e:
        logger.warning("Requests company-id lookup failed for %s: %s", symbol, e)
    return None


def fetch_screener_snapshot(symbol: str) -> dict:
    """Scrape the Screener.in company page and return a snapshot dict with
    company_id, market_cap_crore, key ratios and the latest shareholding."""
    page = _get_page(symbol)
    if page is None:
        return {}
    top = _parse_top_ratios(page)
    shareholding = _parse_shareholding(page)
    snapshot = {
        "company_id": None,
        "market_cap_crore": _parse_meta_market_cap(page),
        "dividend_yield": top.get("dividend_yield"),
        "face_value": top.get("face_value"),
        "roe": top.get("roe"),
        "roce": top.get("roce"),
        "pe": top.get("stock_p_e") or top.get("stock_pe") or top.get("pe"),
        "current_price": top.get("current_price"),
        "book_value": top.get("book_value"),
        "shareholding": shareholding,
    }
    snapshot["company_id"] = get_company_id(symbol)
    return snapshot

def fetch_screener_ratios(symbol: str) -> dict:
    """Scrape the #ratios annual table; returns {} when absent."""
    page = _get_page(symbol)
    if page is None:
        return {}
    return _parse_ratios_table(page)


def fetch_timeseries(company_id: str, metric: str) -> list:
    """Fetch a single metric time series from the Screener.in chart API.

    `metric` is a key of TIMESERIES_METRICS (e.g. "pe"). Returns a list of
    {"date": ..., "value": ...} dicts, or [] when unavailable.
    """
    query = TIMESERIES_METRICS.get(metric, metric)
    url = (
        f"https://www.screener.in/api/company/{company_id}/chart/"
        f"?q={query.replace(' ', '+')}&days=1825&consolidated=true"
    )
    session = requests.Session()
    session.get("https://www.screener.in/company/", headers=HEADERS, timeout=15)
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        datasets = data.get("datasets", [])
        if not datasets:
            return []
        values = datasets[0].get("values", [])
        series = []
        for item in values:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                series.append({"date": str(item[0]), "value": _to_float(item[1])})
            else:
                val = _to_float(item)
                if val is not None:
                    series.append({"date": "", "value": val})
        return [p for p in series if p["value"] is not None]
    except Exception as e:
        logger.debug("Chart API failed for %s/%s: %s", company_id, metric, e)
        return []


def fetch_yfinance_data(symbol: str) -> dict:
    """Fetch analyst recommendations, leverage, growth and company info from
    yfinance (using the .NS suffix). Returns {} on any failure."""
    if not YFINANCE_AVAILABLE:
        logger.warning("yfinance not installed; skipping analyst data")
        return {}
    try:
        ticker = yf.Ticker(symbol.strip().upper() + YF_SUFFIX)
        info = ticker.info or {}
        data = {}

        def g(*keys):
            for k in keys:
                v = info.get(k)
                if v is not None:
                    return v
            return None

        data["long_name"] = g("longName", "shortName")
        data["sector"] = g("sector")
        data["industry"] = g("industry")
        data["market_cap"] = g("marketCap")
        data["pe"] = g("trailingPE", "forwardPE")
        data["price_to_book"] = g("priceToBook")
        data["forward_pe"] = g("forwardPE")
        data["roe"] = g("returnOnEquity")
        data["roce"] = g("returnOnCapitalEmployed")
        data["dividend_yield"] = g("dividendYield", "trailingAnnualDividendYield")
        data["debt_to_equity"] = g("debtToEquity")
        data["beta"] = g("beta")
        data["revenue_growth"] = g("revenueGrowth")
        data["earnings_growth"] = g("earningsGrowth")
        data["recommendation_key"] = g("recommendationKey")
        data["recommendation_mean"] = g("recommendationMean")
        data["analyst_count"] = g("numberOfAnalystOpinions")
        data["target_mean_price"] = g("targetMeanPrice")
        data["current_price"] = g("currentPrice", "regularMarketPrice")
        data["total_debt"] = g("totalDebt")
        data["total_cash"] = g("totalCash")
        try:
            recs = ticker.recommendations
            if recs is not None and len(recs) > 0:
                latest = recs.iloc[-1]
                data["recommendation_latest"] = {
                    k: (None if v is None or (hasattr(v, "isna") and v.isna()) else v)
                    for k, v in latest.items()
                }
        except Exception:
            pass
        return data
    except Exception as e:
        logger.warning("yfinance fetch failed for %s: %s", symbol, e)
        return {}


# --------------------------------------------------------------------------- #
# Orchestrator + cache
# --------------------------------------------------------------------------- #

def _json_safe(value):
    """Recursively convert numpy/pandas/datetime values to JSON-safe types."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "tolist"):  # numpy arrays / pandas Series
        return _json_safe(value.tolist())
    if hasattr(value, "item"):  # numpy scalars
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):  # datetime / pandas Timestamp
        return value.isoformat()
    return str(value)


def fetch_full_fundamentals(symbol: str) -> dict:
    """Fetch deep fundamentals for a symbol from all sources, degrading
    gracefully. Returns a dict with snapshot / ratios / timeseries / yfinance
    plus a warning flag when Scrapling was unavailable."""
    symbol = symbol.strip().upper()
    result = {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "sources": [],
        "company_id": None,
        "snapshot": {},
        "ratios": {},
        "timeseries": {},
        "yfinance": {},
        "warning": None,
    }
    warnings = []

    company_id = None
    try:
        company_id = get_company_id(symbol)
    except Exception as e:
        warnings.append(f"Scrapling unavailable ({e})")

    if company_id:
        result["company_id"] = company_id
        try:
            snapshot = fetch_screener_snapshot(symbol)
            if snapshot:
                result["snapshot"] = snapshot
                result["sources"].append("screener-scrape")
        except Exception as e:
            warnings.append(f"screener scrape failed: {e}")
        try:
            ratios = fetch_screener_ratios(symbol)
            if ratios:
                result["ratios"] = ratios
        except Exception as e:
            warnings.append(f"ratios scrape failed: {e}")
        for key in TIMESERIES_METRICS:
            try:
                series = fetch_timeseries(company_id, key)
                if series:
                    result["timeseries"][key] = series
            except Exception as e:
                logger.debug("timeseries %s failed for %s: %s", key, symbol, e)
        if result["timeseries"]:
            result["sources"].append("screener-chart")
    else:
        # Fallback: chart API (PBV/ROCE) + yfinance only.
        cid = _get_company_id_requests(symbol)
        if cid:
            result["company_id"] = cid
            for key in ("price_to_book", "roce"):
                series = fetch_timeseries(cid, key)
                if series:
                    result["timeseries"][key] = series
            if result["timeseries"]:
                result["sources"].append("screener-chart")
        warnings.append("Scrapling failed — using chart API + yfinance fallback")

    ydata = fetch_yfinance_data(symbol)
    if ydata:
        result["yfinance"] = ydata
        result["sources"].append("yfinance")

    if warnings:
        result["warning"] = "; ".join(dict.fromkeys(warnings))
    return _json_safe(result)


def _cache_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def ensure_cache_table(conn: sqlite3.Connection):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (
            symbol TEXT PRIMARY KEY,
            data_json TEXT,
            source TEXT,
            last_updated TEXT
        )
        """
    )
    conn.commit()


def save_cache(symbol: str, data_json: str, source: str = "scrapling+yfinance"):
    try:
        conn = sqlite3.connect(_cache_path(), timeout=15)
        ensure_cache_table(conn)
        conn.execute(
            f"INSERT OR REPLACE INTO {CACHE_TABLE} (symbol, data_json, source, last_updated) VALUES (?, ?, ?, ?)",
            (symbol.upper(), data_json, source, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to save cache for %s: %s", symbol, e)


def load_cache(symbol: str):
    """Return the cached row (data dict + last_updated + source) or None."""
    try:
        conn = sqlite3.connect(_cache_path(), timeout=15)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT data_json, source, last_updated FROM {CACHE_TABLE} WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "data": json.loads(row["data_json"]),
            "source": row["source"],
            "last_updated": row["last_updated"],
        }
    except Exception as e:
        logger.warning("Failed to load cache for %s: %s", symbol, e)
        return None
