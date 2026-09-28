"""DIAGNOSTIC: is the bulk-deal BUY-vs-control gap an event-day confound?

Context
-------
Commit a1f8b45 pre-registered and FAILED a test on bulk/block disclosed-deal
buying: BUY-minus-control was negative at h60 in 5 of 7 years, and SELL beat
BUY in 6 of 7. That null result STANDS and is not reopened here.

This script tests ONE new, separately pre-registered hypothesis, and nothing
else: that the gap is driven by event-day conditions rather than by who was
counterparty to the trade. Bulk/block deals are disclosed on unusual days
(high volume, often a large price move). Control A matched only on ADTV, so it
was an ordinary symbol on an ordinary day. BUY-minus-control was therefore
partly measuring "what happens after a volume-spike day", not "what happens
after an institution buys". If matching on event-day conditions closes the gap,
the original null is explained by a confound.

Pre-registered criteria (fixed before any result was seen)
---------------------------------------------------------
  With the control additionally matched on event-day conditions, the
  BUY-minus-control gap is > 0 at h60 in at least 5 of 7 years, including
  2023 and 2025-26, AND BUY beats SELL under the same matching in most years.
  Anything less is reported as null.

Interpretation limits (deliberate, so this cannot be misread)
-------------------------------------------------------------
* A PASS is NOT a signal. It is evidence that the a1f8b45 null was a confound,
  which makes it a hypothesis to validate on events after a fixed cutoff date,
  not something to trade.
* A FAIL is a clean second null on an independent control.
* Only 2020-2026 is examined here. No out-of-sample confirmation is claimed.

Method
------
STEP 1  Descriptives only, by year and side: event-day return, event-day volume
        ratio vs the prior 20-day average, and the share of events whose
        deal-day close is within 2% of the 20-day high.
STEP 2  Control B2: same-date random symbol matched on ADTV decile AND
        event-day volume-ratio tercile AND event-day return tercile, terciles
        computed cross-sectionally on that date, seed 42, never the event
        symbol. Events with no match are dropped and counted.
STEP 3  BUY-minus-B2 and SELL-minus-B2 per year at h20/60/120, per-date
        aggregation, t-stats across dates, never pooled across years.
STEP 4  Exploratory, labelled as such: mf_insurance BUY vs B2 by year, only
        if there are >= 300 such events, otherwise reported as underpowered.

All event-day features use data up to and including the deal date only.
READ-ONLY on every database.
"""

from __future__ import annotations

import argparse
import random
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.backtest_bulk_deals import (  # noqa: E402
    ADV_WINDOW,
    COOLDOWN_DAYS,
    CRORE,
    HORIZONS,
    MIN_PRIOR_DAYS,
    SEED,
    aggregate_per_date,
    build_net_events,
    check_prereq,
    compute_return,
    cost_adjusted_return,
    get_benchmark_close,
    load_calendar,
    load_deals_clean,
    load_year_prices,
    nth_td,
    size_bucket,
    thin,
)

YEARS = list(range(2020, 2027))
NEAR_HIGH_PCT = 0.98  # within 2% of the 20-day high
HIGH_WINDOW = 20
MIN_MF_EVENTS = 300  # pre-registered power floor for STEP 4
OUT_CSV = "bulk_deal_b2_diagnostic_results.csv"


# ---------------------------------------------------------------------------
# Event-day features -- strictly up to and including the deal date
# ---------------------------------------------------------------------------
def event_day_features(s, k, vol_cs=None):
    """(event_ret, vol_ratio, near_high) at index k, using data <= index k.

    * event_ret : close[k] / close[k-1] - 1
    * vol_ratio : volume[k] / mean(volume[k-20:k])
    * near_high : close[k] >= 0.98 * max(close[k-19:k+1])

    None if the symbol lacks the prior history to compute it. Nothing after k
    is read, which is the whole point: a control matched on "what the tape
    looked like on the deal day" must not know how the day turned out.

    `vol_cs` is the cumulative-volume prefix. It is passed in by the
    per-symbol hot loop because Series uses __slots__ and cannot cache it.
    """
    n = len(s.dates)
    if k < ADV_WINDOW or k < HIGH_WINDOW or k < 1:
        return None, None, None
    if vol_cs is None:
        vol_cs = np.concatenate([[0.0], np.cumsum(s.vols)])
    adv20 = float((vol_cs[k] - vol_cs[k - ADV_WINDOW]) / ADV_WINDOW)
    if adv20 <= 0:
        return None, None, None
    vol_ratio = float(s.vols[k]) / adv20
    event_ret = float(s.closes[k] / s.closes[k - 1] - 1.0)
    hi = float(s.closes[k - HIGH_WINDOW + 1 : k + 1].max())
    near_high = bool(s.closes[k] >= NEAR_HIGH_PCT * hi)
    return event_ret, vol_ratio, near_high


