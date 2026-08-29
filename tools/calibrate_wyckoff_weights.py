"""
Wyckoff Spring quality-score weight calibration
================================================

Calibrates the six `DEFAULT_SPRING_WEIGHTS` that feed the Wyckoff Spring
``spring_score`` against forward returns, using the SAME event data flow as
``tools/backtest_wyckoff.py`` (reused by module import, not exec).

CRITICAL DISTINCTION (why we calibrate on ``spring_score``, NOT ``quality``)
----------------------------------------------------------------------------
The six tuned weights (``delivery_absorption``, ``lower_wick``,
``close_location``, ``grab_depth``, ``equal_low_bonus``, ``two_candle_bonus``)
scale the components that sum into ``spring_score``. They do NOT control the
``e["quality"]`` field: for Spring events ``quality`` comes from
``_event_quality("Spring", ...)`` = ``del/75*50 + rec/5*50`` — a DIFFERENT
formula. ``tools/backtest_wyckoff.py``'s Q5-Q1 currently measures ``quality``,
so calibrating against it would measure a metric the weights don't control.
Therefore every Q5-Q1 / win-rate / Sharpe metric here is computed on
``spring_score`` (the overridden per-weight-set score) vs forward return,
restricted to Spring events only.

Methodology
-----------
1. Symbol universe + sampling match the harness exactly: mcap 510-530,000 Cr
   universe via ``WyckoffAutomaton._get_universe()``, ``random.seed(42)`` +
   ``random.sample(universe, --n-symbols)`` (default 400).
2. Scan dates: 12 evenly spaced dates over [2025-07-01, min(2026-04-30,
   max_tech_date - max(horizon, 180)d)] — identical to the harness guard.
3. ONE probe collection pass: ``WyckoffAutomaton(weights=PROBE_WEIGHTS)``
   (every weight at the top of its search range). Because ``spring_score`` is
   monotone non-decreasing in every weight, the probe scan emits a SUPERSET of
   the Spring events any candidate weight-set would emit — including near-D
   candidates — so no candidate can be missed. Per Spring event the raw scoring
   inputs are captured (delivery absorption ``del_abs``, lower-wick ratio, close
   location, grab depth, equal-low flag, two-candle flag) reconstructed from the
   same per-symbol DataFrame the scanner used (grab-candle index derived from
   ``event_date``; confirmed Springs are dated on the confirmation candle
   ``abs_i + 1``).
4. Per weight-set the scanner's OWN static helpers
   (``_delivery_absorption_score``, ``_lower_wick_score``,
   ``_close_location_score``, ``_grab_depth_score``, ``_compute_spring_score``)
   recompute ``spring_score`` for every probe candidate, the grade-D dropout
   (score < 35) is re-applied, and the surviving events are exactly the set the
   real ``WyckoffAutomaton(weights=...)`` would emit. A parity check re-runs the
   real scanner for the selected + default weight-sets and compares emitted
   (symbol, event_date) sets and scores.
5. No look-ahead: the probe detection reuses the production
   ``_detect_events`` unchanged, whose baselines are expanding
   (rolling-to-signal-day) series — a signal only sees data up to its own
   candle. Forward returns are measured from each event's own ``event_date``.
6. Chronological split by SCAN DATE (no leakage): first ~70% of scan dates =
   TRAIN, next ~15% = VALIDATION, last ~15% = leave-out HOLDOUT. Train events
   only use data up to their scan; validation dates are strictly later. The
   search selects on TRAIN only; validation is scored once for the selected set
   (plus every searched set, saved to the CSV); the holdout is reported for the
   default + selected sets only and never influences selection.
7. Metric (per split, Spring events only, horizon default 120 calendar days,
   cost-adjusted net return = 0.5% brokerage x2 + 15% STCG on gains, via
   ``backtest_wyckoff.build_forward_row``):
   - Q5-Q1: ``pd.qcut(spring_score, q=5, duplicates="drop")`` -> mean net return
     of the top bucket minus the bottom bucket (requires >= 5 non-empty buckets;
     combos that collapse to fewer buckets get an invalid score).
   - top-quintile win rate: % of top-bucket events with net return > 0.
   - Sharpe: ``mean/std`` of top-bucket net returns, annualized by
     ``sqrt(365 / horizon)`` (each event return spans ``horizon`` calendar days).
   - selection composite on TRAIN: ``Q5-Q1 + 0.5 * win_rate/100`` exactly.
8. Search space (per the brief): ``delivery_absorption`` 0-50 step 5,
   ``lower_wick`` 0-50 step 5, ``close_location`` 0-30 step 5,
   ``grab_depth`` 0-20 step 2, ``equal_low_bonus`` 0-15 step 2,
   ``two_candle_bonus`` 0-10 step 2. The four BASE weights are normalised to sum
   100 (mirroring the scanner's clamp, which then caps the total at 100); the
   two bonuses are added on top unnormalised. Duplicate base ratios collapse to
   the same normalised tuple. ``--search random`` (default, deterministic via an
   independent ``random.Random(42)``; ``--n-combos`` default 800) or
   ``--search grid`` (exhaustive product of all ranges — several hundred
   thousand combos, slower).
9. Decision gate (weight-optimisation variant): PROCEED iff the selected set's
   VALIDATION Q5-Q1 > 0 AND beats the DEFAULT weights' validation Q5-Q1.
   Otherwise ABANDON (current weights already optimal). On PROCEED the scanner's
   ``DEFAULT_SPRING_WEIGHTS`` are updated to the calibrated set; on ABANDON they
   stay.

Output
------
- ``tools/calibrate_wyckoff_weights_output.csv`` (gitignored) — every evaluated
  weight-set with TRAIN + VALIDATION metrics (n, Q5-Q1, top-quintile win rate,
  Sharpe, composite), plus ``is_default`` / ``is_best`` flags.
- Stdout: split table, best weights, train/validation metrics,
  compare-vs-default validation Q5-Q1 gap, and a PROCEED / ABANDON
  recommendation.

Usage
-----
    python tools/calibrate_wyckoff_weights.py                # full default run
    python tools/calibrate_wyckoff_weights.py --smoke        # fast default-only
    python tools/calibrate_wyckoff_weights.py --search random --n-combos 50 --smoke
    python tools/calibrate_wyckoff_weights.py --search grid
    python tools/calibrate_wyckoff_weights.py --weights delivery_absorption=40,lower_wick=30
    python tools/calibrate_wyckoff_weights.py --horizon 60 --n-symbols 200
"""

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta

