import io
import logging
import os
import re
from datetime import datetime

import numpy as np
import pandas as pd

from myra_app.schema_registry import SchemaRegistry

logger = logging.getLogger(__name__)


class BhavcopyParser:
    """
    MYRA v3.3 Bhavcopy Resilience Engine (PRIORITY 1)
    Handles flexible detection, dynamic mapping, and fault-tolerant ingestion.
    """

    @staticmethod
    def detect_file_format(filename: str) -> str:
        """Flexible File Detection (PRIORITY 1.1)"""
        filename = os.path.basename(filename).lower()
        if re.match(r"nse_full_\d{4}-\d{2}-\d{2}\.csv", filename):
            return "YYYY-MM-DD"
        elif re.match(r"nse_full_\d{8}\.csv", filename) or re.match(
            r"bhavcopy\d{8}\.csv", filename
        ):
            return "DDMMYYYY"
        return "UNKNOWN"

    @staticmethod
    def extract_date_from_filename(filename: str) -> str:
        fmt = BhavcopyParser.detect_file_format(filename)
        base = os.path.basename(filename)
        if fmt == "YYYY-MM-DD":
            match = re.search(r"(\d{4}-\d{2}-\d{2})", base)
            if match:
                return match.group(1)
        elif fmt == "DDMMYYYY":
            match = re.search(r"(\d{8})", base)
            if match:
                raw_date = match.group(1)
                try:
                    dt = datetime.strptime(raw_date, "%d%m%Y")
                    return dt.strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
                except:
                    pass
        return None

    @classmethod
    def parse_csv(cls, data, source_filename: str = None) -> tuple[pd.DataFrame, dict]:
        """
        Parses the Bhavcopy CSV, applies Dynamic Column Mapping, Data Cleaning, and Schema Validation.
        """
        report = {"rows_processed": 0, "rows_skipped": 0, "errors": []}

        if data is None or (isinstance(data, str) and not data.strip()):
            report["errors"].append("Data is empty or None")
            return pd.DataFrame(), report

        try:
            # Handle 'ragged' CSV files (v9.0 Resilience)
            # Differentiate filepath vs raw CSV string safely
            if isinstance(data, str):
                if "\n" in data:
                    # It's definitely raw CSV content
                    df = pd.read_csv(io.StringIO(data), on_bad_lines="skip")
                elif len(data) < 2048 and os.path.exists(data):
                    # It's a valid, short filepath
                    if os.path.getsize(data) == 0:
                        report["errors"].append(f"File {data} is empty.")
                        return pd.DataFrame(), report
                    df = pd.read_csv(data, on_bad_lines="skip")
                else:
                    report["errors"].append(
                        f"String data provided is neither valid CSV nor a valid filepath."
                    )
                    return pd.DataFrame(), report
            else:
                # Assume it's a file-like object or bytes
                df = pd.read_csv(data, on_bad_lines="skip")

            if df.empty:
                report["errors"].append(
                    "DataFrame is empty after load (possibly only headers)."
                )
                return pd.DataFrame(), report

            raw_count = len(df)
            report["rows_processed"] = raw_count

            # Clean headers and apply DYNAMIC COLUMN MAPPING (PRIORITY 1.2)
            df.columns = [
                SchemaRegistry.get_canonical_column(col) for col in df.columns
            ]

            # SCHEMA VALIDATION LAYER (PRIORITY 1.3)
            required_cols = SchemaRegistry.TABLES["technical_data"][  # noqa: PG-CHAINED
                "required_for_ingestion"
            ]
            missing_cols = [c for c in required_cols if c not in df.columns]

            if missing_cols:
                # Attempt recovery: Can we get date from filename?
                if "date" in missing_cols and source_filename:
                    extracted_date = cls.extract_date_from_filename(source_filename)
                    if extracted_date:
                        df["date"] = extracted_date
                        missing_cols.remove("date")
                        logger.warning(
                            f"Recovered missing 'date' column from filename {source_filename}."
                        )

                if missing_cols:
                    msg = f"CRITICAL: Missing required columns: {missing_cols}. Skipping batch."
                    logger.error(msg)
                    report["errors"].append(msg)
                    report["rows_skipped"] = raw_count
                    report["rows_processed"] = 0
                    return pd.DataFrame(), report

            # DATA CLEANING LAYER (PRIORITY 1.4)
            if "series" in df.columns:
                df["series"] = df["series"].astype(str).str.strip().str.upper()
                df = df[df["series"].isin(["EQ", "BE", "SM", "ST", "BZ"])]

            df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()

            try:
                # errors='coerce' to safely handle single corrupt strings without failing entire file
                df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime(  # noqa: PG-STRFTIME
                    "%Y-%m-%d"
                )
            except Exception as e:
                report["errors"].append(f"Date coercion failed: {e}")

            numeric_cols = [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "delivery_pct",
                "trades",
                "vwap",
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            df = df.dropna(subset=["symbol", "date", "close"])
            df = df.drop_duplicates(subset=["symbol", "date"], keep="last")

            if "delivery" in df.columns and "volume" in df.columns:
                mask = df["volume"] > 0
                if "delivery_pct" not in df.columns:
                    df.loc[mask, "delivery_pct"] = (
                        df.loc[mask, "delivery"] / df.loc[mask, "volume"]
                    ) * 100
                if "delivery_ratio" not in df.columns:
                    df.loc[mask, "delivery_ratio"] = (
                        df.loc[mask, "delivery"] / df.loc[mask, "volume"]
                    )

            report["rows_skipped"] = raw_count - len(df)

            return df, report

        except Exception as e:
            msg = f"Fatal parsing error: {e}"
            logger.error(msg)
            report["errors"].append(msg)
            report["rows_skipped"] = report["rows_processed"]
            report["rows_processed"] = 0
            return pd.DataFrame(), report