def build_b2_universe(series_map, adtv_min_crore):
    """universe[date] -> list of (symbol, adtv, vol_ratio, event_ret).

    The same ADTV gate as the original study, so B2 and A see the same
    investable set; only the MATCHING differs.
    """
    thr = adtv_min_crore * CRORE
    universe = defaultdict(list)
    for sym, s in series_map.items():
        n = len(s.dates)
        if n <= MIN_PRIOR_DAYS or n <= ADV_WINDOW:
            continue
        cs = s.notional_cs
        vol_cs = np.concatenate([[0.0], np.cumsum(s.vols)])
        for k in range(MIN_PRIOR_DAYS, n):
            adtv = float((cs[k] - cs[k - ADV_WINDOW]) / ADV_WINDOW)
            if adtv < thr:
                continue
            er, vr, _nh = event_day_features(s, k, vol_cs)
            if er is None or vr is None:
                continue
            universe[s.dates[k]].append((sym, adtv, vr, er))
    return universe


# ---------------------------------------------------------------------------
# Cross-sectional bucketing
# ---------------------------------------------------------------------------
def _ranks(values):
    """Dense-ish rank positions 0..n-1, ascending. Ties broken by order."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    return order


def assign_terciles(pairs, value_index):
    """Cross-sectional terciles (0,1,2) of one feature within ONE date.

    `pairs` are tuples; value_index picks the feature. Terciles are computed
    only from the entries passed in, which is what makes them cross-sectional
    on that date: no other date's distribution can leak in.
    """
    if not pairs:
        return {}
    n = len(pairs)
    idx = _ranks([p[value_index] for p in pairs])
    out = {}
    for pos, i in enumerate(idx):
        out[pairs[i][0]] = min(2, int(pos * 3 / n))
    return out


def assign_deciles10(entries):
    """ADTV deciles 0-9 within one date, same rule as the original study."""
    ordered = sorted(entries, key=lambda t: (t[1], t[0]))
    n = len(ordered)
    dec = {}
    for i, (sym, *_rest) in enumerate(ordered):
        dec[sym] = min(9, int(i * 10 / n))
    return dec


def b2_cells(entries):
    """(symbol -> (adtv_decile, vol_tercile, ret_tercile)) plus the cells."""
    dec10 = assign_deciles10(entries)
    vol_t = assign_terciles(entries, 2)
    ret_t = assign_terciles(entries, 3)
    cells = defaultdict(list)
    pos = {}
    for sym, _a, _v, _r in entries:
        cell = (dec10[sym], vol_t[sym], ret_t[sym])
        pos[sym] = cell
        cells[cell].append(sym)
    return pos, cells


def pick_b2(events, universe, rng, log):
    """Control B2: same date, matched on the 3-way cell, never the event symbol."""
    cache = {}
    out = []
    for row in events.itertuples(index=False):
        d = row.date
        if d not in cache:
            entries = universe.get(d, [])
            cache[d] = b2_cells(entries) if entries else ({}, {})
        pos, cells = cache[d]
        target = pos.get(row.symbol)
        if target is None:
            log["b2_event_not_in_universe"] += 1
            out.append(None)
            continue
        cand = [s for s in cells.get(target, []) if s != row.symbol]
        if not cand:
            log["b2_no_match"] += 1
            out.append(None)
            continue
        out.append(rng.choice(cand))
    return out


# ---------------------------------------------------------------------------
# Self-tests -- must pass before ANY real data is read
# ---------------------------------------------------------------------------
def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run_self_tests():
    say("STEP -1 -- SELF-TESTS (run BEFORE any real data is read)")
    say("=" * 100)

    # 1. Terciles are CROSS-SECTIONAL per date: the same symbol must land in a
    #    different tercile on a date with a different distribution, and one
    #    date's entries must never influence another's.
    a = [("LOW", 1.0, 1.0, 0.10), ("MID", 1.0, 2.0, 0.00), ("HIGH", 1.0, 3.0, -0.10)]
    b = [("LOW", 1.0, 9.0, -0.05), ("MID", 1.0, 5.0, 0.05), ("HIGH", 1.0, 1.0, 0.00)]
    ta_v = assign_terciles(a, 2)
    tb_v = assign_terciles(b, 2)
    _check(
        ta_v["LOW"] == 0 and ta_v["MID"] == 1 and ta_v["HIGH"] == 2,
        f"terciles not ascending within a date: {ta_v}",
    )
    # On date b the ordering is inverted, so HIGH must be tercile 0 there.
    _check(
        tb_v["HIGH"] == 0 and tb_v["MID"] == 1 and tb_v["LOW"] == 2,
        f"terciles are not cross-sectional per date: {tb_v}",
    )
    say("  [self-test] 1. terciles are cross-sectional within each date")

    # 2. Terciles ignore anything not passed in (no cross-date leakage).
    solo = assign_terciles([("X", 1.0, 0.5, 0.0)], 2)
    _check(set(solo) == {"X"}, "tercile helper invented a symbol")
    only3 = assign_terciles(
        [("P", 1.0, 1.0, 0.0), ("Q", 1.0, 2.0, 0.0), ("R", 1.0, 3.0, 0.0)], 3
    )
    _check(len(set(only3.values())) == 3, "degenerate spread collapsed terciles")
    say("  [self-test] 2. terciles use only the entries given")

    # 3. Event-day features use NO post-deal data. Append a violent future bar
    #    and re-check every feature at the deal date.
    from tools.backtest_bulk_deals import Series  # local import: test-only

    dts = [f"2020-01-{i:02d}" for i in range(1, 31)]
    cls = np.array([100.0 + i for i in range(30)])
    vol = np.array([1000.0 + 10 * i for i in range(30)])
    s1 = Series(dts, cls, vol)
    k = 25
    before = event_day_features(s1, k)
    dts2 = dts + ["2020-01-31", "2020-02-01", "2020-02-02"]
    s2 = Series(
        dts2, np.append(cls, [9999.0, 1.0, 99999.0]), np.append(vol, [10_000_000.0] * 3)
    )
    after = event_day_features(s2, k)
    _check(
        before == after,
        f"event-day features changed when only FUTURE bars were appended: "
        f"{before} -> {after}",
    )
    say("  [self-test] 3. event-day features ignore all post-deal data")

    # 4. near_high is inclusive of the deal date and excludes the day before
    #    the window, i.e. it is a genuine 20-day high ending AT the deal.
    closes_hi = np.full(25, 10.0)
    closes_hi[24] = 50.0
    sh = Series(
        [f"2020-01-{i:02d}" for i in range(1, 26)],
        closes_hi,
        np.full(25, 100.0),
    )
    _check(
        event_day_features(sh, 24)[2] is True,
        "near_high failed when the deal day WAS the 20-day high",
    )
    closes_low = np.full(25, 10.0)
    closes_low[24] = 5.0
    sl = Series(
        [f"2020-01-{i:02d}" for i in range(1, 26)],
        closes_low,
        np.full(25, 100.0),
    )
    _check(
        event_day_features(sl, 24)[2] is False,
        "near_high wrongly true when the deal day was the 20-day LOW",
    )
    say("  [self-test] 4. near_high is a 20-day high ending at the deal date")

    # 5. NO SELF-MATCH, and an unmatchable event is reported, not fudged.
    #    B2 matches inside a 10 x 3 x 3 = 90-cell grid, so the toy universe
    #    must be realistically sized or every cell holds one name and nothing
    #    can ever match. Real dates carry ~3.4k names.
    def toy_universe(n=600):
        return [
            (f"S{i:04d}", float(i + 1) * 1e6, (i % 9) / 2.0, ((i // 9) % 9 - 4) / 100.0)
            for i in range(n)
        ]

    uni = {"2020-01-15": toy_universe()}
    log = defaultdict(int)
    ev = pd.DataFrame([{"symbol": "S0000", "date": "2020-01-15"}])
    picks = pick_b2(ev, uni, random.Random(SEED), log)
    _check(picks[0] is not None, "B2 found no control in a realistic universe")
    _check(picks[0] != "S0000", "B2 control matched the event symbol")
    say("  [self-test] 5. B2 never self-matches")

    # A single-name universe genuinely cannot match, and that must be COUNTED
    # rather than papered over with a silent fallback.
    solo_uni = {"2020-01-15": [("AAA", 1e7, 1.0, 0.0)]}
    log2 = defaultdict(int)
    ev2 = pd.DataFrame([{"symbol": "AAA", "date": "2020-01-15"}])
    picks2 = pick_b2(ev2, solo_uni, random.Random(SEED), log2)
    _check(picks2[0] is None, "B2 invented a control in a single-name cell")
    _check(log2["b2_no_match"] == 1, f"unmatched event not counted: {dict(log2)}")
    say("  [self-test] 5b. unmatchable events are dropped and counted")

    # 6. B2 really does tighten the match: control shares the event cell.
    ev3 = pd.DataFrame([{"symbol": "S0000", "date": "2020-01-15"}])
    pos, _cells = b2_cells(uni["2020-01-15"])
    p3 = pick_b2(ev3, uni, random.Random(SEED), defaultdict(int))[0]
    _check(
        pos[p3] == pos["S0000"],
        f"control {p3} cell {pos[p3]} != event cell {pos[chr(83)+chr(48)+chr(48)+chr(48)+chr(48)]}",
    )
    say("  [self-test] 6. B2 control shares the event's 3-way cell")

    # 7. B2 must refuse a pair that Control A would happily have made: the two
    #    sit in the SAME ADTV decile but opposite event-day terciles. Even
    #    indices are a quiet tape, odd indices a violent one, and ADTV rises
    #    with the index so adjacent names share a decile.
    opp = [
        (
            f"X{i:03d}",
            float(i + 1) * 1e6,
            0.5 if i % 2 == 0 else 4.0,
            -0.20 if i % 2 == 0 else 0.20,
        )
        for i in range(300)
    ]
    opp_uni = {"2020-01-15": opp}
    _pos, _cells = b2_cells(opp)
    _check(
        _pos["X010"][0] == _pos["X011"][0],
        "test setup broken: X010/X011 should share an ADTV decile",
    )
    log3 = defaultdict(int)
    ev4 = pd.DataFrame([{"symbol": "X010", "date": "2020-01-15"}])
    p4 = pick_b2(ev4, opp_uni, random.Random(SEED), log3)[0]
    _check(p4 is not None, "B2 found no control in test 7 universe")
    _check(
        p4 != "X011",
        f"B2 paired across event-day conditions: X010 -> {p4}",
    )
    _check(
        opp_uni["2020-01-15"][[x[0] for x in opp].index(p4)][3] < 0,
        f"B2 picked a control with the opposite event-day return: {p4}",
    )
    say("  [self-test] 7. B2 refuses to pair across event-day conditions")
    say()
    say("  ALL SELF-TESTS PASSED -- proceeding to real data")
    say()


# ---------------------------------------------------------------------------
# STEP 1 -- descriptives
# ---------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    say()
    say("=" * 100)
    say(title)
    say("=" * 100)


# NOTE on units: compute_return() is imported from the original study and
# returns a PERCENTAGE. event_day_features() deliberately returns event_ret as
# a FRACTION, because it is only ever used for (a) scale-invariant rank
# terciles and (b) the STEP 1 descriptive, which is printed as a percentage.
# Do not mix the two in an arithmetic expression.


def step1_descriptives(rows):
    rule("STEP 1 -- EVENT-DAY DESCRIPTIVES ONLY (by year and side)")
    say("  Descriptive, not a test. No hypothesis is evaluated here.")
    say("  ret = deal-day close/prev close - 1;")
    say("  volx = deal-day volume / prior-20-day average volume;")
    say("  near20hi = share of events whose deal-day close is within 2% of the")
    say("            20-day high. All features use data <= the deal date.")
    say()
    say("| Year | Side |  nEv |  mean ret |  mean volx |  near20hi |")
    say("|" + "-" * 62)
    out = []
    for year in YEARS:
        for side in ("BUY", "SELL"):
            sub = [
                r
                for r in rows
                if r["year"] == year
                and r["side"] == side
                and r["event_ret"] is not None
            ]
            if not sub:
                say(
                    f"| {year} | {side:4s} |    0 |       n/a |        n/a |"
                    f"       n/a |"
                )
                out.append({"year": year, "side": side, "n": 0})
                continue
            mr = sum(r["event_ret"] for r in sub) / len(sub)
            mv = sum(r["vol_ratio"] for r in sub) / len(sub)
            nh = sum(1 for r in sub if r["near_high"]) / len(sub)
            say(
                f"| {year} | {side:4s} | {len(sub):4d} | {mr:+9.2%} | "
                f"{mv:9.2f} | {nh:8.1%} |"
            )
            out.append(
                {
                    "year": year,
                    "side": side,
                    "n": len(sub),
                    "mean_ret": mr,
                    "mean_volx": mv,
                    "near_high": nh,
                }
            )
    say()
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-tests-only", action="store_true")
    ap.add_argument("--adtv-crore", type=float, default=2.0)
    ap.add_argument("--no-costs", action="store_true")
    ap.add_argument("--out", default=OUT_CSV)
    args = ap.parse_args(argv)

    rule("BULK-DEAL EVENT-DAY-MATCHED CONTROL (B2) -- DIAGNOSTIC")
    say("  A SEPARATELY PRE-REGISTERED DIAGNOSTIC. The a1f8b45 null result")
    say("  STANDS and is not reopened. A pass here would mean that null was a")
    say("  confound, making this a hypothesis to validate out-of-sample -- not")
    say("  a signal to trade. A fail is a clean second null.")
    say()
    say("PRE-REGISTERED (fixed before running):")
    say("  H: with the control matched on event-day conditions, BUY-minus-control")
    say("     is > 0 at h60 in >= 5 of 7 years, INCLUDING 2023 and 2025-26, AND")
    say("     BUY beats SELL under the same matching in most years.")
    say("  Anything less -> NULL.")

    if args.self_tests_only:
        run_self_tests()
        return 0
    run_self_tests()

    cal = load_calendar()
    if not check_prereq(cal):
        return 2
    cal_set = set(cal)
    bench_cache = {}
    rows = []
    drops = defaultdict(int)
    b2_log = defaultdict(int)

    deals, _stats = load_deals_clean(verbose=False)
    events_all = build_net_events(deals)
    say(f"  net (symbol,date,client) events: {len(events_all):,}")
    say(
        f"  min 20d ADTV = {args.adtv_crore} crore, costs "
        f"{'OFF' if args.no_costs else 'ON'}"
    )
    say()

    for year in YEARS:
        ev = events_all[
            (events_all["date"] >= f"{year}-01-01")
            & (events_all["date"] <= f"{year}-12-31")
        ]
        if ev.empty:
            continue
        on_cal = ev["date"].isin(cal_set)
        n_off = int((~on_cal).sum())
        if n_off:
            drops["deal_date_not_a_trading_day"] += n_off
        ev = ev[on_cal]
        if ev.empty:
            continue

        i0 = bisect_left(cal, f"{year}-01-01")
        i1 = bisect_left(cal, f"{year}-12-31")
        lo = cal[max(0, i0 - 90)]
        hi = cal[min(len(cal) - 1, i1 + 130)]
        series_map = load_year_prices(lo, hi)
        if not series_map:
            continue

        # Same cooldown as the original study, so B2 differs ONLY in matching.
        ev = ev.copy()
        ev["_pos"] = ev["date"].map({d: i for i, d in enumerate(cal)})
        ev = ev.sort_values(["symbol", "_pos", "client_name"], kind="stable")
        keep, last = [], {}
        for idx, sym, pos in zip(ev.index, ev["symbol"], ev["_pos"]):
            prev = last.get(sym)
            if prev is None or (pos - prev) >= COOLDOWN_DAYS:
                keep.append(idx)
                last[sym] = pos
        ev = ev.loc[keep]

        recs = []
        for row in ev.itertuples(index=False):
            s = series_map.get(row.symbol)
            if s is None:
                drops["no_price_series"] += 1
                continue
            k = s.k_at_or_before(row.date)
            if k is None or k < MIN_PRIOR_DAYS:
                drops["insufficient_history_lt_60d"] += 1
                continue
            adtv = float((s.notional_cs[k] - s.notional_cs[k - 20]) / 20)
            if adtv < args.adtv_crore * CRORE:
                drops["adtv_below_threshold"] += 1
                continue
            adv = float(s.vols[k - 20 : k].mean())
            if size_bucket(row.abs_qty, adv) is None:
                drops["size_ratio_below_0.25"] += 1
                continue
            er, vr, nh = event_day_features(s, k)
            if er is None or vr is None:
                drops["no_event_day_features"] += 1
                continue
            j = bisect_right(s.dates, row.date)
            if j >= len(s.dates):
                drops["no_next_trading_day"] += 1
                continue
            recs.append(
                {
                    "symbol": row.symbol,
                    "date": row.date,
                    "entry_date": s.dates[j],
                    "side": row.side,
                    "client_class": row.client_class,
                    "abs_qty": row.abs_qty,
                    "adv": adv,
                    "adtv": adtv,
                    "size_ratio": row.abs_qty / adv,
                    "entry_price": float(s.closes[j]),
                    "event_ret": er,
                    "vol_ratio": vr,
                    "near_high": nh,
                    "series_ref": s,
                }
            )
        if not recs:
            del series_map
            continue

        evr = pd.DataFrame(
            [{k2: v for k2, v in r.items() if k2 != "series_ref"} for r in recs]
        )
        evr["series_ref"] = [r["series_ref"] for r in recs]

        universe = build_b2_universe(series_map, args.adtv_crore)
        evr["control_b2"] = pick_b2(evr, universe, random.Random(SEED), b2_log)
        n_nomatch = int(evr["control_b2"].isna().sum())
        drops["b2_no_match_dropped"] += n_nomatch
        evr = evr[evr["control_b2"].notna()].reset_index(drop=True)
        if evr.empty:
            del series_map, universe
            continue

        n_ev = len(evr)
        for h in HORIZONS:
            for r in evr.itertuples(index=False):
                xd = nth_td(cal, r.entry_date, h)
                exit_px = r.series_ref.close_at_or_before(xd) if xd else None
                gross = compute_return(r.entry_price, exit_px)
                net = gross if args.no_costs else cost_adjusted_return(gross)
                b_in = get_benchmark_close(r.entry_date, bench_cache)
                b_out = get_benchmark_close(xd, bench_cache) if xd else None
                bench = compute_return(b_in, b_out)
                cs = series_map[r.control_b2]
                cj = bisect_right(cs.dates, r.date)
                c_entry = float(cs.closes[cj]) if cj < len(cs.dates) else None
                c_exit = cs.close_at_or_before(xd) if xd else None
                c_gross = compute_return(c_entry, c_exit)
                c_net = c_gross if args.no_costs else cost_adjusted_return(c_gross)
                rows.append(
                    {
                        "year": year,
                        "symbol": r.symbol,
                        "date": r.date,
                        "entry_date": r.entry_date,
                        "exit_date": xd,
                        "side": r.side,
                        "client_class": r.client_class,
                        "horizon": h,
                        "event_ret": r.event_ret,
                        "vol_ratio": r.vol_ratio,
                        "near_high": r.near_high,
                        "adtv": r.adtv,
                        "size_ratio": r.size_ratio,
                        "excess": None if net is None or bench is None else net - bench,
                        "control_b2": r.control_b2,
                        "b2_excess": None
                        if c_net is None or bench is None
                        else c_net - bench,
                    }
                )
        del series_map, universe, evr
        print(f"    {year}: events with a B2 control = {n_ev:5,}")

    res = pd.DataFrame(rows)
    if res.empty:
        rule("RESULT")
        say("  No events survived. Nothing to conclude.")
        return 3
    res["b2_diff"] = res["excess"] - res["b2_excess"]
    res.to_csv(args.out, index=False)
    say()
    say(f"  per-event results -> {args.out} ({len(res):,} rows)")

    rule("STEP 1 -- EVENT-DAY DESCRIPTIVES ONLY (by year and side)")
    say("  ret = deal-day close/prev close - 1;")
    say("  volx = deal-day volume / prior-20-day average volume;")
    say("  near20hi = share of events whose deal-day close is within 2% of the")
    say("            20-day high. All features use data <= the deal date.")
    say()
    say("| Year | Side |  nEv |  mean ret |  mean volx |  near20hi |")
    say("|" + "-" * 62)
    for year in YEARS:
        for side in ("BUY", "SELL"):
            sub = res[
                (res["year"] == year)
                & (res["side"] == side)
                & (res["event_ret"].notna())
            ]
            if sub.empty:
                say(
                    f"| {year} | {side:4s} |    0 |       n/a |        n/a |"
                    f"       n/a |"
                )
                continue
            say(
                f"| {year} | {side:4s} | {len(sub):4d} | "
                f"{sub['event_ret'].mean():+9.2%} | "
                f"{sub['vol_ratio'].mean():9.2f} | "
                f"{sub['near_high'].mean():8.1%} |"
            )
    say()

    rule("STEP 2/3 -- PER-YEAR RESULT vs CONTROL B2")
    say(
        f"  (min 20d ADTV = {args.adtv_crore} crore, costs "
        f"{'OFF' if args.no_costs else 'ON'})"
    )
    say("  Excess = event excess vs ^NSEI; B2 = matched control's excess vs")
    say("  ^NSEI; Diff = per-date mean(event - control), averaged across dates.")
    say("  t is across dates. Never pooled across years. THIN when")
    say("  n_dates < 30 or n_events < 100.")
    say()
    say(
        "| Year | Side |    H |   nEv | nDates |  Excess |     B2 |   Diff |"
        "      t | Flag |"
    )
    say("|" + "-" * 78)
    per_year = {}
    for year in YEARS:
        for side in ("BUY", "SELL"):
            for h in HORIZONS:
                sub = res[
                    (res["year"] == year)
                    & (res["side"] == side)
                    & (res["horizon"] == h)
                ]
                if sub.empty:
                    continue
                agg = aggregate_per_date(sub, "b2_diff")
                if agg is None:
                    continue
                ex = aggregate_per_date(sub, "excess")
                ctl = aggregate_per_date(sub, "b2_excess")
                flag = thin(agg["n_dates"], agg["n_events"])
                say(
                    f"| {year} | {side:4s} | {h:4d} | {agg['n_events']:5d} | "
                    f"{agg['n_dates']:5d} | {ex['mean']:+8.2f} | "
                    f"{ctl['mean']:+7.2f} | {agg['mean']:+7.2f} | "
                    f"{(agg['t'] if agg['t'] is not None else float('nan')):+7.2f}"
                    f" | {flag:4s} |"
                )
                per_year[(year, side, h)] = agg
    say()

    rule("B2 MATCHING DIAGNOSTICS / DROP COUNTS")
    for kk in sorted(drops):
        say(f"  {kk:32s} {drops[kk]:>8,}")
    for kk in sorted(b2_log):
        say(f"  b2_log.{kk:24s} {b2_log[kk]:>8,}")
    say()

    rule("PRE-REGISTERED CRITERIA EVALUATION")
    pos_years, neg_detail = [], []
    for year in YEARS:
        a = per_year.get((year, "BUY", 60))
        if a is None or a["mean"] is None:
            neg_detail.append(f"      {year}: no data")
            continue
        ok = a["mean"] > 0
        if ok:
            pos_years.append(year)
        neg_detail.append(
            f"      {year}: {a['mean']:+.2f}  " f"[{'OK' if ok else 'FAIL'}]"
        )
    say("  (i) h60 BUY-minus-B2 > 0 in " f"{len(pos_years)}/7 years: {pos_years}")
    for line in neg_detail:
        say(line)
    crit_i = (
        len(pos_years) >= 5
        and 2023 in pos_years
        and 2025 in pos_years
        and 2026 in pos_years
    )

    say()
    say("  (ii) BUY vs SELL falsification at h60:")
    buy_better, comparable = 0, 0
    for year in YEARS:
        b = per_year.get((year, "BUY", 60))
        s = per_year.get((year, "SELL", 60))
        if b is None or s is None or b["mean"] is None or s["mean"] is None:
            say(f"      {year}: BUY n/a, SELL n/a (no data)")
            continue
        comparable += 1
        if b["mean"] > s["mean"]:
            buy_better += 1
            say(
                f"      {year}: BUY {b['mean']:+.2f}  vs  SELL {s['mean']:+.2f}"
                f"  -> BUY better"
            )
        else:
            say(
                f"      {year}: BUY {b['mean']:+.2f}  vs  SELL {s['mean']:+.2f}"
                f"  -> SELL better"
            )
    say(f"      BUY better in {buy_better}/{comparable} comparable years")
    crit_ii = comparable > 0 and buy_better > comparable / 2

    say()
    say(f"  CRITERION (i)  : {'PASS' if crit_i else 'FAIL'}")
    say(f"  CRITERION (ii) : {'PASS' if crit_ii else 'FAIL'}")
    say()
    if crit_i and crit_ii:
        say("  VERDICT: BOTH CRITERIA MET under event-day matching.")
        say("  READ THIS AS: the a1f8b45 null is EXPLAINED BY A CONFOUND, not")
        say("  overturned. Event-day conditions were doing the work. This is a")
        say("  hypothesis to validate on events after a fixed cutoff date -- it")
        say("  is NOT a validated signal and NOT a reason to trade.")
    else:
        say("  VERDICT: NULL RESULT. Event-day matching does not rescue the")
        say("  signal, so the gap is not explained by event-day conditions")
        say("  alone. This is a second, independent null. No thresholds were")
        say("  retuned.")

    # ---------------- STEP 4: exploratory, power-gated ----------------
    rule("STEP 4 -- EXPLORATORY (NOT pre-registered), power-gated")
    say("  mf_insurance BUY vs B2, by year. Reported ONLY if there are at")
    say(f"  least {MIN_MF_EVENTS} such events; otherwise 'underpowered'.")
    say()
    mf = res[(res["side"] == "BUY") & (res["client_class"] == "mf_insurance")]
    n_mf = mf["symbol"].nunique()
    n_mf_rows = len(mf[mf["horizon"] == 60])
    if n_mf_rows < MIN_MF_EVENTS:
        say(
            f"  underpowered: only {n_mf_rows} mf_insurance BUY events at h60 "
            f"({n_mf} distinct symbols),"
        )
        say(
            f"  below the pre-registered floor of {MIN_MF_EVENTS}. Not reported, "
            f"not interpreted."
        )
    else:
        say(
            f"  {n_mf_rows} mf_insurance BUY events at h60 "
            f"({n_mf} distinct symbols) -- reporting."
        )
        say()
        say("| Year |   nEv | nDates |  Excess |     B2 |   Diff |      t |")
        say("|" + "-" * 62)
        for year in YEARS:
            sub = mf[(mf["year"] == year) & (mf["horizon"] == 60)]
            if sub.empty:
                continue
            agg = aggregate_per_date(sub, "b2_diff")
            ex = aggregate_per_date(sub, "excess")
            ctl = aggregate_per_date(sub, "b2_excess")
            say(
                f"| {year} | {agg['n_events']:5d} | {agg['n_dates']:5d} | "
                f"{ex['mean']:+8.2f} | {ctl['mean']:+7.2f} | "
                f"{agg['mean']:+7.2f} | "
                f"{(agg['t'] if agg['t'] is not None else float('nan')):+7.2f} |"
            )
        say()
        say("  EXPLORATORY ONLY: post-hoc, not pre-registered, and it inherits")
        say("  every limitation above. It is a hypothesis, not a finding.")

    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
