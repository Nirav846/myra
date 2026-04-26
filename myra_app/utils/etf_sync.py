"""
MYRA ETF Sync Utility
Fetches the live ETF list from NSE and stores it in myra_metadata.db.
Runs automatically every Sunday via the background orchestrator.
"""
import os
import sqlite3
import logging
import time
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("myra.etf_sync")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_HERE, "db", "myra_metadata.db")

NSE_ETF_API = "https://www.nseindia.com/api/etf"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/exchange-traded-funds-etf",
}

# Seed list from NSE download dated 26-Apr-2026 (322 ETFs)
# This is used as fallback if the API is unreachable
SEED_ETF_SYMBOLS = {
    'SILVERBEES', 'LIQUIDBEES', 'NIFTYBEES', 'GOLDBEES', 'LIQUIDCASE',
    'BANKNIFTY1', 'ITBEES', 'LIQUIDIETF', 'LIQUID1', 'TATSILV', 'LIQUIDPLUS',
    'IT', 'TATAGOLD', 'HDFCSILVER', 'MODEFENCE', 'SILVERIETF', 'LIQUIDADD',
    'MON100', 'GOLDIETF', 'LIQUID', 'BANKBEES', 'HDFCSML250', 'CASHIETF',
    'SBISILVER', 'SETFGOLD', 'PHARMABEES', 'MOCAPITAL', 'JUNIORBEES',
    'SETFNIF50', 'HDFCGOLD', 'SBILIQETF', 'NIFTYIETF', 'PSUBNKBEES',
    'SILVERCASE', 'LIQUIDBETF', 'SILVER', 'MID150BEES', 'AONELIQUID',
    'METALIETF', 'NEXT50IETF', 'GOLDCASE', 'SILVER1', 'NIFTYBETA',
    'GROWWLIQID', 'MAFANG', 'SMALLCAP', 'CPSEETF', 'FMCGIETF', 'NIFTY1',
    'GOLDBETA', 'GOLD1', 'GOLDETF', 'PVTBANIETF', 'PSUBANKADD', 'ITIETF',
    'ITETF', 'MOREALTY', 'NEXT50', 'LIQUIDETF', 'ICICIB22', 'GROWWPOWER',
    'AXISGOLD', 'LIQGRWBEES', 'MOM30IETF', 'ESILVER', 'HNGSNGBEES',
    'GROWWDEFNC', 'SILVERBETA', 'BANKIETF', 'MOSMALL250', 'NIFTYETF',
    'BSE500IETF', 'SETFNIFBK', 'MIDCAPETF', 'ITADD', 'BANKBETA',
    'PVTBANKADD', 'GROWWSLVR', 'AUTOBEES', 'METAL', 'LTGILTBEES', 'ENERGY',
    'ALPHA', 'OILIETF', 'AXISILVER', 'HDFCNIFTY', 'SBIBPB', 'BSLGOLDETF',
    'GROWWGOLD', 'MOMENTUM50', 'MOSILVER', 'MID150CASE', 'NIF100BEES',
    'SENSEXIETF', 'SILVERAG', 'MIDCAPIETF', 'SILVERADD', 'SETFNN50',
    'TOP10ADD', 'MIDSMALL', 'LOWVOLIETF', 'ALPL30IETF', 'MAKEINDIA',
    'ALPHAETF', 'LICMFGOLD', 'HDFCMID150', 'MONIFTY500', 'AUTOIETF',
    'MOVALUE', 'NIFTYCASE', 'BSLNIFTY', 'GOLDADD', 'MASPTOP50',
    'HDFCLIQUID', 'MONQ50', 'GILT5YBEES', 'SBIETFIT', 'DEFENCE',
    'SMALL250', 'HDFCNIFIT', 'MOENERGY', 'GROWWRAIL', 'FINIETF', 'EVIETF',
    'ABSLPSE', 'MOMIDMTM', 'HEALTHIETF', 'TECH', 'INFRAIETF', 'PSUBANK',
    'EBBETF0430', 'CONSUMBEES', 'SML100CASE', 'INFRABEES', 'LIQUIDSBI',
    'HSBCGOLD', 'PSUBNKIETF', 'AXISNIFTY', 'GROWWMETAL', 'TNIDETF',
    'EVINDIA', 'TOP100CASE', 'NIF100IETF', 'VAL30IETF', 'HEALTHY',
    'MOMENTUM30', 'MIDCAP', 'AXISVALUE', 'LIQUIDSHRI', 'HDFCGROWTH',
    'NV20IETF', 'BANKETF', 'HDFCSENSEX', 'GROWWCAPM', 'TOP20',
    'HDFCNEXT50', 'GROWWNIFTY', 'BFSI', 'COMMOIETF', 'NEXT50BETA',
    'SBINEQWETF', 'GROWWHOSPI', 'MOGOLD', 'GOLDBND', 'GROWWEV',
    'AONEGOLD', 'EGOLD', 'SHARIABEES', 'AXISTECETF', 'MIDSELIETF',
    'AONENIFTY', 'MULTICAP', 'QGOLDHALF', 'CHEMICAL', 'EQUAL50ADD',
    'MOM50', 'BBETF0432', 'IVZINGOLD', 'DIVOPPBEES', 'SENSEXBETA',
    'NIFTY100EW', 'SBIETFPB', 'NIFTYADD', 'SENSEXETF', 'HDFCPSUBK',
    'HDFCNIFBAN', 'NEXT50ETF', 'INTERNET', 'ELIQUID', 'EMULTIMQ',
    'HDFCMOMENT', 'NV20BEES', 'MAHKTECH', 'SILVERBND', 'AONESILVER',
    'TOP15IETF', 'GROWWSC250', 'GROWWNET', 'SILVER360', 'MOHEALTH',
    'ABSLLIQUID', 'CHOICEGOLD', 'FLEXIADD', 'MOMNC', 'AONETOTAL',
    'HDFCBSE500', 'BANKPSU', 'MID150', 'CONSUMIETF', 'MOMOMENTUM',
    'ITBETA', 'MNC', 'QNIFTY', 'EQUAL200', 'EBBETF0433', 'EQUAL50',
    'ESG', 'GROWWRLTY', 'AXISBPSETF', 'QUAL30IETF', 'VALUE',
    'GROWWMOM50', 'MIDQ50ADD', 'SBIMIDMOM', 'ELM250', 'LOWVOL1',
    'ECAPINSURE', 'HDFCPVTBAN', 'SNXT30BEES', 'DIVIDEND', 'LICNETFGSC',
    'SMALLADD', 'HDFCNIF100', 'AXISHCETF', 'UNIONGOLD', 'HEALTHCARE',
    'GROWWCHEM', 'MIDCAPADD', 'ABSL10BANK', 'SBINMID150', 'NIFTYBETF',
    'MOMENTUM', 'MIDCAPBETA', 'EBANKNIFTY', 'EBBETF0431', 'MOTOUR',
    'BANKADD', 'GOLD360', 'LTGILTCASE', 'NV20', 'ABSLBANETF', 'BANKBETF',
    'HDFCVALUE', 'CONS', 'BBNPPGOLD', 'NIFTYQLITY', 'MSCIINDIA',
    'NEXT50ADD', 'CONSUMER', 'MOALPHA50', 'GROWWPSE', 'NETF', 'MOIPO',
    'MANUFGBEES', 'INFRA', 'ABSLNN50ET', 'AXISBNKETF', 'AONETMMQ50',
    'GROWWPSUBK', 'MOGSEC', 'MOPSE', 'SBIETFCON', 'SELECTIPO',
    'AXSENSEX', 'SETF10GILT', 'GROWWNXT50', 'HEALTHADD', 'HDFCLOWVOL',
    'NEXT30ADD', 'HDFCQUAL', 'MON50EQUAL', 'SBIETFQLTY', 'MOBANK10',
    'ENIFTY', 'TWCGOLDETF', 'GROWWN200', 'GROWWLOVOL', 'BSLSENETFG',
    'GSEC5IETF', 'MONEXT50', 'SNXT50BETA', 'ESENSEX', 'SDL26BEES',
    'SENSEXADD', 'MOINFRA', 'GSEC10IETF', 'LICNFNHGP', 'IDFNIFTYET',
    'GSEC10YEAR', 'IVZINNIFTY', 'MONIFTY100', 'BANK10ADD', 'AXISCETF',
    'LICNMID100', 'MOQUALITY', 'NPBET', 'MOSERVICE', 'LICNETFN50',
    'QUALITY30', 'LICNETFSEN', 'BBNPNBETF', 'LOWVOL', 'GILT5BETA',
    'MOLOWVOL', 'GROWWMC150', 'MOMGF', 'ABGSEC', 'MSCIADD',
    'GSEC10ABSL', 'ABSLMSCIN', 'GILT10BETA'
}


