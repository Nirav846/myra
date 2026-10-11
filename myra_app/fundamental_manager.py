# myra_app/fundamental_manager.py
import logging
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

import pandas as pd

from .data_sources import RateLimiter, SourceManager, normalize
from myra_app import constants
from myra_app.constants import DISABLE_FUNDAMENTAL_WRITERS
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

#: Requests-per-second for the fundamentals source chain. Screener.in is a
#: volunteer-run scraper target; the daily job can afford to be slow, so we
#: default well below one request per second and let operators tune it.
FUNDAMENTALS_RATE_PER_SEC = float(
    os.environ.get("MYRA_FUNDAMENTALS_RATE_PER_SEC", "0.5")
)

#: `_fetch_google` labels its rows "Q-1".."Q-4" because Google Finance exposes no
#: column headers we can date. Consumers order quarterly_results by
#: `report_date DESC` lexicographically, where "Q-1" outranks a real "Sep 2025"
#: row and would be read as the newest quarter.
_PLACEHOLDER_REPORT_DATE = re.compile(r"^Q-\d+$", re.IGNORECASE)


class FundamentalManager:
    """
    MYRA Fundamental Manager - The Orchestration Layer for Fundamentals (v3.0)
    TRILOGY ERA: Orchestrates ingest into Modular SQLite sidecars.
    """

    def __init__(self, db_dir=None, fetcher=None):
        # 2. Implementation: The Absolute Path Anchor.
        # constants.DB_DIR is the single source of truth for the sidecar
        # directory. It is read at call time (not captured at import) so the
        # test-isolation fixture that rebinds constants.DB_DIR also redirects
        # this class. Deriving the path from __file__ pointed at
        # <repo-root>/db, which does not exist.
        self.db_dir = db_dir or constants.DB_DIR

        self.fetcher = fetcher
        self.limiter = RateLimiter(rate_per_sec=FUNDAMENTALS_RATE_PER_SEC)
        self.source_manager = SourceManager()
        #: Human-readable reason the most recent fetch_fundamentals() call
        #: returned False. Callers must report this instead of assuming the
        #: data sources were at fault.
        self.last_error = None
        #: HTTP status of the most recent upstream fetch (None when unknown).
        #: A 429/403 is the source asking us to slow down, not a missing page.
        self.last_http_status = None
        #: Registry name of the source that served the most recent successful
        #: fetch (e.g. "screener_in"). Recorded as the freshness claim so other
        #: enrichers can tell who owns this symbol's metrics.
        self.last_source = None
        #: Set of Screener-owned fields the most recent fetch actually produced
        #: (non-null). A page can parse and still lack its top-ratios block, so a
        #: successful fetch is not by itself proof that Screener owns P/E, sector,
        #: etc. for this symbol -- callers use this to avoid stamping a claim that
        #: would strand those fields for the whole takeover window.
        self.last_owned_fields = frozenset()

        # Connections for sidecars. These MUST resolve through DB_MAP: the bare
        # "meta.db"/"valuation.db" names are not MYRA sidecars, so every write
        # silently went to a stray empty file and the INSERT failed with
        # "no such table: quarterly_results".
        self.meta_db = os.path.join(self.db_dir, LibrarianCore.DB_MAP["meta"])
        self.val_db = os.path.join(self.db_dir, LibrarianCore.DB_MAP["valuation"])

    def set_connection(self, conn):
        """Legacy support for Librarian."""
        pass

    def set_fetcher(self, fetcher):
        """Legacy support for Librarian."""
        self.fetcher = fetcher

    def _get_val_conn(self):
        conn = sqlite3.connect(self.val_db, timeout=20, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _get_meta_conn(self):
        conn = sqlite3.connect(self.meta_db, timeout=20, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def is_stale(self, symbol, days=90):
        """
        PKScreener Superpower: Smart Stale Detection.
        Checks the sync marker in meta.db (Atomic Standard).
        """
        symbol_clean = symbol.split(".")[0].upper()
        try:
            conn = self._get_meta_conn()
            res = conn.execute(
                "SELECT last_fundamental_update FROM symbols_master WHERE symbol = ?",
                (symbol_clean,),
            ).fetchone()
            conn.close()

            if not res or not res[0] or res[0] == "1900-01-01":
                return True

            last_date_str = res[0]
            try:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
            except ValueError:
                # Handle cases with time if any
                last_date = datetime.strptime(
                    last_date_str.split(" ")[0], "%Y-%m-%d"
                ).date()

            return (date.today() - last_date).days > days
        except Exception:
            return True

    def _fetch_raw_data(self, symbol):
        """[INGESTOR] Pulls raw JSON/CSV from data sources."""
        if not self.fetcher:
            return None

        # Actually space the calls out. This limiter was constructed and then
        # never used, so the bulk sync hit Screener.in as fast as the network
        # allowed -- the "it should not hit screener.in hard" complaint.
        self.limiter.wait()

        # DataFetcher handles its own internal prioritisation and returns (data, source_name)
        raw_data, source_name = self.fetcher.fetch_fundamentals(symbol)
        self.last_http_status = getattr(self.fetcher, "last_http_status", None)
        self.last_source = source_name if raw_data else None

        if raw_data:
            # We still normalize here using our unified schema
            data = normalize(raw_data, source_name)
            self.source_manager.mark_success(source_name)
            return data
        else:
            # The chain returned (None, None), which tells us neither WHICH
            # source failed nor that it was rate limiting. Booking it against a
            # non-existent "screener" key (the registry key is "screener_in")
            # printed a "Cooling off for 60 mins" warning that had no effect on
            # anything -- DataFetcher does its own prioritisation and never
            # consults SourceManager. Record it honestly instead.
            self.source_manager.mark_failure("screener_in")
            return None

    def fetch_fundamentals(self, symbol):
        """
        [MAPPER] Pulls data and splits it into quarterly_results and fundamentals summary.
        Renamed to fetch_fundamentals as per v3.0 requirements.
        """
        data = self._fetch_raw_data(symbol)
        # Reset per-call provenance so a failed fetch never carries a stale claim.
        self.last_owned_fields = frozenset()
        if not data:
            self.last_error = "multi-source fallback exhausted"
            return False

        # Undateable rows are worse than no rows -- writing them would poison
        # the lexicographic report_date ordering every consumer relies on.
        dated = [
            r
            for r in data
            if not _PLACEHOLDER_REPORT_DATE.match(str(r.get("report_date") or ""))
        ]
        if not dated:
            self.last_error = "source returned only undated rows (google fallback)"
            logger.warning(
                "FundamentalManager: %s produced only undated rows; skipping write",
                symbol,
            )
            return False
        data = dated

        symbol_clean = symbol.split(".")[0].upper()

        # 1. Update Detailed Quarterly Table (MAPPER)
        v_conn = self._get_val_conn()
        try:
            # Modular Schema: Map fields to quarterly_results
            cols = [
                "symbol",
                "report_date",
                "period_end",
                "revenue",
                "net_profit",
                "eps",
                "opm_pct",
                # Rest of Screener's quarterly P&L — same page, same request.
                "expenses",
                "operating_profit",
                "other_income",
                "interest",
                "depreciation",
                "profit_before_tax",
                "tax_pct",
            ]
            placeholders = ", ".join(["?" for _ in cols])
            col_names = ", ".join(cols)
            query = f"INSERT OR REPLACE INTO quarterly_results ({col_names}) VALUES ({placeholders})"

            # Optimized with executemany (Fix 115: Avoid execute in loop)
            records = [[symbol_clean] + [row.get(c) for c in cols[1:]] for row in data]
            if records:
                v_conn.executemany(query, records)

            # 2. Update Main Fundamentals Summary Table (MAPPER)
            latest = data[0]

            pe = latest.get("stock_pe") or latest.get("pe")
            roe = latest.get("roe")
            eps = latest.get("eps")
            bv = latest.get("book_value")
            mcap = latest.get("market_cap")
            sect = latest.get("sector")

            promoter_pct = latest.get("promoter_holding")
            # public_holding_pct keeps its established meaning (everything that
            # is not promoter-held); the FII/DII split is added alongside it as
            # inst_holding rather than redefining an existing column.
            public_pct = (100 - promoter_pct) if promoter_pct is not None else None
            ff_pct = public_pct  # free float = public holding in Indian market
            ff_mcap = (mcap * ff_pct / 100) if mcap and ff_pct else None
            inst_pct = latest.get("inst_holding")
            pledged_pct = latest.get("pledged_pct")

            # Which Screener-authoritative fields did this page actually carry?
            # A parse can succeed (quarterly rows present) while the top-ratios /
            # shareholding blocks are missing, in which case stamping a Screener
            # ownership claim would freeze those fields for the takeover window.
            from myra_app.source_priority import SCREENER_OWNED_FIELDS

            produced = {
                "pe": pe,
                "roe": roe,
                "eps": eps,
                "book_value": bv,
                "market_cap": mcap,
                "sector": sect,
                "promoter_holding_pct": promoter_pct,
                "public_holding_pct": public_pct,
                "free_float_pct": ff_pct,
                "free_float_market_cap": ff_mcap,
                "inst_holding": inst_pct,
                "pledged_pct": pledged_pct,
            }
            self.last_owned_fields = (
                frozenset(k for k, v in produced.items() if v is not None)
                & SCREENER_OWNED_FIELDS
            )

            # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns the
            # `fundamentals` table.  Skip ONLY the fundamentals UPSERT;
            # quarterly_results above still commits normally.
            if not DISABLE_FUNDAMENTAL_WRITERS:
                v_conn.execute(
                    """
                    INSERT INTO fundamentals (symbol, pe, roe, eps, book_value, market_cap, sector, industry, promoter_holding_pct, public_holding_pct, free_float_pct, free_float_market_cap, inst_holding, pledged_pct, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (symbol) DO UPDATE SET
                        -- Every column is COALESCE'd: a partial payload must never
                        -- null out a metric we already hold. New values still win.
                        pe = COALESCE(EXCLUDED.pe, fundamentals.pe),
                        roe = COALESCE(EXCLUDED.roe, fundamentals.roe),
                        eps = COALESCE(EXCLUDED.eps, fundamentals.eps),
                        book_value = COALESCE(EXCLUDED.book_value, fundamentals.book_value),
                        market_cap = COALESCE(EXCLUDED.market_cap, fundamentals.market_cap),
                        sector = COALESCE(EXCLUDED.sector, fundamentals.sector),
                        industry = COALESCE(EXCLUDED.industry, fundamentals.industry),
                        promoter_holding_pct = COALESCE(EXCLUDED.promoter_holding_pct, fundamentals.promoter_holding_pct),
                        public_holding_pct = COALESCE(EXCLUDED.public_holding_pct, fundamentals.public_holding_pct),
                        free_float_pct = COALESCE(EXCLUDED.free_float_pct, fundamentals.free_float_pct),
                        free_float_market_cap = COALESCE(EXCLUDED.free_float_market_cap, fundamentals.free_float_market_cap),
                        inst_holding = COALESCE(EXCLUDED.inst_holding, fundamentals.inst_holding),
                        pledged_pct = COALESCE(EXCLUDED.pledged_pct, fundamentals.pledged_pct),
                        last_updated = EXCLUDED.last_updated
                """,
                    (
                        symbol_clean,
                        pe,
                        roe,
                        eps,
                        bv,
                        mcap,
                        sect,
                        sect,
                        promoter_pct,
                        public_pct,
                        ff_pct,
                        ff_mcap,
                        inst_pct,
                        pledged_pct,
                        date.today().isoformat(),
                    ),
                )

            v_conn.commit()

            # 3. Update Sync Marker in meta.db [LIBRARIAN]
            m_conn = self._get_meta_conn()
            try:
                m_conn.execute(
                    "UPDATE symbols_master SET last_fundamental_update = ? WHERE symbol = ?",
                    (date.today().isoformat(), symbol_clean),
                )
                m_conn.commit()
            finally:
                m_conn.close()

            return True
        except Exception as exc:
            # Never swallow this: a bare `return False` here is indistinguishable
            # from "all sources failed", which is how a wrong-DB-path bug came
            # to be reported as 1632 × "multi-source fallback exhausted".
            logger.exception(
                "FundamentalManager: failed to persist fundamentals for %s",
                symbol_clean,
            )
            self.last_error = f"persist failed ({exc.__class__.__name__}: {exc})"
            return False
        finally:
            v_conn.close()

    def get_bulk_f_scores(self, symbols):
        """Calculates F-Scores for multiple symbols using a single query."""
        if not symbols:
            return {}
        symbol_cleans = [s.split(".")[0].upper() for s in symbols]
        try:
            conn = self._get_val_conn()
            placeholders = ",".join(["?"] * len(symbol_cleans))
            query = f"""
                SELECT symbol, report_date, net_profit
                FROM (
                    SELECT symbol, report_date, net_profit,
                           ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY report_date DESC) as rn
                    FROM quarterly_results
                    WHERE symbol IN ({placeholders})
                )
                WHERE rn <= 4
            """
            df = pd.read_sql(query, conn, params=symbol_cleans)
            conn.close()

            scores = {}
            for sym in symbol_cleans:
                sym_df = df[df["symbol"] == sym]
                if len(sym_df) < 2:
                    scores[sym] = 0
                    continue
                score = 0
                latest = sym_df.iloc[0]
                prev = sym_df.iloc[1]
                if latest.get("net_profit", 0) > 0:
                    score += 1
                if latest.get("net_profit", 0) > prev.get("net_profit", 0):
                    score += 1
                scores[sym] = score
            return scores
        except Exception:
            # print("Bulk F-Score Exception:", e)
            return {}

    def calculate_f_score(self, symbol):
        """Piotroski F-Score implementation for Trilogy DB."""
        symbol_clean = symbol.split(".")[0].upper()
        try:
            conn = self._get_val_conn()
            df = pd.read_sql(
                "SELECT * FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC LIMIT 4",
                conn,
                params=[symbol_clean],
            )
            conn.close()

            if len(df) < 2:
                return 0
            score = 0
            latest = df.iloc[0]
            prev = df.iloc[1]

            if latest.get("net_profit", 0) > 0:
                score += 1
            if latest.get("net_profit", 0) > prev.get("net_profit", 0):
                score += 1

            # Ratio checks... (Simplified for v3.0 core)
            return score
        except Exception:
            return 0

    def get_bulk_valuation_metrics(self, symbols):
        """Calculates Graham Number for multiple symbols using a single query."""
        if not symbols:
            return {}
        symbol_cleans = [s.split(".")[0].upper() for s in symbols]
        try:
            conn = self._get_val_conn()
            placeholders = ",".join(["?"] * len(symbol_cleans))
            query = f"""
                SELECT symbol, eps, book_value
                FROM (
                    SELECT symbol, eps, book_value,
                           ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY report_date DESC) as rn
                    FROM quarterly_results
                    WHERE symbol IN ({placeholders})
                )
                WHERE rn = 1
            """
            rows = conn.execute(query, symbol_cleans).fetchall()
            conn.close()

            metrics = {}
            for sym, eps, bv in rows:
                if eps and bv and eps > 0 and bv > 0:
                    graham = (22.5 * eps * bv) ** 0.5
                    metrics[sym] = {"graham_number": round(graham, 2)}
                else:
                    metrics[sym] = {}
            return metrics
        except Exception:
            return {}

    def get_valuation_metrics(self, symbol):
        """Calculates Graham Number from Trilogy DB."""
        symbol_clean = symbol.split(".")[0].upper()
        try:
            conn = self._get_val_conn()
            res = conn.execute(
                "SELECT eps, book_value FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC LIMIT 1",
                (symbol_clean,),
            ).fetchone()
            conn.close()

            if not res or not res[0] or not res[1]:
                return {}
            eps, bv = res
            if eps > 0 and bv > 0:
                graham = (22.5 * eps * bv) ** 0.5
                return {"graham_number": round(graham, 2)}
        except Exception:
            pass
        return {}
