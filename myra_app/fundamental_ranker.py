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

    def __init__(self, duck_conn, scoring_db_path="scoring.db"):
        self.duck_conn = duck_conn
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
                    "A"
                    if score >= 70
                    else "B" if score >= 50 else "C" if score >= 30 else "D"
                )
                return (
                    row.Stock,
                    today,
                    float(getattr(row, "Rev_Growth_Per", 0)),
                    float(getattr(row, "ROE", 0)),
                    0.0,  # stability placeholder
                    float(getattr(row, "Pledge_Pct", 0)),
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
        if not self.duck_conn:
            return pd.DataFrame()

        sym_filter = ""
        if symbols:
            sym_list = "', '".join([s.split(".")[0].upper() for s in symbols])
            sym_filter = f"WHERE symbol IN ('{sym_list}')"

        isin_bridge_path = os.path.join(os.getcwd(), "data", "isin_bridge.parquet")
        has_isin = os.path.exists(isin_bridge_path)

        query = f"""
        WITH base AS (
            SELECT *,
                CASE 
                    WHEN report_date LIKE 'Jan %' THEN CAST(SUBSTR(report_date, 5) || '-01-01' AS DATE)
                    WHEN report_date LIKE 'Feb %' THEN CAST(SUBSTR(report_date, 5) || '-02-01' AS DATE)
                    WHEN report_date LIKE 'Mar %' THEN CAST(SUBSTR(report_date, 5) || '-03-01' AS DATE)
                    WHEN report_date LIKE 'Apr %' THEN CAST(SUBSTR(report_date, 5) || '-04-01' AS DATE)
                    WHEN report_date LIKE 'May %' THEN CAST(SUBSTR(report_date, 5) || '-05-01' AS DATE)
                    WHEN report_date LIKE 'Jun %' THEN CAST(SUBSTR(report_date, 5) || '-06-01' AS DATE)
                    WHEN report_date LIKE 'Jul %' THEN CAST(SUBSTR(report_date, 5) || '-07-01' AS DATE)
                    WHEN report_date LIKE 'Aug %' THEN CAST(SUBSTR(report_date, 5) || '-08-01' AS DATE)
                    WHEN report_date LIKE 'Sep %' THEN CAST(SUBSTR(report_date, 5) || '-09-01' AS DATE)
                    WHEN report_date LIKE 'Oct %' THEN CAST(SUBSTR(report_date, 5) || '-10-01' AS DATE)
                    WHEN report_date LIKE 'Nov %' THEN CAST(SUBSTR(report_date, 5) || '-11-01' AS DATE)
                    WHEN report_date LIKE 'Dec %' THEN CAST(SUBSTR(report_date, 5) || '-12-01' AS DATE)
                    ELSE TRY_CAST(report_date AS DATE)
                END as sort_date
            FROM fundamentals_quarterly {sym_filter}
        ),
        latest_snapshot AS (
            SELECT 
                symbol, roe, opm_pct, pledged_pct, industry_pe, stock_pe,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY sort_date DESC) as rn
            FROM base
        ),
        growth_calc AS (
            SELECT 
                symbol, revenue, net_profit, roce, sales_per_share, sort_date,
                LAG(revenue, 4) OVER (PARTITION BY symbol ORDER BY sort_date) as prev_revenue,
                LAG(net_profit, 4) OVER (PARTITION BY symbol ORDER BY sort_date) as prev_profit,
                LAG(sales_per_share, 4) OVER (PARTITION BY symbol ORDER BY sort_date) as prev_sps
            FROM base
        ),
        agg_scores AS (
            SELECT 
                symbol,
                AVG(CASE WHEN prev_revenue > 0 THEN (revenue - prev_revenue)/prev_revenue ELSE 0 END) * 100 as rev_growth,
                AVG(
                    CASE
                        WHEN prev_profit <= 0 AND net_profit > 0 THEN 100.0
                        WHEN prev_profit != 0 THEN ((net_profit - prev_profit) / ABS(prev_profit)) * 100.0
                        ELSE 0.0
                    END
                ) AS profit_growth,
                AVG(CASE WHEN prev_sps > 0 THEN (sales_per_share - prev_sps)/prev_sps ELSE 0 END) * 100 as sps_growth,
                AVG(roce) as avg_roce
            FROM growth_calc
            GROUP BY symbol
        )
        """
        if has_isin:
            query += f"""
            SELECT
                a.symbol as Stock,
                COALESCE(a.rev_growth, 0) as Rev_Growth_Per,
                COALESCE(a.sps_growth, 0) as SPS_Growth_Per,
                COALESCE(a.avg_roce, 0) as ROCE,
                COALESCE(l.roe, 0) as ROE,
                COALESCE(l.stock_pe, 0) as PE,
                COALESCE(l.industry_pe, 0) as Ind_PE,
                COALESCE(l.pledged_pct, 0) as Pledge_Pct,
                (
                    COALESCE((CASE WHEN a.sps_growth > 20 THEN 20 WHEN a.sps_growth > 10 THEN 10 ELSE 5 END), 5) +
                    COALESCE((CASE WHEN a.avg_roce > 20 THEN 15 WHEN a.avg_roce > 15 THEN 10 ELSE 5 END), 5) +
                    COALESCE((CASE WHEN l.roe > 15 THEN 15 WHEN l.roe > 10 THEN 10 ELSE 5 END), 5) +
                    COALESCE((CASE WHEN l.stock_pe > 0 AND l.stock_pe < l.industry_pe THEN 20 WHEN l.stock_pe > 0 AND l.stock_pe < (l.industry_pe * 1.2) THEN 10 ELSE 0 END), 0) +
                    COALESCE((CASE WHEN l.opm_pct > 20 THEN 10 WHEN l.opm_pct > 10 THEN 5 ELSE 0 END), 0) +
                    COALESCE((CASE WHEN l.pledged_pct > 20 THEN -30 WHEN l.pledged_pct > 5 THEN -10 ELSE 0 END), 0)
                ) as Funda_Score
            FROM agg_scores a
            LEFT JOIN read_parquet('{isin_bridge_path}') b_a ON a.symbol = b_a.SYMBOL
            JOIN (
                SELECT ls.*, COALESCE(b.ISIN, ls.symbol) as join_isin
                FROM latest_snapshot ls
                LEFT JOIN read_parquet('{isin_bridge_path}') b ON ls.symbol = b.SYMBOL
                WHERE ls.rn = 1
            ) l ON COALESCE(b_a.ISIN, a.symbol) = l.join_isin
            """
        else:
            query += """
            SELECT
                a.symbol as Stock,
                COALESCE(a.rev_growth, 0) as Rev_Growth_Per,
                COALESCE(a.sps_growth, 0) as SPS_Growth_Per,
                COALESCE(a.avg_roce, 0) as ROCE,
                COALESCE(l.roe, 0) as ROE,
                COALESCE(l.stock_pe, 0) as PE,
                COALESCE(l.industry_pe, 0) as Ind_PE,
                COALESCE(l.pledged_pct, 0) as Pledge_Pct,
                (
                    COALESCE((CASE WHEN a.sps_growth > 20 THEN 20 WHEN a.sps_growth > 10 THEN 10 ELSE 5 END), 5) +
                    COALESCE((CASE WHEN a.avg_roce > 20 THEN 15 WHEN a.avg_roce > 15 THEN 10 ELSE 5 END), 5) +
                    COALESCE((CASE WHEN l.roe > 15 THEN 15 WHEN l.roe > 10 THEN 10 ELSE 5 END), 5) +
                    COALESCE((CASE WHEN l.stock_pe > 0 AND l.stock_pe < l.industry_pe THEN 20 WHEN l.stock_pe > 0 AND l.stock_pe < (l.industry_pe * 1.2) THEN 10 ELSE 0 END), 0) +
                    COALESCE((CASE WHEN l.opm_pct > 20 THEN 10 WHEN l.opm_pct > 10 THEN 5 ELSE 0 END), 0) +
                    COALESCE((CASE WHEN l.pledged_pct > 20 THEN -30 WHEN l.pledged_pct > 5 THEN -10 ELSE 0 END), 0)
                ) as Funda_Score
            FROM agg_scores a
            JOIN latest_snapshot l ON a.symbol = l.symbol AND l.rn = 1
            """

        try:
            return self.duck_conn.execute(query).df()
        except Exception:
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
