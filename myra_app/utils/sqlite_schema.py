"""Explicit Polars schemas for SQLite reads.

Why this module exists
----------------------
``pl.read_database(query, sqlite_connection)`` has no declared types to read.
The DBAPI cursor description carries column *names* but not types, so Polars
infers each column's dtype from the Python values in a leading sample of rows.
For MYRA that sample is routinely unrepresentative, because ``technical_data``
is wide and sparse and its derived columns fill in only as history accumulates.
Inference therefore commits to the wrong builder and then dies on the first
value outside the sample:

    ComputeError: could not append value: 40.0 of type: f64 to the builder;
    make sure that all rows have the same schema or consider increasing
    ``infer_schema_length``

Measured on the live database (2026-10-17, Polars 1.40.1, three symbols whose
leading rows are the oldest ones): the default call fails. Raising
``infer_schema_length`` only moves the sample, and ``None`` ("scan everything")
is worse for a second, quieter reason - columns that are *entirely* NULL in the
result set come back as Polars dtype ``Null``, which then misbehaves in
comparisons, joins and ``to_pandas()``. Seventeen of the 43 ``technical_data``
columns came back ``Null`` that way.

The fix is to stop inferring. SQLite does record a declared type per column
(``PRAGMA table_info``), and that is a better source of truth than a sample:
it is exact, it is stable across runs, and it cannot drift when
``SchemaRegistry`` adds a column (which is precisely how a hand-maintained
dictionary in a call site goes stale).

Why INTEGER columns map to ``Int64`` rather than ``Float64``
-----------------------------------------------------------
SQLite is dynamically typed, so a declared-INTEGER column can in principle
hold a fractional value and a lossy ``Int64`` mapping would silently truncate
it. That is not the case here, and this was checked rather than assumed: a full
scan of all eleven INTEGER-declared columns in ``technical_data``
(8,022,725 rows) found **zero** non-integral values, and SQLite's INTEGER
affinity converts a stored ``40.0`` to ``40`` on write in the first place. The
remaining INTEGER columns are counts and flags. ``Int64`` is therefore exact,
and it matches what the previously-working ``infer_schema_length=None`` call
produced, so no downstream type expectations change.
"""

from __future__ import annotations

from typing import Iterable, Optional

import polars as pl


def _quote_identifier(name: str) -> str:
    """Quote a SQLite identifier for interpolation into a statement."""
    if not name or "\x00" in name:
        raise ValueError("invalid SQLite identifier")
    return '"' + name.replace('"', '""') + '"'


def declared_type_to_polars(declared: Optional[str]) -> pl.DataType:
    """Map a SQLite declared type to the Polars dtype that reads it losslessly.

    Follows SQLite's own affinity rules (the declared type is matched as text,
    case-insensitively, on substrings) so the mapping agrees with how SQLite
    itself interprets the column. Unknown or absent declarations fall back to
    ``String``: a conservative choice that can render a number as text, but can
    never silently reinterpret text as a number.
    """
    text = (declared or "").strip().upper()
    if "INT" in text:  # INTEGER affinity, incl. "BIGINT", "UNSIGNED INT"
        return pl.Int64
    if any(token in text for token in ("CHAR", "CLOB", "TEXT", "TIME", "DATE")):
        # DATETIME / TIMESTAMP / DATE: SQLite has no datetime type and MYRA
        # stores these as ISO-like text (SchemaRegistry uses TEXT DDL).
        return pl.String
    if "BLOB" in text:
        return pl.Binary
    if any(token in text for token in ("REAL", "FLOA", "DOUB")):
        return pl.Float64
    if "BOOL" in text:
        # SQLite has no boolean type; MYRA stores flags as 0/1 integers.
        return pl.Int64
    if any(token in text for token in ("NUMERIC", "DECIMAL")):
        return pl.Float64
    return pl.String


def sqlite_schema_overrides(
    connection,
    table: str,
    columns: Optional[Iterable[str]] = None,
) -> "dict[str, pl.DataType]":
    """Explicit ``schema_overrides`` for reading ``table`` with Polars.

    ``columns`` narrows the mapping to the columns a query actually returns;
    passing a name the query does not select makes Polars raise, so a caller
    reading a projection must declare it.

    Raises ``ValueError`` for a table with no declared columns rather than
    returning ``{}``: an empty mapping would silently restore Polars' inference,
    which is the failure this module exists to remove.
    """
    pragma = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table)})"
    ).fetchall()
    if not pragma:
        raise ValueError(
            f"no columns found for table {table!r}; refusing to fall back to "
            "Polars schema inference"
        )

    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    declared = {row[1]: row[2] for row in pragma}
    if columns is None:
        selected = declared
    else:
        wanted = set(columns)
        selected = {name: declared[name] for name in declared if name in wanted}

    return {
        name: declared_type_to_polars(sqlite_type)
        for name, sqlite_type in selected.items()
    }


__all__ = ["declared_type_to_polars", "sqlite_schema_overrides"]