# Repo root (for myra_app) + tools dir (for backtest_wyckoff module import).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_REPO_ROOT, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd  # noqa: E402

import backtest_wyckoff as bt  # noqa: E402  (reused harness, module import)
from myra_app.db.bulk_loader import COLUMNS_13, load_ohlcv_for_universe  # noqa: E402
from myra_app.strategies.wyckoff_automaton import (  # noqa: E402
    DEFAULT_SPRING_WEIGHTS,
    WyckoffAutomaton,
)

OUT_CSV = os.path.join(_TOOLS_DIR, "calibrate_wyckoff_weights_output.csv")

# Probe weights: every weight at the top of its search range. Monotone
# non-decreasing score in every weight => the probe scan is a superset of the
# Spring events any candidate weight-set emits (candidates the probe drops would
# score < 35 under EVERY weight-set, so no combo could emit them either).
PROBE_WEIGHTS = {
    "delivery_absorption": 50,
    "lower_wick": 50,
    "close_location": 30,
    "grab_depth": 20,
    "equal_low_bonus": 15,
    "two_candle_bonus": 10,
}

HORIZON_DEFAULT = 120
N_COMBOS_DEFAULT = 800
SEARCH_SEED = 42

BASE_RANGES = {
    "delivery_absorption": range(0, 51, 5),
    "lower_wick": range(0, 51, 5),
    "close_location": range(0, 31, 5),
    "grab_depth": range(0, 21, 2),
}
BONUS_RANGES = {
    "equal_low_bonus": range(0, 16, 2),
    "two_candle_bonus": range(0, 11, 2),
}

# Wicker (lower-wick) piecewise curve expressed as a FRACTION of max_score, i.e.
# the shape anchored on the default 30 scale ((0.20,0),(0.40,15),(0.60,22),
# (0.75,30)) divided by 30. Unrounded — mirrors the helper's arithmetic so that
# `_lower_wick_score(ratio, max_score=w)` == round(w * _wick_fraction(ratio), 1).
_WICK_PTS = [(0.20, 0.0), (0.40, 0.5), (0.60, 22.0 / 30.0), (0.75, 1.0)]

_SPLIT_TRAIN, _SPLIT_VAL, _SPLIT_HOLD = 0, 1, 2


# -- Search-space helpers ----------------------------------------------------


def _wick_fraction(ratio: float) -> float:
    """Fraction of max_score for a lower-wick ratio (unrounded shape value)."""
    if ratio <= _WICK_PTS[0][0]:
        return 0.0
    if ratio >= _WICK_PTS[-1][0]:
        return 1.0
    for (x0, y0), (x1, y1) in zip(_WICK_PTS, _WICK_PTS[1:]):
        if x0 <= ratio < x1:
            return y0 + (ratio - x0) / (x1 - x0) * (y1 - y0)
    return 0.0


def _close_fraction(ratio: float) -> float:
    """Fraction of max_score for a close-location ratio (0.25 / 0.5 / 1.0)."""
    if ratio > 0.75:
        return 1.0
    if ratio >= 0.5:
        return 0.5
    return 0.25


def _depth_fraction(depth_pct: float) -> float:
    """Fraction of max_score for a grab-depth % (0.5 / 0.7 / 1.0)."""
    if depth_pct > 1.5:
        return 0.5
    if depth_pct >= 0.5:
        return 1.0
    return 0.7


def _normalize_base(base: dict) -> dict:
    """Normalise the four base weights to sum 100 (per the brief)."""
    s = float(sum(base.values()))
    if s <= 0:
        return None
    return {k: 100.0 * v / s for k, v in base.items()}


def _weights_str(w: dict) -> str:
    return ";".join(f"{k}={v:.6g}" for k, v in w.items())


def _parse_weights_flag(spec: str) -> dict:
    """Parse `--weights delivery_absorption=40,lower_wick=30` (merged over
    defaults; unknown keys rejected)."""
    out = dict(DEFAULT_SPRING_WEIGHTS)
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        key, _, val = token.partition("=")
        key = key.strip()
        if key not in DEFAULT_SPRING_WEIGHTS:
            raise SystemExit(
                f"ERROR: unknown weight key {key!r}. Valid keys: "
                f"{sorted(DEFAULT_SPRING_WEIGHTS)}"
            )
        out[key] = float(val)
    return out


# -- Event collection (probe) ------------------------------------------------


def _grab_candle_index(df: pd.DataFrame, e: dict) -> int | None:
    """Row index of the Spring grab candle.

    Confirmed Springs are dated on the confirmation candle (abs_i + 1), so the
    grab candle is one row back; unconfirmed Springs are dated on the grab
    candle itself.
    """
    ed = pd.Timestamp(str(e["event_date"])[:10]).date()
    dates = df["date"]
    idx = None
    for i, d in enumerate(dates):
        if pd.Timestamp(d).date() == ed:
            idx = i
            break
    if idx is None:
        return None
    return idx - 1 if e.get("two_candle_confirm") else idx


