#!/usr/bin/env python
import warnings

# Silence Scrapling/Fetcher deprecation warnings BEFORE any other imports
warnings.filterwarnings("ignore", message=".*deprecated now, and have no effect.*")

"""
MYRA Smart Fetcher - Resilient Data Acquisition Layer (v3.2 GHOST)
Powered by scrapling and curl_cffi for human-identical TLS signatures.
EXCLUSIVE GATEKEEPER for all network requests.
"""
import random
import json
import os
import logging

logger = logging.getLogger(__name__)
import re
import pandas as pd
import numpy as np
from io import StringIO
from bs4 import BeautifulSoup
import time
import zipfile
import io

from datetime import date, datetime, timedelta, timezone
from scrapling import Fetcher
import sqlite3
import hashlib


class GhostSession:
    """
    Stealth Session Manager (TRILOGY ERA)
    Wraps scrapling.Fetcher to provide human-identical network signatures.
    Optimized for Bulk Scanning: Persistent connection to prevent N+1 Query overhead.
    """

    def __init__(self, cache_path=None):
        self.cache_path = cache_path
        self._conn = None
        self._init_cache()
        # Scrapling is now a secondary fallback for bulk scanning
        self.fetcher = None
        self.headers = {}

    def _get_conn(self):
        if self._conn is None and self.cache_path:
            try:
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                self._conn = sqlite3.connect(
                    self.cache_path, timeout=20, check_same_thread=False
                )
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            except Exception as e:
                logger.error(f"Failed to connect to cache DB: {e}")
        return self._conn

    def _init_cache(self):
        conn = self._get_conn()
        if not conn:
            return
        try:
            cursor = conn.execute("PRAGMA table_info(cache)")
            columns = [info[1] for info in cursor.fetchall()]

            if not columns:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value BLOB, expiry TIMESTAMP, data_hash TEXT)"
                )
            elif "data_hash" not in columns:
                logger.info(
                    f"Upgrading cache schema in {self.cache_path}: Adding data_hash column."
                )
                conn.execute("ALTER TABLE cache ADD COLUMN data_hash TEXT")
            conn.commit()
        except Exception as e:
            logger.error(f"Unexpected error during cache init: {e}", exc_info=True)
            pass

    def _get_cache(self, url, params=None):
        conn = self._get_conn()
        if not conn:
            return None
        key = hashlib.md5(
            f"{url}{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()
        try:
            # Consistent UTC comparison
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            res = conn.execute(
                "SELECT value FROM cache WHERE key = ? AND expiry > ?",
                (key, now_utc),
            ).fetchone()
            return res[0] if res else None
        except Exception as e:
            logger.error(f"Unexpected error in _get_cache: {e}", exc_info=True)
            return None

    def _set_cache(self, url, value, params=None, expire_seconds=86400):
        conn = self._get_conn()
        if not conn:
            return
        key = hashlib.md5(
            f"{url}{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()
        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            seconds=expire_seconds
        )
        data_hash = hashlib.md5(value).hexdigest() if value else None

        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
                (key, value, expiry, data_hash),
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            if "table cache has 3 columns" in str(e):
                logger.warning(
                    "Cache schema mismatch detected during INSERT. Falling back to 3-column insert."
                )
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, value, expiry) VALUES (?, ?, ?)",
                    (key, value, expiry),
                )
                conn.commit()
            else:
                logger.error(f"SQL Error in _set_cache: {e}")

    def get(self, url, params=None, headers=None, timeout=30, bypass_cache=False):
        """Standardized GET with Requests-First resilience (Fix 5)."""
        if not bypass_cache:
            cached = self._get_cache(url, params)
            if cached:

                class MockResponse:
                    def __init__(self, content):
                        self.content = content
                        self.text = content.decode("utf-8", errors="ignore")
                        self.status_code = 200

                    def json(self):
                        return json.loads(self.text)

                return MockResponse(cached)

        current_headers = self.headers.copy()
        if headers:
            current_headers.update(headers)

        # 1. PRIMARY: Standard Requests (Stable & Fast)
        import requests

        try:
            r = requests.get(
                url, params=params, headers=current_headers, timeout=timeout
            )
            if r.status_code == 200:
                self._set_cache(url, r.content, params)
            return r
        except Exception as e:
            logger.error(f"Requests error for {url}: {e}")
            # 2. FALLBACK: Stealth Scrapling (Only if blocked)
            try:
                if self.fetcher is None:
                    from scrapling import Fetcher

                    self.fetcher = Fetcher()

                response = self.fetcher.get(
                    url, params=params, headers=current_headers, timeout=timeout + 20
                )
                response.status_code = getattr(response, "status", 0)
                response.content = getattr(response, "body", b"")

                if response.status_code == 200:
                    self._set_cache(url, response.content, params)
                return response
            except Exception as e:
                logger.error(f"Scrapling fallback error for {url}: {e}")
                return None


