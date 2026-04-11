import pandas as pd
import requests
import io
import os
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

def update_isin_bridge():
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))

        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        df = pd.read_csv(io.StringIO(response.text))

        # Clean column names to handle any trailing spaces
        df.columns = df.columns.str.strip()

        # Extract required columns and rename ISIN NUMBER to ISIN
        mapping_df = df[['SYMBOL', 'ISIN NUMBER']].rename(columns={'ISIN NUMBER': 'ISIN'})

        # Save to parquet
        data_dir = os.path.join(os.getcwd(), 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        parquet_path = os.path.join(data_dir, 'isin_bridge.parquet')
        mapping_df.to_parquet(parquet_path, index=False)
        logger.info(f"Successfully updated ISIN bridge at {parquet_path}")
        return True

    except Exception as e:
        logger.warning(f"ISIN update failed, falling back to yesterday's cache. Error: {str(e)}")
        raise