def _candle_inputs(df: pd.DataFrame, e: dict, abs_i: int) -> dict:
    """Recompute the Spring scoring raw inputs for the grab candle at ``abs_i``
    exactly as the scanner computes them (delivery absorption, lower-wick ratio,
    close location, grab depth). Flag inputs (equal-low zone, two-candle
    confirm) are read from the event itself — both are detection outputs that do
    not depend on weights."""
    o_arr = df["open"].to_numpy(dtype=float)
    h_arr = df["high"].to_numpy(dtype=float)
    l_arr = df["low"].to_numpy(dtype=float)
    c_arr = df["close"].to_numpy(dtype=float)
    d_arr = df["delivery_pct"].to_numpy(dtype=float)
    sw_arr = df["swing_low"].to_numpy()

    del_pct = float(d_arr[abs_i])
    start_idx = max(0, abs_i - 50)
    del_slice = d_arr[start_idx:abs_i]
    avg_del_50 = float(np.nanmean(del_slice)) if len(del_slice) > 0 else del_pct
    del_abs = del_pct - avg_del_50

    open_p = float(o_arr[abs_i])
    high_p = float(h_arr[abs_i])
    low_p = float(l_arr[abs_i])
    close_p = float(c_arr[abs_i])
    denom = high_p - low_p
    lower_wick_ratio = (min(open_p, close_p) - low_p) / denom if denom > 0 else 0.5
    close_location = (close_p - low_p) / denom if denom > 0 else 0.5

    sl_raw = sw_arr[abs_i]
    swing_low_val = float(sl_raw) if pd.notna(sl_raw) else None
    if swing_low_val is not None and swing_low_val > 0:
        grab_depth_pct = (swing_low_val - low_p) / swing_low_val * 100
    else:
        grab_depth_pct = 0.0

    return {
        "del_abs": del_abs,
        "lower_wick_ratio": lower_wick_ratio,
        "close_location": close_location,
        "grab_depth_pct": grab_depth_pct,
    }


def collect_spring_candidates(
    automaton: WyckoffAutomaton, symbols: list[str], scan_date: date
) -> list[dict]:
    """Replicate the harness' data prep and capture every Spring candidate the
    PROBE scan emits, together with the raw scoring inputs (del_abs, wick ratio,
    close location, depth, flags) needed to re-score under arbitrary weights."""
    scan_date_s = scan_date.isoformat()
    min_date = (scan_date - timedelta(days=bt.LOOKBACK_DAYS)).isoformat()
    symbols = [s.strip() for s in symbols]
    bulk = load_ohlcv_for_universe(min_date, scan_date_s, symbols=symbols)
    automaton._bulk_data = bulk

    out: list[dict] = []
    for symbol in symbols:
        tech = automaton._get_tech_data(symbol, min_date, max_date=scan_date_s)
        if len(tech) < bt.MIN_ROWS:
            continue
        df = pd.DataFrame(tech, columns=list(COLUMNS_13))
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) < bt.MIN_ROWS:
            continue
        events = automaton._detect_events(df, symbol=symbol, as_on_date=scan_date_s)

        for e in events:
            if e["event"] != "Spring":
                continue
            abs_i = _grab_candle_index(df, e)
            if abs_i is None or abs_i < 0 or abs_i >= len(df):
                continue
            raw = _candle_inputs(df, e, abs_i)
            out.append(  # noqa: PG-APPEND  (accumulator of probe Spring candidates)
                {
                    "symbol": symbol,
                    "event": "Spring",
                    "event_date": str(e["event_date"])[:10],
                    "_scan_date": scan_date_s,
                    "_rows_in_window": int(len(df)),
                    "quality": e["quality"],
                    "close": e["close"],
                    **raw,
                    "equal_low_zone": bool(e.get("equal_low_zone", False)),
                    "two_candle_confirm": bool(e.get("two_candle_confirm", False)),
                    "probe_spring_score": e["spring_score"],
                }
            )
    return out


# -- Weight-set scoring (exact reproduction of the scanner's helpers) --------


def _score_arrays(cands: list[dict]) -> tuple:
    """Precompute per-candidate fraction inputs + flags as numpy arrays."""
    n = len(cands)
    u1 = np.zeros(n)
    u2 = np.zeros(n)
    u3 = np.zeros(n)
    u4 = np.zeros(n)
    eqf = np.zeros(n, dtype=bool)
    tcf = np.zeros(n, dtype=bool)
    for i, c in enumerate(cands):
        u1[i] = max(c["del_abs"], 0.0) / 10.0
        u2[i] = _wick_fraction(c["lower_wick_ratio"])
        u3[i] = _close_fraction(c["close_location"])
        u4[i] = _depth_fraction(c["grab_depth_pct"])
        eqf[i] = c["equal_low_zone"]
        tcf[i] = c["two_candle_confirm"]
    return u1, u2, u3, u4, eqf, tcf


def _score_combo(w: dict, u1, u2, u3, u4, eqf, tcf) -> np.ndarray:
    """spring_score per candidate under weight-set w.

    Mirrors the scanner EXACTLY: each component score is rounded to 1dp by the
    static helpers, the bonuses are added unrounded, the total is clamped to
    [0, 100] and rounded to 1dp.
    """
    del_s = np.round(
        np.minimum(u1 * w["delivery_absorption"], w["delivery_absorption"]), 1
    )
    wick_s = np.round(np.minimum(u2 * w["lower_wick"], w["lower_wick"]), 1)
    close_s = np.round(np.minimum(u3 * w["close_location"], w["close_location"]), 1)
    depth_s = np.round(np.minimum(u4 * w["grab_depth"], w["grab_depth"]), 1)
    total = del_s + wick_s + close_s + depth_s
    total = total + eqf * w["equal_low_bonus"] + tcf * w["two_candle_bonus"]
    return np.round(np.clip(total, 0.0, 100.0), 1)


