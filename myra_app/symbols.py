"""Symbol-shape helpers shared by the fundamentals refresh path.

Pure string logic: imports nothing from myra_app, no sqlite3, no yfinance.
Both halves of the rule (the SQL predicate and the Python predicate) live here
so the SQL used by the stale query and the Python helper used by tests cannot
quietly drift -- tests/test_fundamentals_stale_filter.py cross-checks them.

Context: NSE SME (small/medium enterprise) series symbols are carried in the
fundamentals table as "<BASE>_SME" alongside their base-series twin, so the
same company appears twice.  symbols_master contains 0 of the 527 _SME symbols
-- the base row is already the ranked representation -- which is why the _SME
copy is only ever used to source an identical metric (shares_outstanding), never
to supply a distinct one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

SME_SUFFIX = "_SME"

# Rows that are not fetchable equities: index pseudo-symbols carry either an
# embedded space ("NIFTY 50") or an NIFTY/NIF prefix with no space ("NIFTYBEES").
# Parentheses are load-bearing -- FundamentalSync._refresh_stale_shares_outstanding
# in fundamental_sync.py interpolates this fragment into an "AND NOT {NON_EQUITY_SQL}"
# clause of its (otherwise inline) stale-shares query, and without the parentheses
# SQLite binds that AND NOT to the last OR term only.
NON_EQUITY_SQL = "(symbol LIKE '% %' OR symbol LIKE 'NIFTY%' OR symbol LIKE 'NIF%')"


def strip_sme(symbol: str) -> str:
    """Return the base-series ticker for a symbol ("FIXPAIR1_SME" -> "FIXPAIR1")."""
    if symbol.endswith(SME_SUFFIX):
        return symbol[: -len(SME_SUFFIX)]
    return symbol


def is_sme_symbol(symbol: str) -> bool:
    """True when the symbol is an SME-series row (ends with "_SME")."""
    return symbol.endswith(SME_SUFFIX)


def is_non_equity_symbol(symbol: str) -> bool:
    """Python twin of NON_EQUITY_SQL -- must agree branch for branch."""
    return " " in symbol or symbol.startswith("NIFTY") or symbol.startswith("NIF")


@dataclass(frozen=True)
class DedupeResult:
    """Outcome of collapsing SME/base rows down to one fetch per ticker.

    order      distinct stripped tickers, first-seen order preserved from the
               input.  The input order is stale-query order (rowid order
               unless an ORDER BY is present), so it is NOT stable -- assert on
               counts and set membership, never on position.
    row_for    one entry per entry in .order: stripped ticker -> the row that
               was picked as the write target.  Prefers the non-_SME row among
               the input rows for that ticker, falling back to the _SME row
               when no non-_SME row is present.
    duplicates len(input) - len(.order)
    total      len(input), raw pre-dedupe count.  Deliberately NOT len(.order):
               the caller reports the raw row count it was asked to fix.
    """

    order: list[str]
    row_for: dict[str, str]
    duplicates: int
    total: int


def dedupe_tickers(symbols: Sequence[str]) -> DedupeResult:
    """Collapse {ticker, ticker_SME} pairs to one Yahoo fetch each.

    The write-target choice is made here rather than at the call site so every
    caller inherits the same preference (non-_SME first, _SME fallback).
    """
    order: list[str] = []
    row_for: dict[str, str] = {}
    for symbol in symbols:
        ticker = strip_sme(symbol)
        chosen = row_for.get(ticker)
        if chosen is None:
            order.append(ticker)
            row_for[ticker] = symbol
        elif is_sme_symbol(chosen) and not is_sme_symbol(symbol):
            # Base twin arrived after the SME row -- prefer it, it is the row
            # the ranked surface actually reads.
            row_for[ticker] = symbol
    return DedupeResult(
        order=order,
        row_for=row_for,
        duplicates=len(symbols) - len(order),
        total=len(symbols),
    )
