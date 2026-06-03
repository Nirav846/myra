import os
import sqlite3
from datetime import date

import pandas as pd


class FundamentalRanker:
    """
    MYRA Fundamental Ranking Engine (v1.1)
    Scores stocks based on Growth, Quality, Stability, and Risk.
    Uses SQLite (scoring.db) for caching and DuckDB for raw data processing.
    """

    def __init__(self, val_conn, scoring_db_path="scoring.db"):
        self.val_conn = val_conn
        self.scoring_db_path = scoring_db_path

    def _get_scoring_conn(self):
        try:
            return sqlite3.connect(self.scoring_db_path)
        except Exception:
            return None

    def materialize_scores(self, symbols=None):
        """
        PKScreener Superpower: Score Materialization.
        Pre-calculates all fundamental scores and saves them to SQLite.
        """
        print("[MYRA] Materializing Fundamental Scores...")
        df = self._calculate_all_scores_from_duck(symbols)
        if df.empty:
            return

        conn_sq = self._get_scoring_conn()
        if not conn_sq:
            return

        try:
            cursor = conn_sq.cursor()
            today = date.today().isoformat()

            def _to_record(row):
                score = row.Funda_Score
                grade = (
                    "A" if score >= 50
                    else "B" if score >= 35
                    else "C" if score >= 20
                    else "D"
                )
                return (
                    row.Stock,
                    today,
                    float(getattr(row, "margin_score", 0)),
                    float(getattr(row, "roe_score", 0)),
                    float(getattr(row, "div_score", 0)),
                    0.0,
                    float(score),
                    grade,
                )

            records = [_to_record(row) for row in df.itertuples(index=False)]

            cursor.executemany(
                """
                INSERT OR REPLACE INTO fundamental_scores 
                (symbol, date, growth_score, quality_score, stability_score, risk_score, total_funda_score, grade)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                records,
            )
            conn_sq.commit()
            conn_sq.close()
            print(f"[+] Materialized {len(records)} scores to scoring.db")
        except Exception as e:
            print(f"[!] Materialization failed: {e}")

    def _calculate_all_scores_from_duck(self, symbols=None):
        """Score stocks using available fields in myra_valuation.db fundamentals table."""
        if not self.val_conn:
            return pd.DataFrame()
        try:
            where = ""
            params = []
            if symbols:
                clean = [s.split(".")[0].upper() for s in symbols]
                placeholders = ",".join("?" * len(clean))
                where = f"WHERE symbol IN ({placeholders})"
                params = clean

            df = pd.read_sql(
                f"""
                SELECT symbol, pe, sector_pe, net_margin, roe_ttm, dividend_yield
                FROM fundamentals
                {where}
                """,
                self.val_conn,
                params=params if params else None,
            )

            if df.empty:
                return pd.DataFrame()

            df = df.rename(columns={"symbol": "Stock"})

            # Valuation score: pe vs sector_pe (lower is better)
            df["val_score"] = df.apply(
                lambda r: (
                    20 if (r["pe"] > 0 and r["sector_pe"] > 0 and r["pe"] < r["sector_pe"])
                    else 10 if (r["pe"] > 0 and r["sector_pe"] > 0 and r["pe"] < r["sector_pe"] * 1.2)
                    else 0
                ),
                axis=1,
            )

            # Quality score: net_margin
            df["margin_score"] = df["net_margin"].apply(
                lambda x: 20 if x > 20 else 10 if x > 10 else 5 if x > 0 else 0
            )

            # Quality score: roe_ttm
            df["roe_score"] = df["roe_ttm"].apply(
                lambda x: 20 if x > 20 else 15 if x > 15 else 10 if x > 10 else 5 if x > 0 else 0
            )

            # Stability score: dividend_yield
            df["div_score"] = df["dividend_yield"].apply(
                lambda x: 10 if x > 3 else 5 if x > 1 else 0
            )

            df["Funda_Score"] = df["val_score"] + df["margin_score"] + df["roe_score"] + df["div_score"]

            df["Grade"] = df["Funda_Score"].apply(
                lambda s: "A" if s >= 50 else "B" if s >= 35 else "C" if s >= 20 else "D"
            )

            return df[["Stock", "Funda_Score", "Grade", "val_score", "margin_score", "roe_score", "div_score"]]

        except Exception as e:
            print(f"[FundamentalRanker] Score calculation failed: {e}")
            return pd.DataFrame()

    def rank(self, symbols=None, use_cache=True):
        """
        Optimized Rank: Uses SQLite cache if available.
        """
        if use_cache:
            conn_sq = self._get_scoring_conn()
            if conn_sq:
                try:
                    where = ""
                    if symbols:
                        sym_list = "', '".join(
                            [s.split(".")[0].upper() for s in symbols]
                        )
                        where = f"WHERE symbol IN ('{sym_list}')"

                    df = pd.read_sql(
                        f"SELECT symbol as Stock, total_funda_score as Funda_Score, grade as Grade FROM fundamental_scores {where} ORDER BY Funda_Score DESC",
                        conn_sq,
                    )
                    conn_sq.close()
                    if not df.empty:
                        return df
                except Exception:
                    pass

        # Fallback to DuckDB calculation
        return self._calculate_all_scores_from_duck(symbols).sort_values(
            "Funda_Score", ascending=False
        )