def _split_metrics(scores: np.ndarray, net: np.ndarray, horizon: int) -> tuple:
    """Q5-Q1 / top-quintile win rate / Sharpe / composite for one split.

    Uses the score >= 35 (grade C+) gate exactly as the scanner's grade-D skip.
    Returns (n_pass, q5q1, win_rate_pct, sharpe, composite); invalid combos
    (fewer than 5 non-empty score buckets) get q5q1=nan and composite=-inf.

    Quintiles mirror ``pandas.qcut(..., q=5, duplicates="drop")`` exactly:
    edges = unique(pandas quantile of score); with default right=True bins,
    codes = searchsorted(edges, score, side="left") - 1, and include_lowest
    forces score == edges[0] into the first bin. Empty bins get NaN group
    means (pandas observed=False behaviour).
    """
    m = (scores >= 35.0) & np.isfinite(net)
    s, r = scores[m], net[m]
    n_pass = int(len(s))
    if n_pass < 5 or len(np.unique(s)) < 5:
        return n_pass, float("nan"), float("nan"), float("nan"), float("-inf")
    edges = np.unique(np.quantile(s, np.linspace(0.0, 1.0, 6)))
    if len(edges) < 6:
        # pandas qcut drops duplicate edges -> fewer than 5 categories
        return n_pass, float("nan"), float("nan"), float("nan"), float("-inf")
    ids = np.searchsorted(edges, s, side="left")
    ids[s == edges[0]] = 1  # include_lowest: equal to first edge -> first bin
    codes = ids - 1
    q5 = 5
    rsum = np.zeros(q5)
    rcnt = np.zeros(q5, dtype=int)
    for c in range(q5):
        sel = codes == c
        rcnt[c] = int(sel.sum())
        if rcnt[c]:
            rsum[c] = float(r[sel].sum())
    grp = np.where(rcnt > 0, rsum / np.maximum(rcnt, 1), np.nan)
    q5q1 = float(grp[-1] - grp[0])
    top = r[codes == q5 - 1]
    win_rate = float((top > 0).mean() * 100.0)
    if len(top) > 1 and top.std() > 0:
        sharpe = float(top.mean() / top.std() * np.sqrt(365.0 / horizon))
    else:
        sharpe = 0.0
    composite = q5q1 + 0.5 * win_rate / 100.0
    return n_pass, q5q1, win_rate, sharpe, composite


# -- Splits ------------------------------------------------------------------


def _split_scan_dates(scan_dates: list[date]) -> tuple:
    """Chronological ~70/15/15 train/validation/holdout by scan date."""
    n = len(scan_dates)
    if n < 3:
        raise SystemExit("need >= 3 scan dates for a 70/15/15 split")
    n_train = max(1, min(n - 2, int(round(n * 0.70))))
    n_val = max(1, min(n - n_train - 1, int(round(n * 0.15))))
    n_hold = n - n_train - n_val
    return (
        scan_dates[:n_train],
        scan_dates[n_train : n_train + n_val],
        scan_dates[n_train + n_val :],
    )


def _verify_no_leak(cands: list[dict], train_tags, val_tags, hold_tags) -> None:
    """Assert the split is airtight: one split per event, strictly
    chronological, no (symbol, event_date) in two splits."""
    splits = {_SPLIT_TRAIN: train_tags, _SPLIT_VAL: val_tags, _SPLIT_HOLD: hold_tags}
    seen: dict = {}
    for c in cands:
        sd = c["_scan_date"]
        which = [k for k, dates in splits.items() if sd in dates]
        assert (
            len(which) == 1
        ), f"event {c['symbol']}@{c['event_date']} scan_date {sd} in {len(which)} splits"
        key = (c["symbol"], c["event_date"])
        if key in seen:
            assert (
                seen[key] == which[0]
            ), f"(symbol,event_date) {key} leaked across splits"
        else:
            seen[key] = which[0]
    assert max(train_tags) < min(val_tags), "train dates must precede validation dates"
    assert max(val_tags) < min(hold_tags), "validation dates must precede holdout dates"


def _split_tag(scan_date_s: str, train_tags, val_tags, hold_tags) -> int:
    if scan_date_s in train_tags:
        return _SPLIT_TRAIN
    if scan_date_s in val_tags:
        return _SPLIT_VAL
    return _SPLIT_HOLD


# -- Search ------------------------------------------------------------------


def _random_combos(n_combos: int) -> list[dict]:
    rng = random.Random(SEARCH_SEED)
    combos: list[dict] = []
    attempts = 0
    while len(combos) < n_combos and attempts < n_combos * 20:
        attempts += 1
        base = {
            "delivery_absorption": rng.choice(list(BASE_RANGES["delivery_absorption"])),
            "lower_wick": rng.choice(list(BASE_RANGES["lower_wick"])),
            "close_location": rng.choice(list(BASE_RANGES["close_location"])),
            "grab_depth": rng.choice(list(BASE_RANGES["grab_depth"])),
        }
        norm = _normalize_base(base)
        if norm is None:
            continue
        combo = {
            **norm,
            "equal_low_bonus": float(rng.choice(list(BONUS_RANGES["equal_low_bonus"]))),
            "two_candle_bonus": float(
                rng.choice(list(BONUS_RANGES["two_candle_bonus"]))
            ),
        }
        combos.append(combo)  # noqa: PG-APPEND  (accumulator of sampled combos)
    return combos


def _grid_combos() -> list[dict]:
    """Exhaustive product of the search ranges (base normalised, deduped)."""
    from itertools import product

    combos: list[dict] = []
    seen_bases: set = set()
    for base_tuple in product(
        BASE_RANGES["delivery_absorption"],
        BASE_RANGES["lower_wick"],
        BASE_RANGES["close_location"],
        BASE_RANGES["grab_depth"],
    ):
        base = dict(zip(BASE_RANGES.keys(), base_tuple))
        norm = _normalize_base(base)
        if norm is None:
            continue
        key = tuple(round(v, 6) for v in norm.values())
        if key in seen_bases:
            continue
        seen_bases.add(key)
        for eq in BONUS_RANGES["equal_low_bonus"]:
            for tc in BONUS_RANGES["two_candle_bonus"]:
                combos.append(  # noqa: PG-APPEND  (accumulator of grid combos)
                    {
                        **norm,
                        "equal_low_bonus": float(eq),
                        "two_candle_bonus": float(tc),
                    }
                )
    return combos


