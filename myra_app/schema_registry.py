import logging
import sqlite3

logger = logging.getLogger(__name__)


class SchemaRegistry:
    """
    MYRA v3.3 Schema Registry & Standardization Layer.
    Centralized source of truth for database schemas and data ingestion mappings.
    """

    # 1. DYNAMIC COLUMN MAPPING (PRIORITY 1.2)
    # Maps varying Bhavcopy/External headers to canonical MYRA keys.
    COLUMN_MAPPINGS = {
        "symbol": ["SYMBOL", "Symbol", "symbol", "TICKER", "Ticker"],
        "date": ["DATE1", "Date", "date", "TIMESTAMP", "Timestamp", "TRADING_DATE"],
        "series": ["SERIES", "Series", "series", "EQ"],
        "open": ["OPEN", "Open", "open", "OPEN_PRICE", "Open Price"],
        "high": ["HIGH", "High", "high", "HIGH_PRICE", "High Price"],
        "low": ["LOW", "Low", "low", "LOW_PRICE", "Low Price"],
        "close": ["CLOSE", "Close", "close", "CLOSE_PRICE", "Close Price"],
        "volume": [
            "TTL_TRD_QNTY",
            "TOTTRDQTY",
            "VOLUME",
            "Volume",
            "volume",
            "TRADED_QTY",
        ],
        "delivery": [
            "DELIV_QTY",
            "DELIVERY_QTY",
            "Delivery Quantity",
            "delivery",
            "Deliverable Volume",
            "DELIVERABLE_VOLUME",
        ],
        "delivery_pct": [
            "DELIV_PER",
            "DELIVERY_PCT",
            "Delivery Percentage",
            "delivery_pct",
            "% Dly Qt to Traded Qty",
            "DELIVERY_TO_TRADED_QUANTITY",
        ],
        "trades": ["TOTAL_TRADES", "TOTALTRADES", "Trades", "trades"],
        "vwap": ["AVERAGE_PRICE", "AVG_PRICE", "VWAP", "vwap", "Average Price"],
    }

    # 2. CANONICAL DB SCHEMAS (PRIORITY 2.2)
    TABLES = {
        # ── technical.db ──────────────────────────────────────
        "technical_data": {
            "db": "technical",
            "columns": {
                "symbol": "TEXT NOT NULL",
                "date": "TEXT NOT NULL",
                "open": "REAL",
                "high": "REAL",
                "low": "REAL",
                "close": "REAL",
                "volume": "INTEGER",
                "delivery": "INTEGER",
                "trades": "INTEGER",
                "vwap": "REAL",
                "delivery_pct": "REAL",
                "delivery_ratio": "REAL",
            },
            "primary_key": "(symbol, date)",
            "required_for_ingestion": ["symbol", "date", "close", "volume"],
        },
        "launchpad_events": {
            "db": "technical",
            "columns": {
                "symbol": "TEXT",
                "trigger_date": "TEXT",
                "trigger_peak_price": "REAL",
                "digestion_low_price": "REAL",
                "digestion_low_date": "TEXT",
                "launchpad_date": "TEXT",
                "launchpad_close": "REAL",
                "breakout_date": "TEXT",
                "breakout_close": "REAL",
                "return_pct": "REAL",
                "days_to_breakout": "REAL",
                "success": "INTEGER",
                "max_drawdown_pct": "REAL",
                "min_range_atr_ratio": "TEXT",
                "min_vol_ratio": "TEXT",
            },
            "primary_key": "(symbol, trigger_date)",
        },
        "launchpad_features": {
            "db": "technical",
            "columns": {
                "symbol": "TEXT",
                "trigger_date": "TEXT",
                "breakout_date": "TEXT",
                "return_pct": "REAL",
                "max_drawdown_pct": "REAL",
                "days_to_breakout": "REAL",
                "success": "INTEGER",
                "del_zscore_min": "REAL",
                "del_zscore_mean": "REAL",
                "range_atr_min": "REAL",
                "vol_ratio_min": "REAL",
                "digestion_days": "INTEGER",
                "close_min": "REAL",
                "vwap_min": "REAL",
                "volume_min": "INTEGER",
                "liquidity_min": "REAL",
                "fvg_freshness_min": "REAL",
            },
            "primary_key": "(symbol, trigger_date)",
        },
        "ingestion_rejects": {
            "db": "technical",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "reason": "TEXT",
                "raw_values": "TEXT",
                "timestamp": "DATETIME",
            },
            "primary_key": "",
        },
        # ── valuation.db ──────────────────────────────────────
        "fundamentals": {
            "db": "valuation",
            "columns": {
                "symbol": "TEXT NOT NULL",
                "pe": "REAL",
                "roe": "REAL",
                "eps": "REAL",
                "book_value": "REAL",
                "market_cap": "REAL",
                "sector": "TEXT",
                "last_updated": "TEXT",
                "profit_growth": "REAL",
                "sales_growth": "REAL",
                "debt_to_equity": "REAL",
                "inst_holding": "REAL",
                "dividend_yield": "REAL",
                "date": "TEXT",
                "sector_pe": "REAL",
                "face_value": "REAL",
                "issued_size": "REAL",
                "net_margin": "REAL",
                "roe_ttm": "REAL",
                "daily_volatility": "REAL",
                "annual_volatility": "REAL",
                "impact_cost": "REAL",
                "source_ms": "TEXT",
                "source_nse": "TEXT",
                "free_float_pct": "REAL",
                "free_float_market_cap": "REAL",
                "free_float_shares": "REAL",
                "insider_holding_pct": "REAL",
                "public_holding_pct": "REAL",
                "industry": "TEXT",
                "shares_outstanding": "REAL",
                "promoter_holding_pct": "REAL",
                "last_fundamental_update": "TEXT",
            },
            "primary_key": "(symbol)",
        },
        # Point-in-time mcap history for leak-free Wyckoff calibration.
        # Table is CREATED at runtime by myra_app/backfill_fundamentals.py
        # (SchemaRegistry.validate_schema only ALTERs existing tables) — this
        # entry gives the registry validation/parity awareness of it.
        "fundamentals_history": {
            "db": "valuation",
            "columns": {
                "symbol": "TEXT NOT NULL",
                "date": "TEXT NOT NULL",
                "market_cap": "REAL",
                "free_float_mcap": "REAL",
                "free_float_pct": "REAL",
                "source": "TEXT",
            },
            "primary_key": "(symbol, date)",
        },
        "quarterly_results": {
            "db": "valuation",
            "columns": {
                "symbol": "TEXT",
                "report_date": "TEXT",
                "period_end": "TEXT",
                "revenue": "REAL",
                "net_profit": "REAL",
                "eps": "REAL",
                "opm_pct": "REAL",
            },
            "primary_key": "(symbol, report_date)",
        },
        # ── meta.db ───────────────────────────────────────────
        "symbols_master": {
            "db": "meta",
            "columns": {
                "symbol": "TEXT PRIMARY KEY",
                "first_seen": "TEXT",
                "last_seen": "TEXT",
                "in_active_universe": "INTEGER DEFAULT 0",
                "in_nifty500": "INTEGER DEFAULT 0",
                "sector": "TEXT",
                "industry": "TEXT",
                "raw_sector": "TEXT",
                "raw_industry": "TEXT",
                "source": "TEXT",
                "confidence": "REAL",
                "last_updated_sector": "TEXT",
                "sector_locked": "INTEGER DEFAULT 0",
                "is_active": "INTEGER DEFAULT 1",
                "instrument_type": "TEXT DEFAULT 'EQUITY'",
                "last_fundamental_update": "TEXT",
                "bse_scrip_code": "TEXT",
            },
            "primary_key": "(symbol)",
        },
        "index_constituents": {
            "db": "meta",
            "columns": {
                "index_name": "TEXT",
                "symbol": "TEXT",
                "added_date": "TEXT",
            },
            "primary_key": "(index_name, symbol)",
        },
        "benchmarks": {
            "db": "meta",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "close": "REAL",
            },
            "primary_key": "(symbol, date)",
        },
        "metadata": {
            "db": "meta",
            "columns": {
                "key": "TEXT PRIMARY KEY",
                "value": "TEXT",
            },
            "primary_key": "(key)",
        },
        "lineage_tracking": {
            "db": "meta",
            "columns": {
                "dataset_name": "TEXT",
                "fetch_time": "TEXT",
                "source_url": "TEXT",
                "rows_processed": "INTEGER",
                "status": "TEXT",
                "transformations_applied": "TEXT",
            },
            "primary_key": "(dataset_name, fetch_time)",
        },
        "task_registry": {
            "db": "meta",
            "columns": {
                "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "name": "TEXT NOT NULL",
                "status": "TEXT DEFAULT 'running'",
                "message": "TEXT DEFAULT ''",
                "progress": "REAL",
                "eta": "TEXT",
                "task_type": "TEXT DEFAULT 'indefinite'",
                "safe_to_exit": "INTEGER DEFAULT 1",
                "started_at": "TEXT NOT NULL",
                "updated_at": "TEXT",
                "expiry": "TEXT",
                "data": "TEXT DEFAULT '{}'",
            },
            "primary_key": "(id)",
        },
        "etf_blocklist": {
            "db": "meta",
            "columns": {
                "symbol": "TEXT",
                "added_date": "TEXT",
                "source": "TEXT",
            },
            "primary_key": "(symbol)",
        },
        "etf_sync_log": {
            "db": "meta",
            "columns": {
                "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "sync_date": "TEXT",
                "source": "TEXT",
                "count": "INTEGER",
                "status": "TEXT",
            },
            "primary_key": "(id)",
        },
        "sync_log": {
            "db": "meta",
            "columns": {
                "task_name": "TEXT",
                "last_run": "TEXT",
                "last_status": "TEXT",
                "error_message": "TEXT",
                "progress_pct": "REAL",
            },
            "primary_key": "(task_name)",
        },
        # ── institutional.db ──────────────────────────────────
        "fii_dii_history": {
            "db": "institutional",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "fii_pct": "REAL",
                "dii_pct": "REAL",
                "promoter_pct": "REAL",
                "pledged_pct": "REAL",
                "fii_change": "REAL",
                "dii_change": "REAL",
                "car_ratio": "REAL",
                "is_hidden_accumulation": "INTEGER DEFAULT 0",
            },
            "primary_key": "(symbol, date)",
        },
        "fii_dii_daily": {
            "db": "institutional",
            "columns": {
                "date": "TEXT",
                "fii_net_buy": "REAL",
                "dii_net_buy": "REAL",
                "source": "TEXT",
            },
            "primary_key": "(date)",
        },
        "institutional_owners": {
            "db": "institutional",
            "columns": {
                "symbol": "TEXT",
                "owner_name": "TEXT",
                "owner_type": "TEXT",
                "shares_held": "INTEGER",
                "pct_held": "REAL",
                "date": "TEXT",
            },
            "primary_key": "(symbol, owner_name, date)",
        },
        "large_deals": {
            "db": "institutional",
            "columns": {
                "symbol": "TEXT",
                "type": "TEXT",
                "client": "TEXT",
                "buy_sell": "TEXT",
                "qty": "INTEGER",
                "price": "REAL",
                "date": "TEXT",
                "client_name": "TEXT",
                "deal_type": "TEXT",
                "value": "REAL",
            },
            "primary_key": "",
        },
        "block_deals": {
            "db": "institutional",
            "columns": {
                "id": "INTEGER",
                "symbol": "TEXT",
                "date": "TEXT",
                "security_name": "TEXT",
                "client_name": "TEXT",
                "buy_sell": "TEXT",
                "quantity": "INTEGER",
                "price": "REAL",
                "trade_value": "REAL",
                "source": "TEXT",
            },
            "primary_key": "(id)",
        },
        "bulk_deals": {
            "db": "institutional",
            "columns": {
                "id": "INTEGER",
                "symbol": "TEXT",
                "date": "TEXT",
                "security_name": "TEXT",
                "client_name": "TEXT",
                "buy_sell": "TEXT",
                "quantity": "INTEGER",
                "price": "REAL",
                "trade_value": "REAL",
                "source": "TEXT",
            },
            "primary_key": "(id)",
        },
        "corporate_actions": {
            "db": "institutional",
            "columns": {
                "id": "INTEGER",
                "symbol": "TEXT",
                "date": "TEXT",
                "security_name": "TEXT",
                "action_type": "TEXT",
                "ex_date": "TEXT",
                "record_date": "TEXT",
                "source": "TEXT",
            },
            "primary_key": "(id)",
        },
        "insider_trades": {
            "db": "institutional",
            "columns": {
                "symbol": "TEXT",
                "acq_name": "TEXT",
                "category": "TEXT",
                "type": "TEXT",
                "mode": "TEXT",
                "value_cr": "REAL",
                "avg_price": "REAL",
                "date": "TEXT",
            },
            "primary_key": "",
        },
        # ── governance.db ─────────────────────────────────────
        "sast_disclosures": {
            "db": "governance",
            "columns": {
                "disclosure_id": "TEXT PRIMARY KEY",
                "symbol": "TEXT",
                "date": "TEXT",
                "acq_name": "TEXT",
                "qty_pct": "REAL",
                "type": "TEXT",
            },
            "primary_key": "(disclosure_id)",
        },
        "pledged_history": {
            "db": "governance",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "promoter_holding": "REAL",
                "pledged_pct": "REAL",
                "change_qoq": "REAL",
            },
            "primary_key": "(symbol, date)",
        },
        "shareholding_history": {
            "db": "governance",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "fii_pct": "REAL",
                "dii_pct": "REAL",
                "promoter_pct": "REAL",
            },
            "primary_key": "(symbol, date)",
        },
        "ias_history": {
            "db": "governance",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "ias_score": "REAL",
                "ias_rank": "REAL",
                "tags": "TEXT",
            },
            "primary_key": "(symbol, date)",
        },
        # ── calendar.db ───────────────────────────────────────
        "market_calendar": {
            "db": "calendar",
            "columns": {
                "date": "TEXT",
                "is_trading_day": "INTEGER",
                "holiday_name": "TEXT",
                "session_type": "TEXT",
            },
            "primary_key": "(date)",
        },
        # ── scoring.db ────────────────────────────────────────
        "fundamental_scores": {
            "db": "scoring",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "growth_score": "REAL",
                "quality_score": "REAL",
                "stability_score": "REAL",
                "risk_score": "REAL",
                "total_funda_score": "REAL",
                "grade": "TEXT",
            },
            "primary_key": "(symbol)",
        },
        "ranking_history": {
            "db": "scoring",
            "columns": {
                "symbol": "TEXT",
                "date": "TEXT",
                "rank_nifty500": "INTEGER",
                "rank_sector": "INTEGER",
            },
            "primary_key": "(symbol, date)",
        },
        # ── options.db ─────────────────────────────────────────
        "option_chain": {
            "db": "options",
            "columns": {
                "symbol": "TEXT",
                "kind": "TEXT",
                "spot": "REAL",
                "pcr": "REAL",
                "regime": "TEXT",
                "total_ce_oi": "INTEGER",
                "total_pe_oi": "INTEGER",
                "atm_strike": "REAL",
                "expiry": "TEXT",
                "strike_count": "INTEGER",
                "raw_json": "TEXT",
                "created_at": "TEXT",
            },
            "primary_key": "",
        },
        "pcr_snapshot": {
            "db": "options",
            "columns": {
                "index_symbol": "TEXT PRIMARY KEY",
                "pcr": "REAL",
                "regime": "TEXT",
                "spot": "REAL",
                "expiry": "TEXT",
                "updated_at": "TEXT",
            },
            "primary_key": "(index_symbol)",
        },
    }

    @classmethod
    def get_canonical_column(cls, raw_header: str) -> str:
        """Returns the canonical MYRA column name for a given raw header."""
        raw_clean = raw_header.strip()
        for canonical_key, variations in cls.COLUMN_MAPPINGS.items():
            if raw_clean in variations or raw_clean.upper() in [
                v.upper() for v in variations
            ]:
                return canonical_key
        return raw_clean.lower()

    @classmethod
    def validate_schema(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        """
        Runtime Schema Validation (PRIORITY 2.3).
        Validates if the SQLite table matches the registry.
        """
        if table_name not in cls.TABLES:
            return False

        try:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            existing_columns = {row[1]: row[2] for row in cursor.fetchall()}

            if not existing_columns:
                logger.warning(f"[SCHEMA_REGISTRY] Table {table_name} does not exist.")
                return False

            expected_columns = cls.TABLES[table_name]["columns"]

            # Auto-fix minor mismatches (add missing columns)
            missing = [
                (c, t) for c, t in expected_columns.items() if c not in existing_columns
            ]
            if missing:
                try:
                    conn.execute("BEGIN")
                    for col_name, col_type in missing:
                        logger.warning(
                            f"[SCHEMA_REGISTRY] Auto-fixing schema: Adding {col_name} ({col_type}) to {table_name}"
                        )
                        cursor.execute(  # noqa: PG-NPLUS1
                            f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                        )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error(f"[SCHEMA_REGISTRY] Failed to auto-fix schema: {e}")
                    return False

            # Check type mismatches
            cursor.execute(f"PRAGMA table_info({table_name})")
            updated_columns = {row[1]: row[2] for row in cursor.fetchall()}
            type_mismatch_found = False
            for col_name, expected_type in expected_columns.items():
                actual_type = updated_columns.get(col_name)
                if (
                    actual_type
                    and "INT" in expected_type
                    and "TEXT" in actual_type.upper()
                ):
                    logger.error(
                        f"[SCHEMA_REGISTRY] Type mismatch on {table_name}.{col_name}: Expected {expected_type}, Found {actual_type}"
                    )
                    type_mismatch_found = True

            conn.commit()
            if type_mismatch_found:
                return False
            return True
        except Exception as e:
            logger.error(f"[SCHEMA_REGISTRY] Validation error on {table_name}: {e}")
            return False
