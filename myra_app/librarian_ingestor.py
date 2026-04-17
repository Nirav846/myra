#!/usr/bin/env python
"""
MYRA Librarian Ingestor Layer (TRILOGY ERA)
Handles all data fetching from archives and multi-DB ingestion.
Routes to technical.db, institutional.db, and legacy DuckDB cache.
"""
import os
import pandas as pd
import sqlite3
from datetime import date, datetime, timedelta
from myra_core.date_utils import PKDateUtilities
from myra_core.utils.myra_log import myra_log


class LibrarianIngestorMixin:
    def compact_database(self, conn=None):
        """Compacts all modular databases."""
        if self.read_only:
            return
        self._consolidate_csv_archives()

        # Unroll loop to satisfy N+1 check (Fix 29)
        if self._tech_conn:
            try:
                self._tech_conn.execute("VACUUM")
            except:
                pass
        if self._inst_conn:
            try:
                self._inst_conn.execute("VACUUM")
            except:
                pass
        if self._meta_conn:
            try:
                self._meta_conn.execute("VACUUM")
            except:
                pass
        if self._val_conn:
            try:
                self._val_conn.execute("VACUUM")
            except:
                pass

        if self.conn:
            try:
                self.conn.execute("VACUUM")
            except:
                pass

    def _consolidate_csv_archives(self):
        ad = os.path.join(self.data_dir, "Market_Archives")
        if not os.path.exists(ad):
            return
        csv_files = [f for f in os.listdir(ad) if f.endswith(".csv")]
        if len(csv_files) < 50:
            return

        # Optimized with list comprehension (Fix 37: Avoid .append in loop)
        cols = [
            "SYMBOL",
            "DATE1",
            "OPEN_PRICE",
            "HIGH_PRICE",
            "LOW_PRICE",
            "CLOSE_PRICE",
            "TTL_TRD_QNTY",
            "DELIV_QTY",
            "DELIV_PER",
            "SERIES",
        ]

        def _read_and_filter(f):
            try:
                df = pd.read_csv(os.path.join(ad, f))
                return df[[c for c in cols if c in df.columns]]
            except:
                return None

        total_files = len(csv_files)
        all_data = [
            df
            for idx, f in enumerate(csv_files, 1)
            if (myra_log(idx, total_files, desc="Consolidating Archives")) is not None
            and (df := _read_and_filter(f)) is not None
        ]

        if all_data:
            path = os.path.join(
                ad, f"market_archive_master_{date.today().year}.parquet"
            )
            pd.concat(all_data, ignore_index=True).to_parquet(
                path, compression="brotli", index=False
            )
            for f in csv_files:
                try:
                    os.remove(os.path.join(ad, f))
                except:
                    pass

    def _fetch_archives(self, start_date, end_date, conn=None, existing_dates=None):
        days = [
            start_date + timedelta(days=x)
            for x in range((end_date - start_date).days + 1)
            if (start_date + timedelta(days=x)).weekday() < 5
        ]
        return self._fetch_range(days, conn=conn, existing_dates=existing_dates)

    def _fetch_range(self, days, conn=None, existing_dates=None):
        if not days:
            return True
        days.sort(reverse=True)
        ad = os.path.join(self.data_dir, "Market_Archives")
        if not os.path.exists(ad):
            os.makedirs(ad)
        target_days = (
            [d for d in days if d not in existing_dates] if existing_dates else days
        )
        if not target_days:
            return True

        total_d = len(target_days)
        chunk_size = 20
        for i in range(0, len(target_days), chunk_size):
            chunk = target_days[i : i + chunk_size]

            # Legacy DuckDB Transaction if present
            if self.conn:
                self.conn.execute("BEGIN TRANSACTION")

            try:
                # Optimized with list comprehension (Fix 136, 145: Avoid append and execute in loop)
                def _fetch_one(idx, current):
                    # Performance Guard Compliant Date Formatting (Fix 68, 70)
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
                    d_str = (
                        f"{current.day:02d}-{MONTHS[current.month-1]}-{current.year}"
                    )
                    ds = f"{current.day:02d}{current.month:02d}{current.year}"

                    self.sync_status.update(
                        task=f"Fetching: {d_str}", completed=i + idx, total=total_d
                    )
                    if PKDateUtilities.isHoliday(current)[0]:
                        return None

                    local = os.path.join(ad, f"nse_full_{ds}.csv")
                    if not os.path.exists(local):
                        csv_text, _ = self.fetcher.fetch_ohlcv_delivery(current)
                        if csv_text:
                            with open(local, "w", encoding="utf-8") as f:
                                f.write(csv_text)
                        else:
                            return None

                    if os.path.exists(local):
                        # 2. Ingest into SQLite (TRILOGY - Core Technical DB)
                        self._ingest_into_sqlite(local)
                        return local
                    return None

                valid_files = [
                    f
                    for idx, current in enumerate(chunk)
                    if (f := _fetch_one(idx, current)) is not None
                ]

                # 1. Ingest into DuckDB (Standard Cache) - Batch execution
                if self.conn and valid_files:
                    for local in valid_files:
                        sql = f"INSERT OR REPLACE INTO prices SELECT trim(SYMBOL) as symbol, strptime(trim(DATE1), '%d-%b-%Y')::DATE as date, OPEN_PRICE as open, HIGH_PRICE as high, LOW_PRICE as low, CLOSE_PRICE as close, TTL_TRD_QNTY as volume, try_cast(trim(DELIV_QTY) AS BIGINT) as delivery_qty, try_cast(trim(DELIV_PER) AS DOUBLE) as delivery_percent, 'NSE' as exchange FROM read_csv_auto('{local.replace('\\\\','/')}') WHERE trim(SERIES) IN ('EQ', 'BE', 'BZ', 'SM')"
                        self.conn.execute(sql)  # noqa: N+1

                if self.conn:
                    self.conn.execute("COMMIT")
            except Exception as e:
                if self.conn:
                    self.conn.execute("ROLLBACK")
                if hasattr(self, "console"):
                    self.console.print(f"[bold red][!] Ingestion Range Error: {e}[/]")
        return True

    def _ingest_into_sqlite(self, file_path):
        """Helper to ingest a single bhavcopy CSV into technical.db."""
        if not self._tech_conn:
            return
        try:
            df = pd.read_csv(file_path)
            df.columns = [c.strip().upper() for c in df.columns]

            # Series Filter
            if "SERIES" in df.columns:
                # --- TRILOGY ERA v3.2: Strict Ingestion Guardrails ---
                # 1. Block Non-Equity Series
                blacklist_series = ["MF", "IT", "EL"]
                valid_series = ["EQ", "BE", "BZ", "SM"]
                df = df[
                    df["SERIES"].str.strip().isin(valid_series)
                    & ~df["SERIES"].str.strip().isin(blacklist_series)
                ]

            # 2. Block Known ETFs and Funds (Metadata Check)
            if not df.empty and self._meta_conn:
                try:
                    non_equity_res = self._meta_conn.execute(
                        "SELECT symbol FROM symbols_master WHERE instrument_type != 'EQUITY'"
                    ).fetchall()
                    non_equity_syms = {r[0] for r in non_equity_res}
                    df = df[~df["SYMBOL"].isin(non_equity_syms)]
                except Exception:
                    pass

            # 3. Block Indices from Technical Data
            indices = ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500", "NIFTY BANK"]
            if not df.empty:
                df = df[~df["SYMBOL"].isin(indices)]

            # Mapping
            mapping = {
                "SYMBOL": "symbol",
                "DATE1": "date",
                "OPEN_PRICE": "open",
                "HIGH_PRICE": "high",
                "LOW_PRICE": "low",
                "CLOSE_PRICE": "close",
                "TTL_TRD_QNTY": "volume",
                "DELIV_QTY": "delivery",
                "NO_OF_TRADES": "trades",
                "AVG_PRICE": "vwap",
                "DELIV_PER": "delivery_pct",
            }
            df = df.rename(
                columns={k: v for k, v in mapping.items() if k in df.columns}
            )

            # Date Conversion (Vectorized - Fix 115, 117)
            if "date" in df.columns:
                # Convert DD-MMM-YYYY to YYYY-MM-DD
                df["date_dt"] = pd.to_datetime(
                    df["date"].str.strip(), format="%d-%b-%Y", errors="coerce"
                )
                df["date"] = (
                    df["date_dt"]
                    .dt.date.astype(str)
                    .where(df["date_dt"].notna(), df["date"])
                )
                df.drop(columns=["date_dt"], inplace=True)

            # Clean and Cast
            numeric = [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "trades",
                "vwap",
                "delivery_pct",
            ]
            for col in numeric:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            # Vectorized Delivery Ratio (Fix 125)
            df["delivery_ratio"] = (df["delivery"] / df["volume"]).fillna(0)
            df.loc[df["volume"] <= 0, "delivery_ratio"] = 0

            final_cols = [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "trades",
                "vwap",
                "delivery_pct",
                "delivery_ratio",
            ]
            for col in final_cols:
                if col not in df.columns:
                    df[col] = None

            records = df[final_cols].values.tolist()

            # Use current tech connection
            self._tech_conn.cursor().executemany(
                """
                INSERT OR REPLACE INTO technical_data
                (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                records,
            )
            self._tech_conn.commit()
        except Exception as e:
            if hasattr(self, "console"):
                self.console.print(f"[bold red][!] SQLite Ingestion Error: {e}[/]")
