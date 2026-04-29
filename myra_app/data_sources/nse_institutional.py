"""
NSE Institutional Data Source
Fetches bulk deals, block deals, and short selling data directly from NSE APIs.
Uses same session/cookie pattern as DataFetcher for reliability.
"""
import requests, io, pandas as pd
from datetime import date, timedelta

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

def _nse_session():
    """Create a session with a valid NSE cookie."""
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    return session

def _fetch_deals_csv(session, option_type: str, from_date: date, to_date: date) -> pd.DataFrame:
    """Fetch bulk/block/short-selling deals from NSE API."""
    url = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
    params = {
        "optionType": option_type,  # bulk_deals, block_deals, short_selling
        "from": from_date.strftime("%d-%m-%Y"),
        "to": to_date.strftime("%d-%m-%Y"),
        "csv": "true",
    }
    resp = session.get(url, params=params, headers=NSE_HEADERS, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.BytesIO(resp.content))
    df.columns = [c.strip() for c in df.columns]
    return df

def fetch_institutional_data(from_date: date = None, to_date: date = None):
    """
    Fetch bulk and block deals from NSE for given date range.
    Defaults to last 7 days.
    Returns dict with 'bulk' and 'block' DataFrames.
    """
    if to_date is None:
        to_date = date.today()
    if from_date is None:
        from_date = to_date - timedelta(days=7)

    session = _nse_session()
    results = {}

    for option_type, label in [("bulk_deals", "BULK"), ("block_deals", "BLOCK")]:
        try:
            df = _fetch_deals_csv(session, option_type, from_date, to_date)
            df["deal_type"] = label
            results[label.lower()] = df
        except Exception as e:
            results[label.lower()] = pd.DataFrame()

    return results
