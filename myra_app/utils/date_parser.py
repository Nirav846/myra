"""
MYRA Date Parser Utilities
Handles flexible date parsing for Bhavcopy CSVs with multiple format support.
"""

import re
from datetime import datetime
from typing import Optional


def parse_bhavcopy_date(raw_str: str) -> Optional[str]:
    """
    Parse various Bhavcopy date formats into ISO format (YYYY-MM-DD).

    Supported formats:
    - DD-Mon-YYYY (e.g. '10-Apr-2026')
    - DDMMYYYY (e.g. '10042026')
    - YYYY-MM-DD (already ISO, pass through)
    - DD/MM/YYYY (e.g. '10/04/2026')

    Args:
        raw_str: Raw date string from CSV

    Returns:
        ISO format date string (YYYY-MM-DD) or None on failure
    """
    if not raw_str or not isinstance(raw_str, str):
        return None

    raw_str = str(raw_str).strip()

    # Format 1: Already ISO (YYYY-MM-DD)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_str):
        return raw_str

    # Format 2: DD-Mon-YYYY (e.g. '10-Apr-2026')
    if re.match(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$", raw_str, re.IGNORECASE):
        try:
            dt = datetime.strptime(raw_str, "%d-%b-%Y")
            return dt.strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
        except ValueError:
            pass

    # Format 3: DDMMYYYY (e.g. '10042026')
    if re.match(r"^\d{8}$", raw_str):
        try:
            dt = datetime.strptime(raw_str, "%d%m%Y")
            return dt.strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
        except ValueError:
            pass

    # Format 4: DD/MM/YYYY (e.g. '10/04/2026')
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", raw_str):
        try:
            dt = datetime.strptime(raw_str, "%d/%m/%Y")
            return dt.strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
        except ValueError:
            pass

    return None