def _quality_q5q1_reference(
    cands: list[dict], net: np.ndarray, split: np.ndarray
) -> float:
    """Reference Q5-Q1 computed on the `quality` field (the metric the tuned
    weights do NOT control) — printed only to document the distinction."""
    mask = split == _SPLIT_TRAIN
    s = pd.Series([c["quality"] for c in cands])[mask]
    r = pd.Series(net[mask])
    m = np.isfinite(r.values) & s.notna().values
    s, r = s[m], r[m]
    if len(s) < 5 or len(s.unique()) < 5:
        return float("nan")
    try:
        q = pd.qcut(s, q=5, duplicates="drop")
        if len(q.cat.categories) < 5:
            return float("nan")
        grp = r.groupby(q, observed=False).mean()
        return float(grp.iloc[-1] - grp.iloc[0])
    except (ValueError, IndexError):
        return float("nan")


# -- Parity verification -----------------------------------------------------


def _verify_parity(
    weights: dict,
    symbols: list[str],
    scan_dates: list[date],
    tolerance: float = 0.2,
) -> dict:
    """Re-run the REAL scanner with `weights` and compare its emitted Spring
    set against the offline recomputation, per scan date, like-for-like.

    Per date: build the symbol DataFrame ONCE (the probe and real scans share
    the exact same window), detect with the probe automaton to capture raw
    inputs, re-score offline, and detect with the real (weighted) automaton.
    Both maps key on (symbol, event_date). Mismatches are reported per date and
    aggregated. Anything beyond ``tolerance`` in a score, or any set difference,
    means the offline recomputation has diverged from the scanner.
    """
    real = WyckoffAutomaton(weights=weights)
    probe = WyckoffAutomaton(weights=PROBE_WEIGHTS)
    total_real = total_off = total_missing = total_extra = 0
    maxdiff = 0.0
    for D in scan_dates:
        scan_date_s = D.isoformat()
        min_date = (D - timedelta(days=bt.LOOKBACK_DAYS)).isoformat()
        syms = [s.strip() for s in symbols]
        bulk = load_ohlcv_for_universe(min_date, scan_date_s, symbols=syms)
        real._bulk_data = bulk
        probe._bulk_data = bulk
        real_map: dict = {}
        off_map: dict = {}
        for symbol in syms:
            tech = real._get_tech_data(symbol, min_date, max_date=scan_date_s)
            if len(tech) < bt.MIN_ROWS:
                continue
            df = pd.DataFrame(tech, columns=list(COLUMNS_13))
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < bt.MIN_ROWS:
                continue
            for e in real._detect_events(df, symbol=symbol, as_on_date=scan_date_s):
                if e["event"] == "Spring":
                    real_map[(symbol, str(e["event_date"])[:10])] = e["spring_score"]
            for e in probe._detect_events(df, symbol=symbol, as_on_date=scan_date_s):
                if e["event"] != "Spring":
                    continue
                abs_i = _grab_candle_index(df, e)
                if abs_i is None or abs_i < 0 or abs_i >= len(df):
                    continue
                raw = _candle_inputs(df, e, abs_i)
                del_s = WyckoffAutomaton._delivery_absorption_score(
                    raw["del_abs"], weights["delivery_absorption"]
                )
                wick_s = WyckoffAutomaton._lower_wick_score(
                    raw["lower_wick_ratio"], weights["lower_wick"]
                )
                close_s = WyckoffAutomaton._close_location_score(
                    raw["close_location"], weights["close_location"]
                )
                depth_s = WyckoffAutomaton._grab_depth_score(
                    raw["grab_depth_pct"], weights["grab_depth"]
                )
                eq_bonus = weights["equal_low_bonus"] if e["equal_low_zone"] else 0.0
                score = WyckoffAutomaton._compute_spring_score(
                    del_s,
                    wick_s,
                    close_s,
                    depth_s,
                    eq_bonus,
                    e["two_candle_confirm"],
                    weights["two_candle_bonus"],
                )
                off_map[(symbol, str(e["event_date"])[:10])] = float(score)
        real_keys, off_keys = set(real_map), set(off_map)
        total_real += len(real_keys)
        total_off += len(off_keys)
        total_missing += len(off_keys - real_keys)
        total_extra += len(real_keys - off_keys)
        for k in real_keys & off_keys:
            maxdiff = max(maxdiff, abs(real_map[k] - off_map[k]))
    return {
        "real": total_real,
        "offline": total_off,
        "missing": total_missing,
        "extra": total_extra,
        "maxdiff": maxdiff,
        "ok": not total_missing and not total_extra and maxdiff <= tolerance,
    }


# -- Reporting ---------------------------------------------------------------


def _fmt(v: float, suffix: str = "") -> str:
    return (
        "N/A"
        if v is None or (isinstance(v, float) and np.isnan(v))
        else f"{v:+.2f}{suffix}"
    )


