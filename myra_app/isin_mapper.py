import io
import logging
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from myra_app.constants import DATA_DIR

logger = logging.getLogger(__name__)


def update_isin_bridge():
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        session = requests.Session()
        retries = Retry(
            total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504]
        )
        session.mount("https://", HTTPAdapter(max_retries=retries))

        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        df = pd.read_csv(io.StringIO(response.text))

        # Clean column names to handle any trailing spaces
        df.columns = df.columns.str.strip()

        # Defensively find the ISIN column under any known name
        isin_candidates = ["ISIN NUMBER", "ISIN", "isin"]
        isin_col = None
        for col in isin_candidates:
            if col in df.columns:
                isin_col = col
                break

        if isin_col is None:
            logger.warning(
                "No ISIN column found in NSE equity CSV (looked for %s). "
                "Skipping ISIN rename.", isin_candidates
            )
            mapping_df = df[["SYMBOL"]].copy()
            mapping_df["ISIN"] = None
        else:
            mapping_df = df[["SYMBOL", isin_col]].rename(
                columns={isin_col: "ISIN"}
            )

        # Save to parquet
        data_dir = Path(DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)

        parquet_path = data_dir / "isin_bridge.parquet"
        mapping_df.to_parquet(parquet_path, index=False)
        logger.info(f"Successfully updated ISIN bridge at {parquet_path}")
        return True

    except RuntimeError as e:
        if "cannot schedule new futures" in str(e):
            logger.warning(
                "ISIN update skipped due to interpreter shutdown: %s", e
            )
            return False
        raise
    except Exception as e:
        logger.warning(
            f"ISIN update failed, falling back to yesterday's cache. Error: {str(e)}"
        )
        raise