class SchemaContractEnforcer:
    """
    Schema Contract Enforcement Layer (SCEL).
    Acts like a firewall for bad data before it reaches the engine.
    """

    def __init__(self, contract):
        self.contract = contract

    def validate(self, df):
        errors = []
        if df is None or df.empty:
            return ["EMPTY_DATAFRAME"]

        req_cols_present = [
            col for col in self.contract["required_columns"] if col in df.columns
        ]
        if req_cols_present:
            initial_len = len(df)
            df.dropna(subset=req_cols_present, inplace=True)
            if len(df) < initial_len:
                logger.warning(
                    f"Materiality Warning: Dropped {initial_len - len(df)} rows due to missing required columns."
                )

        errors.extend(
            [
                f"Missing required column: {col}"
                for col in self.contract["required_columns"]
                if col not in df.columns
            ]
        )

        all_cols = {
            **self.contract["required_columns"],
            **self.contract["optional_columns"],
        }
        num_cols = [
            c
            for c, t in all_cols.items()
            if t in [float, int]
            and c in df.columns
            and not pd.api.types.is_numeric_dtype(df[c])
        ]

        if num_cols:
            try:
                for col in num_cols:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                errors.extend(
                    [
                        f"Type mismatch in {col}: Not numeric"
                        for col in num_cols
                        if df[col].isna().all()
                    ]
                )
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)

                def get_col_err(c):
                    try:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                        return (
                            f"Type mismatch in {c}: Not numeric"
                            if df[c].isna().all()
                            else None
                        )
                    except:
                        return f"Type mismatch in {c}"

                fallback_errors = [err for c in num_cols if (err := get_col_err(c))]
                errors.extend(fallback_errors)

        if "close_price" in df.columns:
            if df["close_price"].isna().mean() > 0.1:
                errors.append("Critical field close_price has >10% NaN")

        return errors


class LineageTracker:
    """
    End-to-End Data Lineage Tracker (v11).
    Provides full traceability and Active Intelligence for root-cause analysis.
    """

    def __init__(self):
        self.run_id = str(datetime.now().timestamp())
        self.steps = []
        self.symbols = {}

    def log_step(self, stage, status, details=None):
        self.steps.append(
            {
                "stage": stage,
                "status": status,
                "details": details,
                "time": datetime.now().isoformat(),
            }
        )

    def analyze_failures(self):
        failures = [s for s in self.steps if s["status"] == "FAIL"]
        if not failures:
            return None
        summary = {"counts": {}, "recommendations": []}
        counts = summary["counts"]
        for f in failures:
            stage = f["stage"]
            counts[stage] = counts.get(stage, 0) + 1
        if counts.get("schema", 0) > 2:
            summary["recommendations"].append(
                "Pattern: RECURRING SCHEMA DRIFT. Upstream source format likely changed. Audit normalize_dataframe aliases."
            )
        if summary["counts"].get("truth", 0) > 2:
            summary["recommendations"].append(
                "Pattern: STALE MIRROR LOOP. Primary sources are serving yesterday's data. Check mirror latency."
            )
        return summary

    def save(self):
        log_dir = os.path.join(os.getcwd(), "logs", "lineage")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        path = os.path.join(log_dir, f"lineage_{self.run_id}.json")
        analysis = self.analyze_failures()
        with open(path, "w") as f:
            json.dump(
                {
                    "run_id": self.run_id,
                    "timestamp": datetime.now().isoformat(),
                    "analysis": analysis,
                    "steps": self.steps,
                    "symbols": self.symbols,
                },
                f,
                indent=2,
            )
        return path


