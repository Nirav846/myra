"""Authoritative sources for the v2.5 positional scorer's cross-sectional inputs.

Why this module exists
----------------------
``myra_app.score_components_v2.precompute_ranks`` turns five per-symbol inputs
into percentile ranks that feed the v2.5 positional score::

    roe              -> _rank_roe
    ProfitGrowth     -> _rank_growth
    avg_delivery_20d -> _rank_delivery
    avg_volume_20d   -> _rank_volume
    smart_money_score-> _rank_sm_score

An audit (Phase 6, see ``docs/PHASE6_LIVE_RANKING_INTEGRATION.md``) found that
three of those inputs had **no producer anywhere in the repository** — they were
only ever *read* with a ``0`` default — while the other two existed in
``fundamentals`` but were never plumbed to the scorer. This module provides the
legitimate, already-ingested sources for the metrics that genuinely exist, so a
caller can populate the scorer's inputs without inventing data:

* ``avg_volume_20d``   <- ``technical_data.volume`` (20-session mean, shares)
* ``avg_delivery_20d`` <- ``technical_data.delivery_pct`` (20-session mean, %)
* ``roe``              <- ``fundamentals.roe`` (%)
* ``ProfitGrowth``     <- ``fundamentals.profit_growth`` (%)

``smart_money_score`` has no defensible existing definition and is therefore
*deliberately not* produced here; see the phase doc for the blocker.

Everything here is opt-in. The default production ordering path is unchanged;
``load_universe`` only merges these inputs when explicitly asked (or when the
``MYRA_V25_RANKING_INPUTS`` environment variable is enabled).

Definitions and windows (do not guess these)
--------------------------------------------
* Both 20-day windows are **20 trading sessions** (row count over
  ``technical_data``), *not* 20 calendar days. This matches the established
  in-repo computations (``myra_app/ml_trainer.py`` ``AVG(delivery_pct) OVER
  w20``, ``launchpad_labels._rolling_avg_volume(period=20)``).
* ``avg_delivery_20d`` is the mean of the daily **delivery percentage**
  (``DELIV_PER``). It is *not* a delivery quantity and *not*
  ``delivery_ratio``; conflating those is the documented failure mode this
  module avoids.
* If ``delivery_pct`` is missing for a session but ``delivery`` and ``volume``
  are present, the percentage is derived as ``delivery * 100 / volume`` — the
  same derivation the repo's own repair tools use
  (``tools/db_doctor.py``, ``myra_app/mass_backfill.py``).
* A symbol needs a full window of sessions before either mean is emitted;
  otherwise the metric is left absent (``None``) so the scorer applies its
  neutral fallback rather than ranking a two-session average as a 20-day one.
"""

from __future__ import annotations

import os
from statistics import mean

#: Window length, in trading sessions (rows), matching the established 20-day
#: computations elsewhere in the repo.
DEFAULT_WINDOW = 20

#: Max symbols per ``IN (...)`` clause, to stay well under SQLite's bound
#: parameter limit (3000+ symbol scans would otherwise exceed it).
_SYMBOL_CHUNK = 500

#: Environment switch read by :func:`ranking_inputs_enabled` when a caller does
#: not pass an explicit flag. Unset/``0`` keeps the current production ordering.
ENV_FLAG = "MYRA_V25_RANKING_INPUTS"

#: Columns merged into each result row; kept here so the shadow comparison and
#: the scorer agree on spelling. ``roe`` is lowercase on purpose — that is the
#: key ``precompute_ranks`` reads (the uppercase ``ROE`` key is display only).
METRIC_KEYS = ("roe", "ProfitGrowth", "avg_delivery_20d", "avg_volume_20d")


def ranking_inputs_enabled(explicit: bool | None = None) -> bool:
    """Resolve the opt-in flag: explicit argument wins, else the env var.

    The default (no argument, no env var) is **False** — the production
    ordering path stays exactly as it was before Phase 6.
    """
    if explicit is not None:
        return bool(explicit)
    return os.environ.get(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _query_rows(conn, sql, params):
    """Run one query and return a list of column-name-keyed dicts."""
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, raw)) for raw in cur.fetchall()]


def _delivery_pct_expr(row):
    """Daily delivery percentage for one ``technical_data`` row (or ``None``).

    Prefers the ingested ``delivery_pct`` (NSE ``DELIV_PER``); derives it from
    ``delivery``/``volume`` only when absent, mirroring the repo's repair tools.
    """
    raw = row.get("delivery_pct")
    if raw is not None:
        return float(raw)
    delivery = row.get("delivery")
    volume = row.get("volume")
    if delivery is not None and volume:
        return float(delivery) * 100.0 / float(volume)
    return None