def _print_table(rows: list[tuple]) -> None:
    header = (
        f"{'split':>8} | {'n':>5} | {'Q5-Q1':>8} | {'topWin%':>8} | "
        f"{'Sharpe':>7} | {'composite':>10}"
    )
    print(header)
    print("|" + "-" * (len(header) - 2) + "|")
    for name, n, q, w, sh, co in rows:
        qs = (
            "N/A"
            if (q is None or (isinstance(q, float) and np.isnan(q)))
            else f"{q:+.2f}%"
        )
        ws = (
            "N/A"
            if (w is None or (isinstance(w, float) and np.isnan(w)))
            else f"{w:>5.1f}%"
        )
        shs = (
            "N/A"
            if (sh is None or (isinstance(sh, float) and np.isnan(sh)))
            else f"{sh:+.2f}"
        )
        cos = (
            "N/A"
            if (co is None or (isinstance(co, float) and np.isnan(co)))
            else f"{co:+.3f}"
        )
        print(f"{name:>8} | {n:>5d} | {qs:>8} | {ws:>8} | {shs:>7} | {cos:>10}")


def _write_csv(rows: list[dict], path: str) -> None:
    cols = [
        "kind",
        "search",
        "train_n",
        "train_q5q1",
        "train_winrate",
        "train_sharpe",
        "train_composite",
        "val_n",
        "val_q5q1",
        "val_winrate",
        "val_sharpe",
        "val_composite",
        "hold_n",
        "hold_q5q1",
        "hold_winrate",
        "hold_sharpe",
        "is_default",
        "is_best",
    ]
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)
    print(f"\nEvery combo + train/val metrics written to {path} (gitignored)")


# -- Main --------------------------------------------------------------------