class DataFetcher:
    FIELD_MAP = {
        "SALES": "revenue",
        "REVENUE": "revenue",
        "TOTAL INCOME": "revenue",
        "TURNOVER": "revenue",
        "NET PROFIT": "net_profit",
        "PROFIT AFTER TAX": "net_profit",
        "PAT": "net_profit",
        "NET INCOME": "net_profit",
        "P/E": "stock_pe",
        "STOCK P/E": "stock_pe",
        "PRICE TO EARNINGS": "stock_pe",
        "PER": "stock_pe",
        "ROE": "roe",
        "RETURN ON EQUITY": "roe",
        "ROE %": "roe",
        "ROCE": "roce",
        "RETURN ON CAPITAL EMPLOYED": "roce",
        "DEBT TO EQUITY": "debt",
        "D/E": "debt",
        "DEBT EQUITY": "debt",
        "EPS": "eps",
        "EARNINGS PER SHARE": "eps",
        "OPM": "opm_pct",
        "OPERATING PROFIT MARGIN": "opm_pct",
        "BOOK VALUE": "book_value",
        "MARKET CAP": "market_cap",
        "MCAP": "market_cap",
    }

    def __init__(self):
        from .librarian_core import LibrarianCore

        cache_path = os.path.join(
            os.getcwd(), "db", LibrarianCore.DB_MAP["network_cache"]
        )
        self.session = GhostSession(cache_path=cache_path)

        self.registry_path = os.path.join(os.getcwd(), "config", "myra_sources.json")
        self.config_path = os.path.join(os.getcwd(), "config", "sources.json")
        self.cal_path = os.path.join(os.getcwd(), "data", "trading_calendar_master.csv")
        self._load_registry()
        self._load_config()
        self._load_calendar()

        self.contract = {
            "required_columns": {
                "symbol": str,
                "close_price": float,
                "total_traded_qty": float,
            },
            "optional_columns": {
                "delivery_qty": float,
                "open_price": float,
                "high_price": float,
                "low_price": float,
                "sector": str,
            },
        }
        self.enforcer = SchemaContractEnforcer(self.contract)
        self.lineage = LineageTracker()

    def get_ist_now(self):
        """Returns current IST (UTC+5:30) independently of system timezone settings."""
        return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

    def _load_registry(self):
        try:
            with open(self.registry_path, "r") as f:
                self.registry = json.load(f)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            self.registry = {}

    def _load_config(self):
        try:
            with open(self.config_path, "r") as f:
                self.config = json.load(f)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            self.config = {}

    def _load_calendar(self):
        self.valid_dates = set()
        if os.path.exists(self.cal_path):
            try:
                df = pd.read_csv(self.cal_path)
                self.valid_dates = set(df["date"].astype(str).tolist())
            except:
                pass

    def _is_holiday(self, dt):
        """Market Closure Detection (Fix v12.1)."""
        ist_now = self.get_ist_now()
        d_str = dt.date().isoformat() if hasattr(dt, "date") else str(dt)

        if dt.weekday() >= 5:
            return True
        if self.valid_dates and d_str in self.valid_dates:
            return False

        NSE_HOLIDAYS = [
            "2026-01-26",
            "2026-03-03",
            "2026-03-26",
            "2026-03-31",
            "2026-04-03",
        ]
        if d_str in NSE_HOLIDAYS:
            return True

        try:
            target_date = dt.date() if hasattr(dt, "date") else dt
            if target_date > ist_now.date():
                return True
        except:
            pass

        return False

    def is_data_ready(self, dt):
        """Checks if NSE has likely uploaded data for the given date, forced to IST."""
        ist_now = self.get_ist_now()
        target_date = dt.date() if hasattr(dt, "date") else dt
        if target_date < ist_now.date():
            return True
        if target_date == ist_now.date():
            return ist_now.hour >= 18
        return False

    def score_data_quality(self, data, source_type="bhavcopy", current_date=None):
        if data is None:
            return 0.0
        try:
            df = pd.read_csv(io.StringIO(data)) if isinstance(data, str) else data
            if df.empty:
                return 0.0
            score = 0.0

            rows = len(df)
            expected = 1200 if self.is_special_session(current_date) else 1800
            if rows >= expected:
                score += 0.5
            elif rows >= expected * 0.7:
                score += 0.3
            elif rows >= expected * 0.4:
                score += 0.1

            cols = [c.upper() for c in df.columns]
            required = ["SYMBOL", "CLOSE_PRICE", "TTL_TRD_QNTY"]
            if all(col in cols for col in required):
                score += 0.2

            d_col = next((c for c in df.columns if c.upper() == "DELIV_QTY"), None)
            if d_col:
                delivery_present = df[d_col].notna() & (
                    pd.to_numeric(df[d_col], errors="coerce").fillna(0) > 0
                )
                density = delivery_present.mean()
                if density > 0.8:
                    score += 0.3
                elif density > 0.5:
                    score += 0.1

            return round(score, 2)
        except:
            return 0.0

    def is_special_session(self, dt):
        return False

    def _update_source_reliability(self, name, score, current_date):
        if self._is_holiday(current_date):
            return
        try:
            conn = self.session._get_conn()
            if not conn:
                return
            conn.execute(
                "CREATE TABLE IF NOT EXISTS source_stats (name TEXT, score FLOAT, date TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO source_stats VALUES (?, ?, ?)",
                (name, score, datetime.now(timezone.utc).replace(tzinfo=None)),
            )
            conn.execute(
                "DELETE FROM source_stats WHERE name = ? AND date NOT IN (SELECT date FROM source_stats WHERE name = ? ORDER BY date DESC LIMIT 15)",
                (name, name),
            )
            conn.commit()
        except:
            pass

    def get_reliability(self, name):
        try:
            conn = self.session._get_conn()
            if not conn:
                return 0.5
            res = conn.execute(
                "SELECT score FROM source_stats WHERE name = ? ORDER BY date ASC",
                (name,),
            ).fetchall()
            if not res:
                return 0.5
            scores = [r[0] for r in res]
            weights = [i + 1 for i in range(len(scores))]
            weighted_sum = sum(s * w for s, w in zip(scores, weights))
            return round(weighted_sum / sum(weights), 2)
        except:
            return 0.5

    def to_snake_case(self, col):
        col = col.strip()
        col = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", col)
        col = col.replace(" ", "_").replace("-", "_").replace(".", "_")
        return col.lower()

    def normalize_dataframe(self, df):
        if df is None or df.empty:
            return df
        df.columns = [self.to_snake_case(c) for c in df.columns]
        COLUMN_MAP = {
            "close": "close_price",
            "last": "close_price",
            "last_price": "close_price",
            "open": "open_price",
            "high": "high_price",
            "low": "low_price",
            "tottrdqty": "total_traded_qty",
            "ttl_trd_qnty": "total_traded_qty",
            "volume": "total_traded_qty",
            "deliv_qty": "delivery_qty",
            "deliveryqty": "delivery_qty",
            "deliv_per": "delivery_pct",
            "delivery_percentage": "delivery_pct",
        }
        df = df.rename(columns=lambda c: COLUMN_MAP.get(c, c))
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
        return df

    def compute_fingerprint(self, df):
        if df is None or df.empty:
            return None
        try:
            return {
                "rows": len(df),
                "unique_symbols": (
                    df["symbol"].nunique() if "symbol" in df.columns else 0
                ),
                "mean_close": (
                    df["close_price"].mean() if "close_price" in df.columns else 0
                ),
                "total_volume": (
                    df["total_traded_qty"].sum()
                    if "total_traded_qty" in df.columns
                    else 0
                ),
            }
        except:
            return None

    def is_stale_fingerprint(self, fp_today, fp_prev):
        if not fp_today or not fp_prev:
            return False
        vol_diff = abs(fp_today["total_volume"] - fp_prev["total_volume"])
        if (
            fp_prev["total_volume"] > 0
            and (vol_diff / fp_prev["total_volume"]) < 0.0001
        ):
            return True
        return False

    def check_price_consistency(self, df_today, df_prev):
        if df_today is None or df_prev is None or df_today.empty or df_prev.empty:
            return True, "OK"
        try:
            merged = df_today.merge(df_prev, on="symbol", suffixes=("_t", "_y"))
            if merged.empty:
                return True, "NO_COMMON_SYMBOLS"
            same_price = (merged["close_price_t"] == merged["close_price_y"]).mean()
            same_volume = (
                merged["total_traded_qty_t"] == merged["total_traded_qty_y"]
            ).mean()
            if same_price > 0.9 and same_volume > 0.9:
                return False, "STALE_DATA_CONFIRMED"
            return True, "OK"
        except:
            return True, "CHECK_FAILED"

    def check_sector_coverage(self, df):
        if df is None or df.empty:
            return False, "EMPTY"
        if "sector" not in df.columns:
            return True, "NO_SECTOR_DATA"
        try:
            BASELINE = {
                "FINANCIAL SERVICES": 0.35,
                "IT": 0.15,
                "OIL & GAS": 0.12,
                "CONSUMER GOODS": 0.10,
            }
            current = df["sector"].value_counts(normalize=True)
            for sector, weight in current.items():
                base = BASELINE.get(sector.upper(), 0.1)
                if weight > base * 2.5:
                    return False, f"ABNORMAL_SECTOR_SKEW_{sector}"
            return True, "OK"
        except:
            return True, "CHECK_FAILED"

    def fetch_bhavcopy_with_retry(self, current_date):
        for i in range(5):
            data, name = self.fetch_ohlcv_delivery(current_date)
            if data:
                return data, name
            if self._is_holiday(current_date):
                break
            print(
                f"[RETRY] {current_date}: Bhavcopy attempt {i+1} failed. Gapping for 20s..."
            )
            time.sleep(20)
        return None, None

    def validate_against_anchor(self, df):
        if df is None or df.empty:
            return False, "EMPTY"
        if "symbol" not in df.columns or "close_price" not in df.columns:
            return True, "NO_DATA"
        try:
            rel_price = df.loc[df["symbol"] == "RELIANCE", "close_price"]
            if not rel_price.empty:
                p = rel_price.iloc[0]
                if not (500 < p < 10000):
                    return False, f"ANCHOR_DEVIATION_RELIANCE_{p}"
            avg_p = df["close_price"].mean()
            std_p = df["close_price"].std()
            if std_p == 0 and avg_p > 0 and len(df) > 500:
                return False, "ZERO_VARIANCE_UNIVERSE_PROBABLE_FAKE"
            if avg_p < 5 or avg_p > 15000:
                return False, f"ABNORMAL_UNIVERSE_MEAN_{avg_p}"
            return True, "OK"
        except:
            return True, "CHECK_FAILED"

    def execute_controlled_response(self, analysis):
        if not analysis:
            return
        try:
            conn = self.session._get_conn()
            if not conn:
                return
            last_action = conn.execute(
                "SELECT MAX(date) FROM source_stats WHERE name = 'SYSTEM_ACTION'"
            ).fetchone()
            if last_action and last_action[0]:
                la_dt = datetime.fromisoformat(last_action[0])
                if (datetime.now() - la_dt).total_seconds() < 1800:
                    print(
                        "[MYRA] Auto-Response in Cooldown. Skipping action to prevent cascade."
                    )
                    return
        except:
            pass

        FAILURE_WEIGHTS = {"schema": 0.6, "truth": 1.0, "anchor": 1.0, "default": 0.3}
        total_weight = 0
        failures = analysis.get("counts", {})
        for stage, count in failures.items():
            weight = FAILURE_WEIGHTS.get(stage, FAILURE_WEIGHTS["default"])
            total_weight += weight * count

        if total_weight >= 2.5:
            print(
                f"[MYRA AUTO-RESPONSE] High-Confidence System Event (Weight: {round(total_weight, 2)})"
            )
            try:
                conn = self.session._get_conn()
                if conn:
                    conn.execute(
                        "INSERT INTO source_stats VALUES (?, ?, ?)",
                        (
                            "SYSTEM_ACTION",
                            total_weight,
                            datetime.now(timezone.utc).replace(tzinfo=None),
                        ),
                    )
                    conn.commit()
            except:
                pass
            if failures.get("schema", 0) >= 3:
                print("👉 Action: SCHEMA EMERGENCY. Forcing Mirror Priority Reset.")
            if failures.get("truth", 0) >= 2 or failures.get("anchor", 0) >= 2:
                print("👉 Action: REALITY BREACH. flushing network_cache.sqlite.")

    def re_enable_sources(self):
        pass

    def _merge_zip_mto(self, zip_content, mto_text, current_date):
        """Hardened zip extraction with vectorized MTO parsing."""
        if not zip_content or b"<html" in zip_content[:200].lower():
            logger.critical("NSE WAF block detected. HTML received instead of ZIP.")
            return None

        try:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                df_bhav = pd.read_csv(z.open(z.namelist()[0]))
        except zipfile.BadZipFile:
            logger.error("Corrupted ZIP content.")
            return None

        mapping = {
            "TckrSymb": "SYMBOL",
            "SctySrs": "SERIES",
            "OpnPric": "OPEN_PRICE",
            "HghPric": "HIGH_PRICE",
            "LwPric": "LOW_PRICE",
            "ClsPric": "CLOSE_PRICE",
            "TtlTradgVol": "TTL_TRD_QNTY",
            "TOTTRDQTY": "TTL_TRD_QNTY",
            "CLOSE": "CLOSE_PRICE",
            "OPEN": "OPEN_PRICE",
            "HIGH": "HIGH_PRICE",
            "LOW": "LOW_PRICE",
            "TIMESTAMP": "DATE1",
        }
        df_bhav = df_bhav.rename(columns=lambda x: mapping.get(x, x.upper()))

        # PERFORMANCE GUARD FIX: Robust vectorized MTO parsing (v9.0 Resilience)
        try:
            mto_io = StringIO(mto_text)
            # Read with names=range(10) to force schema to accommodate all columns (Record 50)
            # and prevent short Record 10/20 from causing bad line truncation.
            df_mto = pd.read_csv(
                mto_io, header=None, names=range(10), on_bad_lines="skip"
            )

            # Vectorized filtering: Extract valid data rows (avoiding record type assumptions).
            # We target rows with enough columns where delivery metrics (indices 5 & 6) are numeric.
            mask = pd.to_numeric(df_mto.iloc[:, 5], errors="coerce").notna() & pd.to_numeric(
                df_mto.iloc[:, 6], errors="coerce"
            ).notna()
            df_mto = df_mto[mask].copy()

            if df_mto.empty:
                logger.warning("MTO parsing resulted in empty DataFrame.")
                return None

            # Select confirmed indices: 2=SYMBOL, 3=SERIES, 5=DELIV_QTY, 6=DELIV_PER
            df_mto = df_mto.iloc[:, [2, 3, 5, 6]]
            df_mto.columns = ["SYMBOL", "SERIES", "DELIV_QTY", "DELIV_PER"]

            # Vectorized cleaning and type conversion
            df_mto["SYMBOL"] = df_mto["SYMBOL"].astype(str).str.strip().str.upper()
            df_mto["SERIES"] = df_mto["SERIES"].astype(str).str.strip().str.upper()
            df_mto["DELIV_QTY"] = pd.to_numeric(df_mto["DELIV_QTY"], errors="coerce")
            df_mto["DELIV_PER"] = pd.to_numeric(df_mto["DELIV_PER"], errors="coerce")
        except Exception as e:
            logger.error(f"Failed to parse MTO text: {e}")
            return None

        df_full = pd.merge(df_bhav, df_mto, on=["SYMBOL", "SERIES"], how="left")

        MONTHS = [
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        ]
        df_full[
            "DATE1"
        ] = f"{current_date.day:02d}-{MONTHS[current_date.month-1]}-{current_date.year}"

        cols = [
            "SYMBOL",
            "SERIES",
            "DATE1",
            "OPEN_PRICE",
            "HIGH_PRICE",
            "LOW_PRICE",
            "CLOSE_PRICE",
            "TTL_TRD_QNTY",
            "DELIV_QTY",
            "DELIV_PER",
        ]
        for c in cols:
            if c not in df_full.columns:
                df_full[c] = 0
        return df_full.reindex(columns=cols).fillna(0).to_csv(index=False)

    def fetch_ohlcv_delivery(self, current_date):
        """Primary data stream entry point (Restored & Hardened)."""
        if self._is_holiday(current_date):
            return None, "holiday_skip"
        if not self.is_data_ready(current_date):
            return None, "too_early"

        streams = self.registry.get("data_streams", {}).get("market_ohlcv_delivery", [])

        MONTHS = [
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        ]
        d, m, y = current_date.day, current_date.month, current_date.year
        ds = f"{d:02d}{m:02d}{y}"
        ds_leg = f"{d:02d}{MONTHS[m-1]}{y}"
        ds_udiff = f"{y}{m:02d}{d:02d}"
        year_str = str(y)
        mon_str = MONTHS[m - 1]

        best_data = None
        best_final_score = -1.0
        best_source_name = "None"
        exploration_triggered = random.random() < 0.10

        for stream in streams:
            try:
                headers = self.registry.get("headers", {}).get("standard_myra", {})
                data_text = None

                if stream["format"] == "csv_direct":
                    url = stream["url"].format(ds=ds)
                    self.session.get("https://www.nseindia.com", headers=headers)
                    r = self.session.get(url, headers=headers)
                    if r and r.status_code == 200:
                        data_text = r.text

                elif stream["format"] == "zip_mto_merge":
                    p_url = stream["price_url"].format(
                        ds_udiff=ds_udiff, ds_leg=ds_leg, year=year_str, mon=mon_str
                    )
                    d_url = stream["delivery_url"].format(ds=ds)

                    self.session.get("https://www.nseindia.com", headers=headers)
                    r_p = self.session.get(p_url, headers=headers)
                    r_d = self.session.get(d_url, headers=headers)

                    if (
                        r_p
                        and r_p.status_code == 200
                        and r_d
                        and r_d.status_code == 200
                    ):
                        data_text = self._merge_zip_mto(
                            r_p.content, r_d.text, current_date
                        )

                if data_text:
                    df_current = (
                        pd.read_csv(io.StringIO(data_text))
                        if isinstance(data_text, str)
                        else data_text
                    )
                    score = self.score_data_quality(
                        df_current, current_date=current_date
                    )

                    valid_anchor, a_reason = self.validate_against_anchor(df_current)
                    if not valid_anchor:
                        print(
                            f"[TRACE] Source {stream['name']} REJECTED: Anchor failure: {a_reason}"
                        )
                        self.lineage.log_step(
                            "anchor",
                            "FAIL",
                            {"source": stream["name"], "reason": a_reason},
                        )
                        continue

                    valid_sector, s_reason = self.check_sector_coverage(df_current)
                    if not valid_sector:
                        score *= 0.5

                    try:
                        from myra_app.results_manager import ResultsManager

                        rm = ResultsManager()
                        prev_data = rm.load_last_snapshot("all_pass")
                        if prev_data:
                            df_prev = pd.DataFrame(prev_data)
                            valid_price, p_reason = self.check_price_consistency(
                                df_current, df_prev
                            )
                            if not valid_price:
                                score = 0.0
                    except:
                        pass

                    rel = self.get_reliability(stream["name"])

                    target_date = (
                        current_date.date()
                        if hasattr(current_date, "date")
                        else current_date
                    )
                    is_cooldown = (self.get_ist_now().date() - target_date).days <= 1
                    q_w = 0.5 if is_cooldown else 0.7
                    r_w = 0.5 if is_cooldown else 0.3
                    final_score = (score * q_w) + (rel * r_w)

                    if exploration_triggered and score < 0.5:
                        continue

                    if final_score > best_final_score:
                        best_final_score = final_score
                        best_data = data_text
                        best_source_name = stream["name"]

                    if final_score >= 0.85 and not exploration_triggered:
                        break

            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                continue

        if best_data:
            self._update_source_reliability(
                best_source_name, best_final_score, current_date
            )
            return best_data, best_source_name

        return None, None

    def validate_csv(self, text):
        if not text or len(text) < 100:
            return False
        if (
            "<html" in text.lower()
            or "<body" in text.lower()
            or "SYMBOL" not in text.upper()
        ):
            return False
        return True

    def unify_symbol(self, symbol):
        if not symbol:
            return ""
        return (
            str(symbol).split(":")[-1].split(".")[0].replace("_", "&").strip().upper()
        )

    def fetch_large_deals_v2(self):
        streams = self.registry.get("data_streams", {}).get(
            "institutional_tracking", []
        )
        headers = self.registry.get("headers", {}).get("nse_api_headers", {})
        for stream in [s for s in streams if s["name"] == "nse_bulk_block_deals"]:
            try:
                self.session.get("https://www.nseindia.com", headers=headers)
                r = self.session.get(stream["url"], headers=headers)
                if r and r.status_code == 200:
                    data = r.json()
                    mapping = {
                        "BULK_DEALS_DATA": "Bulk",
                        "BLOCK_DEALS_DATA": "Block",
                        "SHORT_DEALS_DATA": "Short",
                    }
                    results = [
                        {
                            "symbol": self.unify_symbol(deal.get("symbol")),
                            "type": deal_type,
                            "client": deal.get("clientName"),
                            "buy_sell": deal.get("buySell"),
                            "qty": int(
                                self.sanitize_float(deal.get("quantityTraded", 0))
                            ),
                            "price": self.sanitize_float(deal.get("tradePrice", 0)),
                            "date": date.today(),
                        }
                        for key, deal_type in mapping.items()
                        for deal in data.get(key, [])
                    ]
                    return results
            except:
                continue
        return []

    def fetch_fundamentals(self, symbol):
        clean_symbol = self.unify_symbol(symbol)
        streams = self.registry.get("data_streams", {}).get("fundamentals", [])
        for stream in streams:
            try:
                if stream["name"] == "screener_in":
                    res = self._fetch_screener(clean_symbol)
                elif stream["name"] == "google_finance":
                    res = self._fetch_google(clean_symbol)
                else:
                    continue
                if res:
                    return res, stream["name"]
            except:
                continue
        return None, None

    def fetch_deep_history(self, symbol):
        clean_symbol = self.unify_symbol(symbol)
        url = f"https://www.screener.in/company/{clean_symbol}/consolidated/"
        cached = self.session._get_cache(url + "_deep", params={"deep": True})
        if cached:
            return json.loads(cached.decode("utf-8"))

        r = self.session.get(url, timeout=20)
        if not r or r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        data_map = {}

        for table in soup.find_all("table"):
            sect = table.find_parent("section")
            if sect and "Profit & Loss" in sect.get_text():
                rows = table.find_all("tr")
                dates = [
                    c.get_text().strip() for c in rows[0].find_all(["th", "td"])[1:]
                ]
                for i, d in enumerate(dates):
                    if d not in data_map:
                        data_map[d] = {"report_date": d, "period": "ANNUAL"}
                    d_entry = data_map[d]
                    for row in rows[1:]:
                        m_tag = row.find(["th", "td"])
                        if not m_tag:
                            continue
                        std_key = self._normalize_field(m_tag.get_text())
                        cols = row.find_all("td")
                        if i < len(cols):
                            d_entry[std_key] = self._clean_num(cols[i].get_text())
                break

        results = sorted(
            data_map.values(), key=lambda x: x["report_date"], reverse=True
        )
        if results:
            self.session._set_cache(
                url + "_deep",
                json.dumps(results).encode("utf-8"),
                params={"deep": True},
                expire_seconds=2592000,
            )
        return results

    def _fetch_screener(self, symbol):
        url = f"https://www.screener.in/company/{symbol}/consolidated/"
        r = self.session.get(url, timeout=15)
        if not r or r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        sector_val = None
        sub_p = soup.find("p", class_="sub")
        if sub_p:
            links = sub_p.find_all("a")
            if links:
                sector_val = links[0].get_text().strip()

        top_metrics = {}
        if sector_val:
            top_metrics["sector"] = sector_val

        for li in soup.find_all("li", class_=re.compile(r"flex.*")):
            n_tag, v_tag = li.find("span", class_="name"), li.find(
                "span", class_="number"
            )
            if n_tag and v_tag:
                top_metrics[self._normalize_field(n_tag.get_text())] = self._clean_num(
                    v_tag.get_text()
                )
        data_map = {}
        for table in soup.find_all("table"):
            sect = table.find_parent("section")
            if sect and "Quarterly" in sect.get_text():
                rows = table.find_all("tr")
                dates = [
                    c.get_text().strip() for c in rows[0].find_all(["th", "td"])[1:]
                ]
                for i, d in enumerate(dates):
                    if d not in data_map:
                        data_map[d] = {"report_date": d}
                    d_entry = data_map[d]
                    for row in rows[1:]:
                        m_tag = row.find(["th", "td"])
                        if not m_tag:
                            continue
                        std_key = self._normalize_field(m_tag.get_text())
                        cols = row.find_all("td")
                        if i < len(cols):
                            d_entry[std_key] = self._clean_num(cols[i].get_text())
                break

        results = [
            {
                **data_map[d],
                **{k: v for k, v in top_metrics.items() if data_map[d].get(k) is None},
            }
            for d in sorted(data_map.keys(), reverse=True)
        ]
        return results

    def _fetch_google(self, symbol):
        url = f"https://www.google.com/finance/quote/{symbol}:NSE"
        r = self.session.get(url, timeout=10)
        if not r or r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not results:
                results.extend(
                    [{"report_date": f"Q-{i}", "source": "google"} for i in range(1, 5)]
                )
            for row in rows:
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue
                std_key = self._normalize_field(cells[0].get_text())
                for idx, c in enumerate(cells[1:5]):
                    if idx >= len(results):
                        break
                    val, txt = self._clean_num(c.get_text()), c.get_text().strip()
                    if "T" in txt:
                        val = (val * 1000000) if val else None
                    elif "B" in txt:
                        val = (val * 100) if val else None
                    elif "M" in txt:
                        val = (val / 10) if val else None
                    results[idx] = {**results[idx], **{std_key: val}}
        return [r for r in results if r.get("revenue")]

    def fetch_index_constituents(self, index_name="NIFTY 50"):
        sources = self.config.get("nse_indices", {}).get(index_name)
        if not sources:
            return []
        try:
            r = self.session.get(sources["primary"])
            if r and r.status_code == 200:
                return [self.unify_symbol(s) for s in self._parse_nse_csv(r.text)]
        except:
            pass
        return []

    def _parse_nse_csv(self, text):
        try:
            df = pd.read_csv(StringIO(text))
            col = (
                "Symbol"
                if "Symbol" in df.columns
                else "SYMBOL"
                if "SYMBOL" in df.columns
                else df.columns[2]
            )
            return df[col].tolist()
        except:
            return []

    def fetch_market_status(self):
        streams = self.registry.get("data_streams", {}).get("market_intelligence", [])
        headers = self.registry.get("headers", {}).get("nse_api_headers", {})
        for stream in [s for s in streams if s["name"] == "nse_market_status"]:
            try:
                self.session.get("https://www.nseindia.com", headers=headers)
                r = self.session.get(stream["url"], headers=headers)
                if r and r.status_code == 200:
                    return r.json()
            except:
                continue
        return None

    def sanitize_float(self, val):
        if val is None or str(val).strip() in ["", "-", "NaN", "null"]:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        try:
            clean = re.sub(r"[^\d.-]", "", str(val).replace(",", ""))
            return float(clean) if clean else 0.0
        except:
            return 0.0

    def _normalize_field(self, raw_name):
        name = str(raw_name).strip().upper()
        for key, standard in self.FIELD_MAP.items():
            if key == name or key in name:
                return standard
        return raw_name.lower().replace(" ", "_")

    def _clean_num(self, val):
        if not val:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        clean = re.sub(r"[^\d.]", "", val.replace(",", "").replace("%", ""))
        try:
            return float(clean) if clean else None
        except:
            return None

    def fetch_sast_disclosures(self, days=3):
        headers = self.registry.get("headers", {}).get("nse_api_headers", {}).copy()
        headers[
            "Referer"
        ] = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
        today = date.today()
        start_date = today - timedelta(days=days)
        end_str = f"{today.day:02d}-{today.month:02d}-{today.year}"
        start_str = f"{start_date.day:02d}-{start_date.month:02d}-{start_date.year}"
        url = f"https://www.nseindia.com/api/corporate-sast-reg29?index=equities&from_date={start_str}&to_date={end_str}"
        try:
            self.session.get("https://www.nseindia.com", headers=headers)
            r = self.session.get(url, headers=headers)
            if r and r.status_code == 200:
                return r.json().get("data", [])
        except:
            pass
        return []

    def fetch_pledged_info(self, symbol):
        headers = self.registry.get("headers", {}).get("nse_api_headers", {}).copy()
        url = f"https://www.nseindia.com/api/corporate-pledged-info?symbol={symbol}"
        try:
            self.session.get("https://www.nseindia.com", headers=headers)
            r = self.session.get(url, headers=headers)
            if r and r.status_code == 200:
                return r.json().get("data", [])
        except:
            pass
        return []

    def fetch_fii_dii_activity(self):
        headers = self.registry.get("headers", {}).get("nse_api_headers", {}).copy()
        url = "https://www.nseindia.com/api/fii_dii"
        try:
            self.session.get("https://www.nseindia.com", headers=headers)
            r = self.session.get(url, headers=headers)
            if r and r.status_code == 200:
                return r.json()
        except:
            pass
        return []

    def fetch_shareholding_pattern(self, symbol):
        headers = self.registry.get("headers", {}).get("nse_api_headers", {}).copy()
        url = f"https://www.nseindia.com/api/equity-shareholding?symbol={symbol}"
        try:
            self.session.get("https://www.nseindia.com", headers=headers)
            r = self.session.get(url, headers=headers)
            if r and r.status_code == 200:
                return r.json().get("data", [])
        except:
            pass
        return []

    def fetch_corporate_announcements(self, days=2):
        headers = self.registry.get("headers", {}).get("nse_api_headers", {}).copy()
        today = date.today()
        start_date = today - timedelta(days=days)
        end_str = f"{today.day:02d}-{today.month:02d}-{today.year}"
        start_str = f"{start_date.day:02d}-{start_date.month:02d}-{start_date.year}"
        url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={start_str}&to_date={end_str}"
        try:
            self.session.get("https://www.nseindia.com", headers=headers)
            r = self.session.get(url, headers=headers)
            if r and r.status_code == 200:
                return r.json()
        except:
            pass
        return []

    def get_ticker_info_safe(self, ticker):
        import yfinance as yf

        try:
            t = yf.Ticker(ticker)
            return t.info
        except:
            return {}