def compute_ranking_inputs(
    tech_conn,
    symbols,
    as_of_date=None,
    window: int = DEFAULT_WINDOW,
):
    """Per-symbol 20-session volume / delivery means from ``technical_data``.

    Parameters
    ----------
    tech_conn:
        A connection to ``myra_technical.db`` (the ``technical_data`` table).
    symbols:
        Symbols to compute for (matched case-insensitively on ``symbol``).
    as_of_date:
        Optional ``YYYY-MM-DD`` cutoff. When given, only sessions on or before
        it are used, so a historical/backtest scan can never see future rows.
    window:
        Number of most-recent sessions to average (default 20).

    Returns
    -------
    dict[str, dict]
        ``{symbol: {"avg_volume_20d", "avg_delivery_20d", "sessions",
        "volume_obs", "delivery_obs"}}``. ``avg_volume_20d`` /
        ``avg_delivery_20d`` are ``None`` when fewer than ``window`` sessions
        are available, or when the metric has no non-null observation in the
        window. Keys are upper-cased to match the ``Stock`` field on results.
    """
    syms = [str(s).upper() for s in symbols or []]
    if not syms or tech_conn is None or window < 1:
        return {}

    lower_date, upper_date = _resolve_window_bounds(tech_conn, as_of_date, window)
    if lower_date is None or upper_date is None:
        return {}

    raw_rows = [
        row
        for chunk in _chunks(syms, _SYMBOL_CHUNK)
        for row in _fetch_window_rows(tech_conn, chunk, lower_date, upper_date, window)
    ]

    buckets: dict[str, list[dict]] = {}
    for row in raw_rows:
        sym = str(row["symbol"]).upper()
        buckets.setdefault(sym, []).append(row)  # noqa: PG-APPEND

    out: dict[str, dict] = {}
    for symbol, rows in buckets.items():
        sessions = len(rows)
        volumes = [float(r["volume"]) for r in rows if r.get("volume") is not None]
        deliveries = [d for d in (_delivery_pct_expr(r) for r in rows) if d is not None]

        full_window = sessions >= window
        out[symbol] = {
            "avg_volume_20d": (mean(volumes) if full_window and volumes else None),
            "avg_delivery_20d": (
                mean(deliveries) if full_window and deliveries else None
            ),
            "sessions": sessions,
            "volume_obs": len(volumes),
            "delivery_obs": len(deliveries),
        }
    return out


def compute_fundamental_inputs(val_conn, symbols):
    """Per-symbol ``roe`` / ``ProfitGrowth`` from the ``fundamentals`` table.

    Values are passed through exactly as stored (``0`` is kept as ``0`` — the
    invariant is that a stored metric is never replaced by a sentinel); only a
    SQL ``NULL`` becomes ``None``, which the scorer treats as "no data".
    """
    syms = [str(s).upper() for s in symbols or []]
    if not syms or val_conn is None:
        return {}

    raw_rows = [
        row
        for chunk in _chunks(syms, _SYMBOL_CHUNK)
        for row in _fetch_fundamental_rows(val_conn, chunk)
    ]
    return {
        str(row["symbol"]).upper(): {
            "roe": None if row["roe"] is None else float(row["roe"]),
            "ProfitGrowth": (
                None if row["profit_growth"] is None else float(row["profit_growth"])
            ),
        }
        for row in raw_rows
    }


def _resolve_window_bounds(tech_conn, as_of_date, window):
    """Inclusive ``(lower_date, upper_date)`` spanning ``window`` sessions.

    Returns ``(None, None)`` when the table (or the ``as_of_date`` slice of it)
    has no sessions at all. Resolving the real trading dates -- rather than
    filtering only by ``symbol`` -- lets SQLite use the ``idx_tech_symbol_date``
    index and touch only ~``window`` dates of rows per symbol instead of the
    whole history (measured ~110x faster on a 2 GB / 8 M-row DB).
    """
    if as_of_date:
        upper_date = tech_conn.execute(
            "SELECT MAX(date) FROM technical_data WHERE date <= ?", (as_of_date,)
        ).fetchone()[0]
    else:
        upper_date = tech_conn.execute(
            "SELECT MAX(date) FROM technical_data"
        ).fetchone()[0]
    if upper_date is None:
        return (None, None)
    lower_date = tech_conn.execute(
        "SELECT MIN(date) FROM ("
        "  SELECT DISTINCT date FROM technical_data"
        "  WHERE date <= ? ORDER BY date DESC LIMIT ?"
        ")",
        (upper_date, int(window)),
    ).fetchone()[0]
    return (lower_date, upper_date)


def _fetch_window_rows(tech_conn, chunk, lower_date, upper_date, window):
    """The ``window`` most recent sessions for one chunk of symbols."""
    placeholders = ",".join(["?"] * len(chunk))
    sql = (
        "SELECT symbol, volume, delivery, delivery_pct FROM ("
        "  SELECT symbol, volume, delivery, delivery_pct,"
        "         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn"
        "  FROM technical_data"
        f"  WHERE symbol IN ({placeholders})"
        "    AND date >= ? AND date <= ?"
    )
    params: list = list(chunk) + [lower_date, upper_date]
    sql += ") WHERE rn <= ?"
    params.append(int(window))
    return _query_rows(tech_conn, sql, params)


def _fetch_fundamental_rows(val_conn, chunk):
    """``roe`` / ``profit_growth`` for one chunk of symbols."""
    placeholders = ",".join(["?"] * len(chunk))
    sql = (
        "SELECT symbol, roe, profit_growth FROM fundamentals "
        f"WHERE symbol IN ({placeholders})"
    )
    return _query_rows(val_conn, sql, list(chunk))


def _merge_row(row, inputs, symbol_key):
    """Copy ``row`` and merge whichever inputs it has (never overwrite with None)."""
    new_row = dict(row)
    provided = inputs.get(str(new_row.get(symbol_key, "")).upper())
    if provided:
        for key in METRIC_KEYS:
            value = provided.get(key)
            if value is not None:
                new_row[key] = value
    return new_row


def attach_ranking_inputs(results, inputs, symbol_key="Stock"):
    """Return a **new** list of result dicts with the available metrics merged.

    Pure: the caller's rows are copied, never mutated, and metrics absent from
    ``inputs`` are left untouched (so this can never overwrite a real value with
    ``None``/``0``). Symbols are aligned on ``symbol_key`` (result rows use
    ``Stock``; ``funda_map`` values use ``symbol``), upper-cased.
    """
    return [_merge_row(row, inputs, symbol_key) for row in results]