def parse_args(argv):
    p = argparse.ArgumentParser(description="Wyckoff Spring weight calibration")
    p.add_argument(
        "--horizon",
        type=int,
        default=HORIZON_DEFAULT,
        help="forward-return horizon in calendar days (default 120)",
    )
    p.add_argument(
        "--n-symbols",
        type=int,
        default=None,
        help="symbols sampled from the universe (default 400; 150 in --smoke)",
    )
    p.add_argument("--seed", type=int, default=42, help="symbol sampling seed")
    p.add_argument(
        "--search",
        choices=["random", "grid"],
        default=None,
        help="combo search strategy (default: no search; --smoke-only run)",
    )
    p.add_argument(
        "--n-combos",
        type=int,
        default=N_COMBOS_DEFAULT,
        help="random-search combo count (default 800; grid is exhaustive)",
    )
    p.add_argument(
        "--n-scan-dates",
        type=int,
        default=12,
        help="number of evenly spaced scan dates (default 12)",
    )
    p.add_argument(
        "--weights",
        default=None,
        metavar="K=V,K=V",
        help="single custom weight set (merged over defaults; skips search)",
    )
    p.add_argument(
        "--parity",
        action="store_true",
        help="run the real-scanner parity re-check for selected + default sets "
        "(per-date, like-for-like; adds runtime)",
    )
    p.add_argument(
        "--parity-symbols",
        type=int,
        default=150,
        help="symbols subsample for the parity re-check (default 150)",
    )
    p.add_argument(
        "--smoke",
        action="store_true",
        help="quick run: default weights only + split/no-leak table (with --search, runs a reduced search)",
    )
    p.add_argument("--out", default=OUT_CSV, help="CSV output path")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    horizon = args.horizon
    cost_label = "net (0.5% x2 + 15% STCG)"
    n_symbols = args.n_symbols or (150 if args.smoke else 400)
    t0 = datetime.now()

    # --- Scan dates + split -------------------------------------------------
    mtd = bt.max_tech_date()
    end_guard = (
        datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(horizon, 180))
    ).date()
    end_default = min(
        datetime.strptime(bt.DEFAULT_END_HARD, "%Y-%m-%d").date(), end_guard
    )
    start = datetime.strptime(bt.DEFAULT_START, "%Y-%m-%d").date()
    span = (end_default - start).days
    step = max(1, span // (args.n_scan_dates - 1))
    scan_dates = [start + timedelta(days=i * step) for i in range(args.n_scan_dates)]
    scan_dates = [d for d in scan_dates if d <= end_default]
    train_d, val_d, hold_d = _split_scan_dates(scan_dates)
    train_tags = {d.isoformat() for d in train_d}
    val_tags = {d.isoformat() for d in val_d}
    hold_tags = {d.isoformat() for d in hold_d}

    print("=" * 100)
    print("WYCKOFF SPRING WEIGHT CALIBRATION")
    print("=" * 100)
    print(f"Scan dates ({len(scan_dates)}): {[d.isoformat() for d in scan_dates]}")
    print(
        f"Chronological split: TRAIN {len(train_d)} ({train_d[0]}..{train_d[-1]}), "
        f"VALIDATION {len(val_d)} ({val_d[0]}..{val_d[-1]}), "
        f"HOLDOUT {len(hold_d)} ({hold_d[0]}..{hold_d[-1]})"
    )

    # --- Universe + sample --------------------------------------------------
    automaton = WyckoffAutomaton()
    universe = automaton._get_universe()
    random.seed(args.seed)
    sampled = random.sample([r[0].strip() for r in universe], n_symbols)
    print(
        f"Universe {len(universe)} -> sampled {len(sampled)} symbols (seed {args.seed})"
    )

    # --- Probe collection ---------------------------------------------------
    probe = WyckoffAutomaton(weights=PROBE_WEIGHTS)
    cands: list[dict] = []
    for D in scan_dates:
        evs = collect_spring_candidates(probe, sampled, D)
        print(f"  {D.isoformat()}: {len(evs):4d} probe Spring candidates")
        cands.extend(evs)
    print(f"\nTotal probe Spring candidates (raw, incl. re-detections): {len(cands)}")

    # Deduplicate re-detected events: detection is deterministic given the data
    # up to the event candle, so the same (symbol, event_date) Spring is
    # re-emitted by every later scan whose 90d window covers it. Keep the FIRST
    # scan that emitted it — an event is a dated signal and belongs to the split
    # of its earliest detectable scan date, guaranteeing no (symbol, event_date)
    # appears in two splits.
    seen_keys: set = set()
    deduped: list[dict] = []
    for c in cands:
        key = (c["symbol"], c["event_date"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(c)  # noqa: PG-APPEND  (accumulator of unique events)
    cands = deduped
    print(f"After dedupe (unique (symbol, event_date) events): {len(cands)}")

    _verify_no_leak(cands, train_tags, val_tags, hold_tags)
    print(
        "Split integrity: OK - every event in exactly one split; train dates < "
        "validation dates < holdout dates; no (symbol,event_date) in two splits"
    )

    # --- Forward returns ----------------------------------------------------
    net_arr = np.full(len(cands), np.nan)
    win_arr = np.zeros(len(cands), dtype=bool)
    split_arr = np.zeros(len(cands), dtype=int)
    for i, c in enumerate(cands):
        r = bt.build_forward_row(c, horizon, use_costs=True)
        net_arr[i] = r["net_return"] if r["net_return"] is not None else np.nan
        win_arr[i] = bool(r["win"]) if r["win"] is not None else False
        split_arr[i] = _split_tag(c["_scan_date"], train_tags, val_tags, hold_tags)

    meas = int(np.isfinite(net_arr).sum())
    print(
        f"\nForward returns ({horizon}d, {cost_label}): {meas}/{len(cands)} candidates measurable"
    )
    u1, u2, u3, u4, eqf, tcf = _score_arrays(cands)

    # Reference Q5-Q1 on `quality` (the metric the weights do NOT control).
    ref_q = _quality_q5q1_reference(cands, net_arr, split_arr)
    print(
        f"\n[reference only] DEFAULT `quality`-field Q5-Q1 on TRAIN: {_fmt(ref_q)}% "
        "- NOT used for calibration (weights do not control `quality`)"
    )

    def evaluate(
        w: dict, label: str, kind: str, is_default: bool, is_best: bool
    ) -> dict:
        scores = _score_combo(w, u1, u2, u3, u4, eqf, tcf)
        tr = _split_metrics(
            scores[split_arr == _SPLIT_TRAIN],
            net_arr[split_arr == _SPLIT_TRAIN],
            horizon,
        )
        va = _split_metrics(
            scores[split_arr == _SPLIT_VAL], net_arr[split_arr == _SPLIT_VAL], horizon
        )
        ho = _split_metrics(
            scores[split_arr == _SPLIT_HOLD], net_arr[split_arr == _SPLIT_HOLD], horizon
        )
        return {
            "kind": kind,
            "search": label,
            "w": dict(w),
            "train": tr,
            "val": va,
            "hold": ho,
            "is_default": is_default,
            "is_best": is_best,
        }

    csv_rows: list[dict] = []

    def push_csv(e: dict, is_best: bool = False) -> dict:
        row = {  # noqa: PG-APPEND  (accumulator of result rows)
            "kind": e["kind"],
            "search": e["search"],
            "train_n": e["train"][0],
            "train_q5q1": e["train"][1],
            "train_winrate": e["train"][2],
            "train_sharpe": e["train"][3],
            "train_composite": e["train"][4],
            "val_n": e["val"][0],
            "val_q5q1": e["val"][1],
            "val_winrate": e["val"][2],
            "val_sharpe": e["val"][3],
            "val_composite": e["val"][4],
            "hold_n": e["hold"][0],
            "hold_q5q1": e["hold"][1],
            "hold_winrate": e["hold"][2],
            "hold_sharpe": e["hold"][3],
            "is_default": e["is_default"],
            "is_best": is_best,
        }
        csv_rows.append(row)  # noqa: PG-APPEND  (accumulator of result rows)
        return row

    # --- Default baseline (weights as shipped, NOT normalised) --------------
    default_eval = evaluate(
        dict(DEFAULT_SPRING_WEIGHTS),
        "default",
        "default",
        is_default=True,
        is_best=False,
    )
    push_csv(default_eval)
    print("\n--- DEFAULT weights (as shipped) per-split ---")
    _print_table(
        [
            ("TRAIN", *default_eval["train"]),
            ("VALIDATION", *default_eval["val"]),
            ("HOLDOUT", *default_eval["hold"]),
        ]
    )

    # --- Custom single set mode --------------------------------------------
    if args.weights:
        w = _parse_weights_flag(args.weights)
        custom_eval = evaluate(
            w, "custom --weights", "custom", is_default=False, is_best=False
        )
        push_csv(custom_eval)
        _write_csv(csv_rows, args.out)
        print(f"\n--- Custom weights: {_weights_str(w)} ---")
        _print_table(
            [
                ("TRAIN", *custom_eval["train"]),
                ("VALIDATION", *custom_eval["val"]),
                ("HOLDOUT", *custom_eval["hold"]),
            ]
        )
        dv = default_eval["val"][1]
        gap = (
            custom_eval["val"][1] - dv
            if not np.isnan(dv) and not np.isnan(custom_eval["val"][1])
            else float("nan")
        )
        if (
            not np.isnan(gap)
            and custom_eval["val"][1] > 0
            and custom_eval["val"][1] > dv
        ):
            print(
                f"\nDECISION: PROCEED - custom validation Q5-Q1 {custom_eval['val'][1]:+.2f}% "
                f"beats default {dv:+.2f}% (gap {gap:+.2f}%)"
            )
        else:
            print(
                f"\nDECISION: ABANDON - custom validation Q5-Q1 {_fmt(custom_eval['val'][1])} "
                f"does not beat default {_fmt(dv)} (gap {_fmt(gap)})"
            )
        if args.parity:
            p_syms = random.Random(7).sample(
                sampled, min(args.parity_symbols, n_symbols)
            )
            chk = _verify_parity(w, p_syms, train_d + val_d)
            print(
                f"Parity check (real scanner vs offline, {len(train_d)+len(val_d)} dates, "
                f"{len(p_syms)} symbols): real={chk['real']} offline={chk['offline']} "
                f"missing={chk['missing']} extra={chk['extra']} "
                f"maxscore-diff={chk['maxdiff']:.3f} -> {'OK' if chk['ok'] else 'MISMATCH'}"
            )
        return 0

    # --- Search -------------------------------------------------------------
    if args.search is None:
        print("\nNo --search given: session ends after the default/leak table.")
        print(
            "(combine with --search random --n-combos N to search, or --weights K=V,K=V)"
        )
        return 0

    if args.search == "random":
        combos = _random_combos(args.n_combos)
        print(
            f"\nRandom search: {len(combos)} combos (seed {SEARCH_SEED}, independent Random)"
        )
    else:
        combos = _grid_combos()
        print(
            f"\nGrid search: {len(combos)} combos (exhaustive; normalised bases deduped)"
        )

    best_composite = float("-inf")
    best_eval = None
    best_row = None
    for j, w in enumerate(combos):
        scores = _score_combo(w, u1, u2, u3, u4, eqf, tcf)
        tr = _split_metrics(
            scores[split_arr == _SPLIT_TRAIN],
            net_arr[split_arr == _SPLIT_TRAIN],
            horizon,
        )
        va = _split_metrics(
            scores[split_arr == _SPLIT_VAL], net_arr[split_arr == _SPLIT_VAL], horizon
        )
        ho = _split_metrics(
            scores[split_arr == _SPLIT_HOLD], net_arr[split_arr == _SPLIT_HOLD], horizon
        )
        e = {
            "kind": args.search,
            "search": _weights_str(w),
            "w": w,
            "train": tr,
            "val": va,
            "hold": ho,
            "is_default": False,
            "is_best": False,
        }
        row = push_csv(e)
        if np.isfinite(tr[4]) and tr[4] > best_composite:
            best_composite = tr[4]
            best_eval = e
            best_row = row

    if best_eval is None:
        print(
            "ERROR: no weight-set produced a valid TRAIN composite (Q5-Q1 via >=5 score buckets)."
        )
        _write_csv(csv_rows, args.out)
        return 1

    best_eval["is_best"] = True
    best_row["is_best"] = True
    _write_csv(csv_rows, args.out)

    # --- Selected vs default ------------------------------------------------
    print("\n" + "=" * 100)
    print("SELECTED WEIGHTS (argmax TRAIN composite = Q5-Q1 + 0.5*win_rate/100)")
    print("=" * 100)
    print(f"  {_weights_str(best_eval['w'])}")
    print(
        f"  TRAIN  composite {best_eval['train'][4]:+.3f} | Q5-Q1 {best_eval['train'][1]:+.2f}% | topWin {best_eval['train'][2]:.1f}% | Sharpe {best_eval['train'][3]:+.2f}"
    )
    print(
        f"  VALID  composite {best_eval['val'][4]:+.3f} | Q5-Q1 {best_eval['val'][1]:+.2f}% | topWin {best_eval['val'][2]:.1f}% | Sharpe {best_eval['val'][3]:+.2f}"
    )
    print(
        f"  HOLD   composite {best_eval['hold'][4]:+.3f} | Q5-Q1 {best_eval['hold'][1]:+.2f}% | topWin {best_eval['hold'][2]:.1f}% | Sharpe {best_eval['hold'][3]:+.2f}"
    )

    print("\nDEFAULT WEIGHTS for comparison (as shipped)")
    print(
        f"  TRAIN  composite {default_eval['train'][4]:+.3f} | Q5-Q1 {default_eval['train'][1]:+.2f}% | topWin {default_eval['train'][2]:.1f}% | Sharpe {default_eval['train'][3]:+.2f}"
    )
    print(
        f"  VALID  composite {default_eval['val'][4]:+.3f} | Q5-Q1 {default_eval['val'][1]:+.2f}% | topWin {default_eval['val'][2]:.1f}% | Sharpe {default_eval['val'][3]:+.2f}"
    )
    print(
        f"  HOLD   composite {default_eval['hold'][4]:+.3f} | Q5-Q1 {default_eval['hold'][1]:+.2f}% | topWin {default_eval['hold'][2]:.1f}% | Sharpe {default_eval['hold'][3]:+.2f}"
    )

    bv, dv = best_eval["val"][1], default_eval["val"][1]
    gap = bv - dv if (not np.isnan(bv) and not np.isnan(dv)) else float("nan")
    if not np.isnan(gap) and bv > 0 and bv > dv:
        decision = "PROCEED"
        print(
            f"\nDECISION: {decision} - selected validation Q5-Q1 {bv:+.2f}% > 0 and beats "
            f"default {dv:+.2f}% (gap {gap:+.2f}%). Recommend updating "
            "DEFAULT_SPRING_WEIGHTS to the calibrated set."
        )
    else:
        decision = "ABANDON"
        pos = "positive" if bv > 0 else "not positive"
        print(
            f"\nDECISION: {decision} - selected validation Q5-Q1 {_fmt(bv)} is {pos} and/or "
            f"does not beat default {_fmt(dv)} (gap {_fmt(gap)}%). "
            f"Current weights already optimal; defaults unchanged."
        )

    # --- Parity verification ------------------------------------------------
    if args.parity:
        print(
            "\nParity re-check (real WyckoffAutomaton scans vs offline recompute, "
            "per-date like-for-like):"
        )
        p_syms = random.Random(7).sample(sampled, min(args.parity_symbols, n_symbols))
        for label, w in (
            ("selected", best_eval["w"]),
            ("default", DEFAULT_SPRING_WEIGHTS),
        ):
            chk = _verify_parity(w, p_syms, train_d + val_d)
            print(
                f"  {label:>8}: real={chk['real']} offline={chk['offline']} "
                f"missing={chk['missing']} extra={chk['extra']} maxscore-diff={chk['maxdiff']:.3f} "
                f"-> {'OK' if chk['ok'] else 'MISMATCH'}"
            )

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\nTotal runtime: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