def _ensure_etf_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_blocklist (
            symbol      TEXT PRIMARY KEY,
            added_date  TEXT,
            source      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_date   TEXT,
            source      TEXT,
            count       INTEGER,
            status      TEXT
        )
    """)
    conn.commit()


def get_etf_symbols() -> set:
    """
    Returns current ETF blocklist from DB.
    Falls back to SEED_ETF_SYMBOLS if DB is unavailable.
    """
    try:
        if not os.path.exists(_DB_PATH):
            return SEED_ETF_SYMBOLS
        with sqlite3.connect(_DB_PATH, timeout=10) as conn:
            _ensure_etf_table(conn)
            rows = conn.execute("SELECT symbol FROM etf_blocklist").fetchall()
            if rows:
                return {r[0] for r in rows}
    except Exception as e:
        logger.warning(f"ETF DB read failed, using seed list: {e}")
    return SEED_ETF_SYMBOLS


def _fetch_from_nse() -> set:
    """Fetches live ETF list from NSE API."""
    session = requests.Session()
    # NSE requires a cookie from the main page first
    session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    time.sleep(1)
    resp = session.get(NSE_ETF_API, headers=NSE_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # NSE returns {"data": [{"symbol": "...", ...}, ...]}
    symbols = {item["symbol"].strip().upper() for item in data.get("data", [])}
    return symbols


def sync_etf_list(force: bool = False) -> bool:
    """
    Syncs ETF list from NSE API into myra_metadata.db.
    Runs weekly — skips if last sync was less than 6 days ago unless force=True.
    Returns True if sync happened, False if skipped.
    """
    try:
        with sqlite3.connect(_DB_PATH, timeout=10) as conn:
            _ensure_etf_table(conn)

            if not force:
                last = conn.execute(
                    "SELECT sync_date FROM etf_sync_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if last:
                    from datetime import date
                    last_date = date.fromisoformat(last[0])
                    days_since = (date.today() - last_date).days
                    if days_since < 6:
                        logger.info(f"ETF sync skipped — last sync {days_since} days ago")
                        return False

            # Try live NSE API first
            today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
            try:
                symbols = _fetch_from_nse()
                source = "nse_api"
                logger.info(f"Fetched {len(symbols)} ETFs from NSE API")
            except Exception as e:
                logger.warning(f"NSE API fetch failed ({e}), using seed list")
                symbols = SEED_ETF_SYMBOLS
                source = "seed_fallback"

            # Upsert into DB
            conn.executemany(
                "INSERT OR REPLACE INTO etf_blocklist (symbol, added_date, source) VALUES (?, ?, ?)",
                [(s, today, source) for s in symbols]
            )
            conn.execute(
                "INSERT INTO etf_sync_log (sync_date, source, count, status) VALUES (?, ?, ?, ?)",
                (today, source, len(symbols), "ok")
            )
            conn.commit()
            print(f"[MYRA ETF] Synced {len(symbols)} ETF symbols from {source}")
            return True

    except Exception as e:
        logger.error(f"ETF sync failed: {e}")
        return False


def purge_etf_rows_from_technical_db(dry_run: bool = False) -> int:
    """
    Removes any ETF rows that slipped into technical_data.
    Called by db_doctor after ETF sync.
    Returns count of rows removed.
    """
    from myra_app.librarian_core import LibrarianCore
    tech_db = os.path.join(_HERE, "db", LibrarianCore.DB_MAP["technical"])

    etf_symbols = get_etf_symbols()
    if not etf_symbols:
        return 0

    try:
        with sqlite3.connect(tech_db, timeout=30) as conn:
            placeholders = ",".join("?" * len(etf_symbols))
            count = conn.execute(
                f"SELECT COUNT(*) FROM technical_data WHERE symbol IN ({placeholders})",
                list(etf_symbols)
            ).fetchone()[0]

            if count == 0:
                return 0

            if dry_run:
                print(f"  [DRY RUN] Would purge {count} ETF rows from technical_data")
                return count

            conn.execute(
                f"DELETE FROM technical_data WHERE symbol IN ({placeholders})",
                list(etf_symbols)
            )
            conn.commit()
            print(f"  [FIXED] Purged {count} ETF rows from technical_data")
            return count
    except Exception as e:
        logger.error(f"ETF purge failed: {e}")
        return 0
