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
        "technical_data": {
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
        "prices": {
            "status": "DEPRECATED",
            "migration_note": "Legacy High-Speed Cache. Do not query directly. Target migration in v4.0.",
            "columns": {
                "symbol": "VARCHAR",
                "date": "DATE",
                "open": "DOUBLE",
                "high": "DOUBLE",
                "low": "DOUBLE",
                "close": "DOUBLE",
                "volume": "BIGINT",
                "delivery_qty": "BIGINT",
                "delivery_percent": "DOUBLE",
                "exchange": "VARCHAR",
            },
            "primary_key": "(symbol, date, exchange)",
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

            expected_columns = cls.TABLES[table_name]["columns"]  # noqa: PG-CHAINED

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
                # Note SQLite type affinity makes exact matching fuzzy, but we log severe differences
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
