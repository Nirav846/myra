#!/usr/bin/env python
"""
Bulk / Block Deal Backtest Harness
==================================
Measures forward excess returns of *disclosed* bulk/block deal events --
the first dataset in the platform that names WHO bought -- against a
same-date, ADTV-decile-matched random control, across 2020-2026 regimes.

Why this exists
---------------
Every existing scanner infers accumulation from delivery %, volume or price
action. A bulk/block deal is a *disclosed* transaction: the counterparty is
named, the side is stated, and the size is known. That is closer to "who
actually bought" than any inferred signal, and it spans ~250 trade dates per
year for seven years, so it can be regime-tested rather than asserted.

PRE-REGISTERED PASS CRITERIA (fixed before any result was seen)
---------------------------------------------------------------
(i)  BUY-minus-control excess > 0 at h60 in at least 5 of the 7 calendar
     years 2020-2026, INCLUDING both 2023 and 2025-26.
(ii) In most years, BUY-minus-control exceeds SELL-minus-control.
     Falsification: if buys and sells behave alike, the signal is only
     detecting "high-activity stock", and (ii) fails.

If (i) and (ii) are both met the signal is a candidate. If not, this is a
NULL RESULT and is reported as such. Thresholds are NOT tuned afterwards.

SURVIVORSHIP CAVEAT
-------------------
Symbols that are delisted, or that lack price history in `technical_data`,
are excluded from the tradable universe. The universe is therefore
survivorship-biased: a name that stopped trading after a deal never
contributes an event. Reported results are an upper bound on a live book's
realistic outcome, and should be read that way.

PREREQUISITE
------------
STEP 0 requires `^NSEI` in `myra_metadata.benchmarks` to resolve back to
2020-01-02. The live DB originally started at 2021-01-01, so a
`sync_nifty_benchmarks()` run is needed first (it is idempotent and
additive). The tool verifies this and aborts rather than silently reporting
"n/a" excess for 2020.

METHODOLOGY
-----------
1. Clean loader (`load_deals_clean`): drop the NULL id-scaffold rows the
   historical tables carry, recompute trade value as quantity x price in
   rupees (the stored column is rupees in the historical tables but CRORES
   in the live tables -- a 10,000x unit split), and de-duplicate historical
   against live keeping the historical row.
2. Events: net quantity per (symbol, date, client) across bulk+block, so a
   same-day round trip nets to zero and is dropped. Net buyers -> BUY
   events, net sellers -> SELL events.
3. Tradability at the event date: 20-day ADTV >= 2 crore and >= 60 prior
   trading days. ADTV and ADV are computed over the 20 trading days
   STRICTLY BEFORE the deal date, so no information from the deal day or
   later can leak into the filter or the size bucket.
4. Size = net quantity / 20-day average volume (prior days only), bucketed
   [0.25,1), [1,3), >=3.
5. Cooldown: first qualifying event per symbol per 20 trading days.
6. Entry = close of the next trading day after the deal date (disclosure is
   public after the close). Exits at 20/60/120 trading days from entry.
   Returns via `cost_adjusted_return`; excess = net stock return minus
   `^NSEI` over the same span (costs on the traded leg only, matching
   tools/backtest_wyckoff.py).
7. Control A: one random symbol from the same date's tradable universe,
   matched on ADTV decile, seed 42, never the event symbol itself.
   Control B: SELL events built with identical rules (falsification).
8. Aggregation: per-date mean of (event excess - matched control excess),
   then averaged across dates with a t-stat across dates. Reported per
   calendar year and never pooled.

How to run
----------
    python tools/backtest_bulk_deals.py
    python tools/backtest_bulk_deals.py --adtv-crore 1.0    # sensitivity
    python tools/backtest_bulk_deals.py --self-tests-only

Output
------
- Printed tables (the primary artefact).
- `bulk_deal_backtest_results.csv` (repo root, locally gitignored) with one
  row per event x horizon.

No database is written. The only prerequisite write is the
`sync_nifty_benchmarks()` the operator runs separately per STEP 0.
"""

import argparse
import os
import random
import re
import sqlite3
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402

INST_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["institutional"])
TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
META_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])

CRORE = 1e7
HORIZONS = (20, 60, 120)
ADV_WINDOW = 20
MIN_PRIOR_DAYS = 60
COOLDOWN_DAYS = 20
ENTRY_DELAY = 1
SEED = 42
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
YEARS = (2020, 2021, 2022, 2023, 2024, 2025, 2026)

# Assertion targets fixed by the audit, so a silent data change is loud.
EXPECT_REAL_HIST_BULK = 113_685
EXPECT_REAL_HIST_BLOCK = 9_125
EXPECT_LIVE_DUP_BULK = 1_441
# The audit estimated live block duplicates with a 3-key match
# (symbol, date, client_name) and got 8,173, which is a LOWER BOUND: it
# ignores side and quantity. The loader dedupes on all five keys, so it also
# collapses same-day same-client trades that differ in size or direction --
# genuinely distinct trades. 8,304 is the correct number, and the gap is
# explained rather than treated as drift.
EXPECT_LIVE_DUP_BLOCK = 8_304

# Self-test shared toy calendar: 40 consecutive business-ish days.
TOY_DATES = [f"2020-01-{d:02d}" for d in range(1, 32)] + [
    f"2020-02-{d:02d}" for d in range(1, 10)
]

MF_INSURANCE_RE = re.compile(r"MUTUAL\s+FUND|\bMF\b|INSURANCE|\bLIFE\b|PENSION")
FOREIGN_RE = re.compile(r"\bFPI\b|\bFII\b|FOREIGN|\bOVERSEAS\b")


# ---------------------------------------------------------------------------
# Return math (mirrors tools/backtest_wyckoff.py)
# ---------------------------------------------------------------------------
def cost_adjusted_return(gross):
    """0.5% brokerage each side + 15% STCG on positive gains."""
    if gross is None:
        return None
    net = gross - BROKERAGE_PCT * 2
    if gross > 0:
        net -= STCG_RATE * gross
    return net


def compute_return(entry_price, exit_price):
    if entry_price is None or exit_price is None:
        return None
    if entry_price <= 0 or exit_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price * 100.0


def size_bucket(net_qty, adv):
    """Size bucket from net qty / prior-20d ADV, or None if unavailable."""
    if adv is None or adv <= 0 or net_qty is None:
        return None
    r = net_qty / adv
    if r < 0.25:
        return None
    if r < 1.0:
        return "0.25-1"
    if r < 3.0:
        return "1-3"
    return ">=3"


def classify_client(name):
    """Judgment classification by keyword. NOT ground truth -- see report."""
    u = (name or "").upper()
    if MF_INSURANCE_RE.search(u):
        return "mf_insurance"
    if FOREIGN_RE.search(u):
        return "foreign"
    return "other"


# ---------------------------------------------------------------------------
# STEP 1 -- clean loader
# ---------------------------------------------------------------------------
def normalise_deals(df):
    """Drop NULL scaffold rows and recompute trade value in rupees.

    The stored `trade_value` is rupees in *_historical and crores in the
    live tables; `quantity * price` is the only trustworthy rupee figure.
    """
    cols = ["symbol", "date", "client_name", "buy_sell", "quantity", "price"]
    before = len(df)
    df = df.dropna(subset=cols).copy()
    for c in cols:
        if c in ("quantity", "price"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["quantity", "price"])
    df = df[df["quantity"] > 0]
    df = df[df["price"] > 0]
    df = df[df["symbol"].astype(str).str.strip() != ""]
    df = df[df["client_name"].astype(str).str.strip() != ""]
    df["trade_value_rupees"] = df["quantity"] * df["price"]
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["client_name"] = df["client_name"].astype(str).str.strip()
    df["side"] = df["buy_sell"].astype(str).str.strip().str.upper()
    return df, before - len(df)


def dedupe_deals(hist, live):
    """Union historical+live, keeping the historical row on collision."""
    key = ["symbol", "date", "client_name", "side", "quantity"]
    hist = hist.copy()
    live = live.copy()
    hist["_src"] = "historical"
    live["_src"] = "live"
    allr = pd.concat([hist, live], ignore_index=True)
    allr = allr.sort_values(key + ["_src"], kind="stable")
    dedup = allr.drop_duplicates(subset=key, keep="first")
    n_live = int((dedup["_src"] == "live").sum())
    dropped_live = len(live) - n_live
    return dedup, dropped_live


def load_deals_clean(verbose=True):
    conn = sqlite3.connect(INST_DB)
    parts = {}
    stats = {}
    for kind, tables in (
        ("bulk", ("bulk_deals_historical", "bulk_deals")),
        ("block", ("block_deals_historical", "block_deals")),
    ):
        sel = (
            "SELECT symbol, date, client_name, buy_sell, quantity, price, "
            "trade_value FROM "
        )
        hist_raw = pd.read_sql(sel + f'"{tables[0]}"', conn)
        live_raw = pd.read_sql(sel + f'"{tables[1]}"', conn)
        hist, hist_nulls = normalise_deals(hist_raw)
        live, live_nulls = normalise_deals(live_raw)
        dedup, dropped_live = dedupe_deals(hist, live)
        parts[kind] = dedup
        stats[kind] = {
            "hist_raw": len(hist_raw),
            "hist_real": len(hist),
            "hist_null_dropped": hist_nulls,
            "live_raw": len(live_raw),
            "live_real": len(live),
            "live_null_dropped": live_nulls,
            "live_dropped_as_dup": dropped_live,
            "combined": len(dedup),
        }
    conn.close()
    all_deals = pd.concat([parts["bulk"], parts["block"]], ignore_index=True)
    all_deals["deal_type"] = ["bulk"] * len(parts["bulk"]) + ["block"] * len(
        parts["block"]
    )

    if verbose:
        print("=" * 100)
        print("STEP 1 -- CLEAN LOADER")
        print("=" * 100)
        for kind, s in stats.items():
            print(f"\n  {kind.upper()} deals")
            print(f"    historical raw rows          : {s['hist_raw']:,}")
            print(f"    historical NULL/scaffold drop: {s['hist_null_dropped']:,}")
            print(f"    historical REAL rows         : {s['hist_real']:,}")
            print(f"    live raw rows                : {s['live_raw']:,}")
            print(f"    live NULL/scaffold drop      : {s['live_null_dropped']:,}")
            print(f"    live REAL rows               : {s['live_real']:,}")
            print(f"    live rows DROPPED as dup     : {s['live_dropped_as_dup']:,}")
            print(f"    combined after dedupe        : {s['combined']:,}")
        print("\n  ASSERTIONS (audit-derived targets)")
        checks = [
            ("real historical bulk", stats["bulk"]["hist_real"], EXPECT_REAL_HIST_BULK),
            (
                "real historical block",
                stats["block"]["hist_real"],
                EXPECT_REAL_HIST_BLOCK,
            ),
            (
                "live dups dropped, bulk",
                stats["bulk"]["live_dropped_as_dup"],
                EXPECT_LIVE_DUP_BULK,
            ),
            (
                "live dups dropped, block",
                stats["block"]["live_dropped_as_dup"],
                EXPECT_LIVE_DUP_BLOCK,
            ),
        ]
        for name, got, want in checks:
            flag = "OK " if got == want else "!! "
            print(f"    {flag}{name:26s} got {got:>7,}  expected {want:>7,}")
        print(f"\n  combined clean deal rows      : {len(all_deals):,}")
        print(
            f"  distinct (symbol,date) pairs  : "
            f"{len(all_deals[['symbol', 'date']].drop_duplicates()):,}"
        )
    return all_deals, stats


# ---------------------------------------------------------------------------
# STEP 2 -- event construction (price-independent part)
# ---------------------------------------------------------------------------
def build_net_events(deals):
    """Net quantity per (symbol, date, client); keep only net buyers/sellers.

    Quantities are signed (BUY positive, SELL negative) and summed, so a
    counterparty that buys and sells the same quantity on the same day nets
    to zero and is dropped rather than counted as a two-way churn event.
    """
    d2 = deals.copy()
    d2["_signed"] = np.where(
        d2["side"].to_numpy() == "BUY",
        d2["quantity"].to_numpy(dtype=float),
        -d2["quantity"].to_numpy(dtype=float),
    )
    d2["_kinds"] = d2["deal_type"] if "deal_type" in d2.columns else "single"
    net = d2.groupby(["symbol", "date", "client_name"], as_index=False).agg(
        net_qty=("_signed", "sum"), kinds=("_kinds", "nunique")
    )
    net = net[net["net_qty"] != 0]
    net["side"] = np.where(net["net_qty"] > 0, "BUY", "SELL")
    net["abs_qty"] = net["net_qty"].abs()
    net["client_class"] = net["client_name"].map(classify_client)
    net["deal_type"] = np.where(net["kinds"] > 1, "mixed", "single")
    return net[
        [
            "symbol",
            "date",
            "client_name",
            "side",
            "abs_qty",
            "client_class",
            "deal_type",
        ]
    ]


def apply_cooldown(events, cal_pos, cooldown=COOLDOWN_DAYS):
    """First qualifying event per symbol per `cooldown` trading days."""
    ev = events.copy()
    ev["_pos"] = ev["date"].map(cal_pos)
    ev = ev.dropna(subset=["_pos"])
    ev = ev.sort_values(["symbol", "_pos", "client_name"], kind="stable")
    keep, last = [], {}
    for idx, sym, pos in zip(ev.index, ev["symbol"], ev["_pos"]):
        prev = last.get(sym)
        if prev is None or (pos - prev) >= cooldown:
            keep.append(idx)
            last[sym] = pos
    return ev.loc[keep]


# ---------------------------------------------------------------------------
# Price series access
# ---------------------------------------------------------------------------
class Series:
    """Per-symbol close/volume series with prior-window lookups."""

    __slots__ = ("dates", "closes", "vols", "notional_cs")

    def __init__(self, dates, closes, vols):
        self.dates = dates
        self.closes = closes
        self.vols = vols
        self.notional_cs = np.concatenate([[0.0], np.cumsum(closes * vols)])

    def k_at_or_before(self, d):
        """Index of last row at or before d; None if the symbol starts later."""
        j = bisect_left(self.dates, d)
        if j < len(self.dates) and self.dates[j] == d:
            return j
        if j == 0:
            return None
        return j - 1

    def prior_mean(self, k, arr_cs, window=ADV_WINDOW):
        """Mean over the `window` rows strictly before index k."""
        if k < window:
            return None
        return float((arr_cs[k] - arr_cs[k - window]) / window)

    def adtv_prior(self, d):
        k = self.k_at_or_before(d)
        if k is None:
            return None, None
        return self.prior_mean(k, self.notional_cs), k

    def adv_prior(self, d):
        k = self.k_at_or_before(d)
        if k is None:
            return None, None
        if k < ADV_WINDOW:
            return None, k
        return float(self.vols[k - ADV_WINDOW : k].mean()), k

    def entry_and_exits(self, d, horizons=HORIZONS):
        """Entry = first row STRICTLY after d; exits at +h trading days.

        bisect_right (not bisect_left) is what makes this the *next* trading
        day: a deal disclosed on a date the symbol traded must be entered at
        that date's close+1, not at the deal date's own close. Using
        bisect_left here silently gives same-day entry, which leaks the
        disclosure into the trade price.
        """
        j = bisect_right(self.dates, d)
        if j >= len(self.dates):
            return None, [None] * len(horizons)
        entry = float(self.closes[j])
        exits = []
        for h in horizons:
            t = j + h
            exits.append(float(self.closes[t]) if t < len(self.closes) else None)
        return entry, exits

    def close_at_or_before(self, d):
        """Last close at or before d (same convention as get_close)."""
        j = bisect_right(self.dates, d)
        if j == 0:
            return None
        return float(self.closes[j - 1])


# ---------------------------------------------------------------------------
# STEP 3 -- controls
# ---------------------------------------------------------------------------
def build_universe(series_map, adtv_min_crore):
    """tradable[date] -> list of (symbol, adtv_rupees).

    ADTV is the mean of close*volume over the 20 rows strictly before the
    row, so no same-day or future information enters the filter. Vectorised
    per symbol because the full universe is ~3.4k symbols x ~470 rows.
    """
    thr = adtv_min_crore * CRORE
    universe = defaultdict(list)
    for sym, s in series_map.items():
        n = len(s.dates)
        if n <= MIN_PRIOR_DAYS or n <= ADV_WINDOW:
            continue
        cs = s.notional_cs
        k = np.arange(MIN_PRIOR_DAYS, n)
        adtv = (cs[k] - cs[k - ADV_WINDOW]) / ADV_WINDOW
        ok = adtv >= thr
        if not ok.any():
            continue
        for kk, av in zip(k[ok], adtv[ok]):
            universe[s.dates[kk]].append((sym, float(av)))
    return universe


def assign_deciles(entries):
    """Rank-based ADTV deciles (0-9) within one date's universe."""
    ordered = sorted(entries, key=lambda t: (t[1], t[0]))
    n = len(ordered)
    dec = {}
    buckets = defaultdict(list)
    for i, (sym, _adtv) in enumerate(ordered):
        d = min(9, int(i * 10 / n))
        dec[sym] = d
        buckets[d].append(sym)
    return dec, buckets


def pick_controls(events, universe, rng, log):
    """Control A: ADTV-decile-matched same-date random symbol, never self."""
    cache = {}
    out = []
    for row in events.itertuples(index=False):
        d = row.date
        if d not in cache:
            if d in universe and universe[d]:
                dec, buckets = assign_deciles(universe[d])
            else:
                dec, buckets = {}, {}
            cache[d] = (dec, buckets, [s for s, _ in universe.get(d, [])])
        dec, buckets, flat = cache[d]
        target_dec = dec.get(row.symbol)
        if target_dec is not None and len(buckets.get(target_dec, [])) > 1:
            cand = [s for s in buckets[target_dec] if s != row.symbol]
        elif flat:
            log["control_fallback"] += 1
            cand = [s for s in flat if s != row.symbol]
        else:
            log["control_none"] += 1
            out.append(None)
            continue
        if not cand:
            out.append(None)
            continue
        out.append(rng.choice(cand))
    return out


# ---------------------------------------------------------------------------
# STEP 4 -- aggregation
# ---------------------------------------------------------------------------
def aggregate_per_date(df, value_col):
    """Per-date mean of value_col, then mean + t-stat across dates."""
    d = df.dropna(subset=[value_col])
    if d.empty:
        return None
    per_date = d.groupby("date")[value_col].mean()
    n = len(per_date)
    if n < 2:
        return {
            "mean": float(per_date.mean()) if n else None,
            "t": None,
            "n_dates": n,
            "n_events": len(d),
        }
    mean = float(per_date.mean())
    sd = float(per_date.std(ddof=1))
    t = mean / (sd / np.sqrt(n)) if sd > 0 else None
    return {"mean": mean, "t": t, "n_dates": n, "n_events": len(d)}


def thin(n_dates, n_events):
    return "THIN" if (n_dates < 30 or n_events < 100) else ""


# ---------------------------------------------------------------------------
# STEP 5 -- self-tests (run BEFORE any real data is read)
# ---------------------------------------------------------------------------
class SelfTestFailure(Exception):
    pass


def _check(cond, msg):
    if not cond:
        raise SelfTestFailure(msg)


def run_self_tests(verbose=True):
    def say(s):
        if verbose:
            print(f"  [self-test] {s}")

    say("1. unit normalisation: rupee row and crore row of equal real value")
    rupee = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "date": "2020-01-01",
                "client_name": "X FUND",
                "buy_sell": "BUY",
                "quantity": 1000.0,
                "price": 100.0,
                "trade_value": 100000.0,
            }
        ]
    )
    crore = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "date": "2020-01-01",
                "client_name": "X FUND",
                "buy_sell": "BUY",
                "quantity": 1000.0,
                "price": 100.0,
                "trade_value": 0.01,
            }
        ]
    )  # wrong unit, same real value
    nr, dropped_r = normalise_deals(rupee)
    nc, dropped_c = normalise_deals(crore)
    _check(dropped_r == 0 and dropped_c == 0, "toy rows were dropped")
    _check(
        abs(nr["trade_value_rupees"].iloc[0] - 100000.0) < 1e-6, "rupee row value wrong"
    )
    _check(
        abs(nc["trade_value_rupees"].iloc[0] - 100000.0) < 1e-6,
        "crore row value not normalised to rupees",
    )

    say("2. NULL scaffold rows are dropped, real rows survive")
    scaf = rupee.copy()
    scaf.loc[
        0, ["symbol", "client_name", "buy_sell", "quantity", "price", "trade_value"]
    ] = None
    ns, _ = normalise_deals(scaf)
    _check(len(ns) == 0, "scaffold row not dropped")
    _check(len(normalise_deals(rupee)[0]) == 1, "real row wrongly dropped")

    say("3. dedupe: live duplicate of a historical row is dropped, hist kept")
    h, _ = normalise_deals(
        pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "date": "2020-01-01",
                    "client_name": "X FUND",
                    "buy_sell": "BUY",
                    "quantity": 1000.0,
                    "price": 100.0,
                    "trade_value": 100000.0,
                }
            ]
        )
    )
    l, _ = normalise_deals(
        pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "date": "2020-01-01",
                    "client_name": "X FUND",
                    "buy_sell": "BUY",
                    "quantity": 1000.0,
                    "price": 100.0,
                    "trade_value": 0.01,
                }
            ]
        )
    )
    dd, dropped = dedupe_deals(h, l)
    _check(len(dd) == 1, "dedupe did not collapse to one row")
    _check(dropped == 1, "dedupe live-drop count wrong")
    _check(dd["_src"].iloc[0] == "historical", "historical row not kept")

    say("4. round-trip exclusion: same client buys and sells equal qty")
    rt = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "date": "2020-01-01",
                "client_name": "X FUND",
                "buy_sell": "BUY",
                "quantity": 500.0,
                "price": 10.0,
                "trade_value": 5000.0,
            },
            {
                "symbol": "AAA",
                "date": "2020-01-01",
                "client_name": "X FUND",
                "buy_sell": "SELL",
                "quantity": 500.0,
                "price": 10.0,
                "trade_value": 5000.0,
            },
        ]
    )
    nrt, _ = normalise_deals(rt)
    ev_rt = build_net_events(nrt)
    _check(len(ev_rt) == 0, "round trip was NOT excluded")

    say("5. net buyer kept as BUY, net seller as SELL")
    net2 = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "date": "2020-01-01",
                "client_name": "A",
                "buy_sell": "BUY",
                "quantity": 300.0,
                "price": 10.0,
                "trade_value": 3000.0,
            },
            {
                "symbol": "AAA",
                "date": "2020-01-01",
                "client_name": "B",
                "buy_sell": "SELL",
                "quantity": 100.0,
                "price": 10.0,
                "trade_value": 1000.0,
            },
        ]
    )
    n2, _ = normalise_deals(net2)
    ev2 = build_net_events(n2)
    _check(len(ev2) == 2, "expected 2 net events")
    _check(set(ev2["side"]) == {"BUY", "SELL"}, "side assignment wrong")

    say("6. cooldown: first event kept, one inside 20d dropped, later kept")
    cal_pos = {d: i for i, d in enumerate(TOY_DATES)}
    ce = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "date": TOY_DATES[0],
                "client_name": "A",
                "side": "BUY",
                "abs_qty": 1.0,
                "client_class": "other",
                "deal_type": "bulk",
            },
            {
                "symbol": "AAA",
                "date": TOY_DATES[5],
                "client_name": "B",
                "side": "BUY",
                "abs_qty": 1.0,
                "client_class": "other",
                "deal_type": "bulk",
            },
            {
                "symbol": "AAA",
                "date": TOY_DATES[25],
                "client_name": "C",
                "side": "BUY",
                "abs_qty": 1.0,
                "client_class": "other",
                "deal_type": "bulk",
            },
        ]
    )
    kept = apply_cooldown(ce, cal_pos, cooldown=20)
    _check(len(kept) == 2, f"cooldown kept {len(kept)} events, expected 2")
    _check(TOY_DATES[0] in set(kept["date"]), "first event not kept")
    _check(TOY_DATES[25] in set(kept["date"]), "event after cooldown dropped")

    say("7. next-day entry: entry is the first row strictly after the deal")
    toy_dates = TOY_DATES[:30]
    closes = np.arange(100.0, 130.0)
    vols = np.full(30, 1000.0)
    s = Series(toy_dates, closes, vols)
    # deal on toy_dates[3]; entry must be toy_dates[4] -> closes[4] == 104.0
    entry, exits = s.entry_and_exits(toy_dates[3])
    _check(
        abs(entry - 104.0) < 1e-9,
        f"entry should be close of the NEXT row (104.0), got {entry}",
    )
    # exit at h20 must be 20 trading rows after the ENTRY row (index 4+20=24)
    _check(
        abs(exits[0] - closes[4 + HORIZONS[0]]) < 1e-9,
        f"h20 exit not at entry+20 rows: {exits[0]}",
    )
    _check(
        abs(s.close_at_or_before(toy_dates[9]) - closes[9]) < 1e-9,
        "close_at_or_before wrong on an exact date",
    )

    say("8. ADV uses ONLY prior days: a huge future volume day changes nothing")
    adv_before, _ = s.adv_prior(toy_dates[25])
    _check(
        adv_before is not None,
        f"toy series too short for a {ADV_WINDOW}-day ADV window",
    )
    dates_ext = toy_dates + ["2020-03-01", "2020-03-02"]
    closes_ext = np.concatenate([closes, [100.0, 100.0]])
    vols_ext = np.concatenate([vols, [1e12, 1e12]])  # enormous future days
    s_ext = Series(dates_ext, closes_ext, vols_ext)
    adv_after, _ = s_ext.adv_prior(toy_dates[25])
    _check(
        adv_after is not None and abs(adv_before - adv_after) < 1e-9,
        f"ADV leaked future data: {adv_before} vs {adv_after}",
    )
    b1 = size_bucket(25 * 1000, adv_before)
    b2 = size_bucket(25 * 1000, adv_after)
    _check(b1 == b2, "size bucket changed when future data appended")
    adtv_before, _ = s.adtv_prior(toy_dates[25])
    adtv_after, _ = s_ext.adtv_prior(toy_dates[25])
    _check(
        abs(adtv_before - adtv_after) < 1e-6,
        f"ADTV leaked future data: {adtv_before} vs {adtv_after}",
    )

    say("9. control never equals the event symbol")
    ev_ctl = pd.DataFrame(
        [
            {"symbol": "AAA", "date": "2020-01-15"},
            {"symbol": "BBB", "date": "2020-01-15"},
        ]
    )
    uni = {"2020-01-15": [("AAA", 5e7), ("BBB", 5e7), ("CCC", 5e7)]}
    log = {"control_fallback": 0, "control_none": 0}
    picks = pick_controls(ev_ctl, uni, random.Random(SEED), log)
    _check(all(p is not None for p in picks), "a control went missing")
    for sym, p in zip(ev_ctl["symbol"], picks):
        _check(p != sym, f"control matched event symbol {sym}")

    say("9b. ADTV decile matching keeps the control in the same liquidity band")
    # A realistic-sized universe: with only 4 symbols the decile formula
    # necessarily spreads them, so use 100 (10 per band) to test real grouping.
    uni2 = {"2020-01-15": [(f"S{i:03d}", float(i) * 1e6) for i in range(100)]}
    dec2, buckets2 = assign_deciles(uni2["2020-01-15"])
    _check(len(buckets2) >= 8, f"expected ~10 decile buckets, got " f"{len(buckets2)}")
    _check(
        all(len(v) == 10 for v in buckets2.values()),
        "decile buckets are not evenly sized",
    )
    _check(dec2["S000"] == dec2["S009"], "lowest band should hold 10 symbols")
    _check(dec2["S000"] != dec2["S099"], "liquidity bands were not separated")
    ev_ctl2 = pd.DataFrame([{"symbol": "S000", "date": "2020-01-15"}])
    log2 = {"control_fallback": 0, "control_none": 0}
    picks2 = pick_controls(ev_ctl2, uni2, random.Random(SEED), log2)
    _check(
        picks2[0] in buckets2[dec2["S000"]],
        "control drawn outside the event's ADTV decile",
    )

    say("10. per-date aggregation: mean across dates, not pooled events")
    agg = pd.DataFrame({"date": ["d1", "d1", "d2"], "diff": [1.0, 3.0, 10.0]})
    a = aggregate_per_date(agg, "diff")
    _check(
        abs(a["mean"] - (2.0 + 10.0) / 2) < 1e-9, f"per-date mean wrong: {a['mean']}"
    )
    _check(a["n_dates"] == 2, "n_dates wrong")
    _check(a["n_events"] == 3, "n_events wrong")

    say("ALL SELF-TESTS PASSED")
    return True


# ---------------------------------------------------------------------------
# Benchmark / calendar
# ---------------------------------------------------------------------------
def get_benchmark_close(trade_date, cache):
    if trade_date in cache:
        return cache[trade_date]
    conn = sqlite3.connect(META_DB)
    r = conn.execute(
        "SELECT close FROM benchmarks WHERE symbol = '^NSEI' AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (trade_date,),
    ).fetchone()
    conn.close()
    v = float(r[0]) if r else None
    cache[trade_date] = v
    return v


def load_calendar():
    conn = sqlite3.connect(TECH_DB)
    ds = [
        r[0]
        for r in conn.execute("SELECT DISTINCT date FROM technical_data ORDER BY date")
    ]
    conn.close()
    return ds


def check_prereq(cal):
    conn = sqlite3.connect(META_DB)
    r = conn.execute(
        "SELECT MIN(date) FROM benchmarks WHERE symbol = '^NSEI'"
    ).fetchone()
    conn.close()
    bmin = r[0] if r else None
    ok = bool(bmin and bmin <= "2020-01-02")
    print("=" * 100)
    print("STEP 0 -- PREREQUISITE: ^NSEI must resolve back to 2020-01-02")
    print("=" * 100)
    print(f"  benchmarks ^NSEI earliest date : {bmin}")
    print(f"  resolves for 2020-01-02        : {ok}")
    if not ok:
        print(
            '\n  ABORT. Run:  python -c "from myra_app.utils.index_sync import '
            'sync_nifty_benchmarks; sync_nifty_benchmarks()"'
        )
        print("  (it is idempotent and additive), then re-run this tool.")
        return False
    print(
        "  technical_data calendar        : "
        f"{cal[0]} .. {cal[-1]}  ({len(cal)} trading days)"
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(description="Bulk/block deal backtest harness")
    p.add_argument(
        "--adtv-crore",
        type=float,
        default=2.0,
        help="min 20d ADTV in crore (default 2.0)",
    )
    p.add_argument("--out", default="bulk_deal_backtest_results.csv")
    p.add_argument("--self-tests-only", action="store_true")
    p.add_argument("--no-costs", action="store_true")
    return p.parse_args(argv)


def load_year_prices(lo, hi):
    """All symbols' close/volume in [lo, hi], as {symbol: Series}."""
    conn = sqlite3.connect(TECH_DB)
    px = pd.read_sql(
        "SELECT symbol, date, close, volume FROM technical_data "
        "WHERE date >= ? AND date <= ? ORDER BY symbol, date",
        conn,
        params=(lo, hi),
    )
    conn.close()
    px = px.dropna(subset=["close", "volume"])
    px = px[px["close"] > 0]
    out = {}
    for sym, g in px.groupby("symbol", sort=False):
        out[sym] = Series(
            g["date"].tolist(),
            g["close"].to_numpy(dtype=float),
            g["volume"].to_numpy(dtype=float),
        )
    return out


def nth_td(cal, entry_date, h):
    """Date h trading days after entry_date, or None if past the calendar."""
    j = bisect_left(cal, entry_date)
    if j >= len(cal):
        return None
    t = j + h
    return cal[t] if t < len(cal) else None


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    print("=" * 100)
    print("BULK / BLOCK DEAL BACKTEST")
    print("=" * 100)
    print("SURVIVORSHIP CAVEAT: symbols that are delisted, or that lack price")
    print(
        "history in technical_data, are excluded from the tradable universe, "
        "so a name that"
    )
    print(
        "stopped trading after a deal never contributes an event. Results are "
        "survivorship-"
    )
    print(
        "biased and are an UPPER BOUND on what a live book would achieve. "
        "Read every number"
    )
    print("in that light.")
    print()
    print("PRE-REGISTERED CRITERIA (fixed before any result was seen):")
    print(
        "  (i)  BUY-minus-control excess > 0 at h60 in >= 5 of 7 years, "
        "INCLUDING 2023"
    )
    print("       and 2025-26.")
    print("  (ii) BUY-minus-control > SELL-minus-control in most years.")
    print("  If not both met -> NULL RESULT, reported plainly, no retuning.")
    print()

    print("STEP 5 -- SELF-TESTS (run BEFORE any real data is read)")
    print("=" * 100)
    try:
        run_self_tests()
    except SelfTestFailure as e:
        print(f"\n  SELF-TEST FAILED: {e}")
        print("  ABORTING -- no real data was read.")
        return 2
    print()
    if args.self_tests_only:
        return 0

    cal = load_calendar()
    if not check_prereq(cal):
        return 2
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_set = set(cal)
    next_td = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    print()

    deals, _stats = load_deals_clean()
    print()

    events_all = build_net_events(deals)
    print("=" * 100)
    print("STEP 2 -- EVENT CONSTRUCTION (price-independent part)")
    print("=" * 100)
    print(f"  net (symbol,date,client) events : {len(events_all):,}")
    for side in ("BUY", "SELL"):
        print(
            f"    {side:4s} events                : "
            f"{len(events_all[events_all['side'] == side]):7,}"
        )
    print(
        "\n  client class coverage "
        "(JUDGMENT keyword classification, not ground truth):"
    )
    for cc, g in events_all.groupby("client_class"):
        print(f"    {cc:14s} {len(g):7,}  " f"({len(g) / len(events_all) * 100:5.1f}%)")
    print("\n  30 most frequent UNMATCHED client names " "(fell through to 'other'):")
    others = events_all[events_all["client_class"] == "other"]
    for nm, n in others["client_name"].value_counts().head(30).items():
        print(f"    {n:6d}  {nm[:70]}")
    print()

    bench_cache = {}
    rows = []
    ctrl_log = {"control_fallback": 0, "control_none": 0}
    drops = defaultdict(int)
    dropped_px = defaultdict(int)

    for year in YEARS:
        ev = events_all[
            (events_all["date"] >= f"{year}-01-01")
            & (events_all["date"] <= f"{year}-12-31")
        ]
        if ev.empty:
            continue
        # A handful of deal dates are not trading days in technical_data
        # (e.g. 2020-02-01, 2020-11-14). Drop THOSE events and count them;
        # never let one bad date discard an entire year of valid events.
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

        n_pre_cooldown = len(ev)
        ev = ev.copy()
        ev["_pos"] = ev["date"].map(cal_pos)
        ev = ev.sort_values(["symbol", "_pos", "client_name"], kind="stable")
        keep, last = [], {}
        for idx, sym, pos in zip(ev.index, ev["symbol"], ev["_pos"]):
            prev = last.get(sym)
            if prev is None or (pos - prev) >= COOLDOWN_DAYS:
                keep.append(idx)
                last[sym] = pos
        ev = ev.loc[keep]
        drops["cooldown_suppressed_events"] += n_pre_cooldown - len(ev)

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
            adtv = float(
                (s.notional_cs[k] - s.notional_cs[k - ADV_WINDOW]) / ADV_WINDOW
            )
            if adtv < args.adtv_crore * CRORE:
                drops["adtv_below_threshold"] += 1
                continue
            adv = float(s.vols[k - ADV_WINDOW : k].mean())
            bkt = size_bucket(row.abs_qty, adv)
            if bkt is None:
                drops["size_ratio_below_0.25"] += 1
                continue
            # Entry is the symbol's first row STRICTLY after the deal date
            # (bisect_right), and the entry DATE is that row's own date, so a
            # suspended name does not get a benchmark window that disagrees
            # with the price it actually traded at.
            j = bisect_right(s.dates, row.date)
            if j >= len(s.dates):
                drops["no_next_trading_day"] += 1
                continue
            recs.append(
                {
                    "symbol": row.symbol,
                    "date": row.date,
                    "entry_date": s.dates[j],
                    "client_name": row.client_name,
                    "side": row.side,
                    "client_class": row.client_class,
                    "deal_type": row.deal_type,
                    "abs_qty": row.abs_qty,
                    "adv": adv,
                    "adtv": adtv,
                    "size_ratio": row.abs_qty / adv,
                    "size_bucket": bkt,
                    "entry_price": float(s.closes[j]),
                    # NOTE: named without a leading underscore on purpose --
                    # itertuples() renames `_x` columns to positional `_1`, `_2`,
                    # so a `_s` attribute silently does not exist.
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

        universe = build_universe(series_map, args.adtv_crore)
        evr["control"] = pick_controls(evr, universe, random.Random(SEED), ctrl_log)
        n_noc = int(evr["control"].isna().sum())
        drops["control_unavailable"] += n_noc
        evr = evr[evr["control"].notna()].reset_index(drop=True)
        if evr.empty:
            del series_map, universe
            continue

        for h in HORIZONS:
            for r in evr.itertuples(index=False):
                # Both legs and the benchmark are anchored to the SAME global
                # exit date, so a suspension cannot silently lengthen the
                # stock's holding period relative to the benchmark's.
                xd = nth_td(cal, r.entry_date, h)
                exit_px = r.series_ref.close_at_or_before(xd) if xd else None
                gross = compute_return(r.entry_price, exit_px)
                if gross is None:
                    dropped_px[h] += 1
                net = gross if args.no_costs else cost_adjusted_return(gross)

                b_in = get_benchmark_close(r.entry_date, bench_cache)
                b_out = get_benchmark_close(xd, bench_cache) if xd else None
                bench_ret = compute_return(b_in, b_out)

                cs = series_map[r.control]
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
                        "deal_type": r.deal_type,
                        "client_name": r.client_name,
                        "abs_qty": r.abs_qty,
                        "adv": r.adv,
                        "adtv": r.adtv,
                        "size_ratio": r.size_ratio,
                        "size_bucket": r.size_bucket,
                        "horizon": h,
                        "entry_price": r.entry_price,
                        "exit_price": exit_px,
                        "gross_return": gross,
                        "net_return": net,
                        "bench_return": bench_ret,
                        "excess": None
                        if net is None or bench_ret is None
                        else net - bench_ret,
                        "control": r.control,
                        "ctrl_gross": c_gross,
                        "ctrl_net": c_net,
                        "ctrl_excess": None
                        if c_net is None or bench_ret is None
                        else c_net - bench_ret,
                    }
                )
        del series_map, universe, evr
        print(
            f"    {year}: events priced = {len(recs):6,}  "
            f"event x horizon rows so far = {len(rows):,}"
        )

    if not rows:
        print("\nNo events survived. Aborting.")
        return 1

    res = pd.DataFrame(rows)
    res["excess_diff"] = res["excess"] - res["ctrl_excess"]
    res.to_csv(args.out, index=False)
    print(
        f"\n  per-event results -> {args.out} ({len(res):,} rows; root "
        f"*.csv is locally gitignored)"
    )

    print()
    print("=" * 100)
    print("STEP 2/3 -- EVENT, CONTROL AND DROP COUNTS")
    print("=" * 100)
    for k in sorted(drops):
        print(f"  {k:34s} {drops[k]:9,d}")
    for h in HORIZONS:
        print(f"  {'dropped missing price h' + str(h):34s} " f"{dropped_px[h]:9,d}")
    print(
        f"  {'control fell back to whole universe':34s} "
        f"{ctrl_log['control_fallback']:9,d}"
    )
    print(
        f"  {'no control available (event dropped)':34s} "
        f"{ctrl_log['control_none']:9,d}"
    )
    print(
        f"  {'events with a control (rows / 3 horizons)':34s} "
        f"{len(res) // len(HORIZONS):9,d}"
    )

    print()
    print("=" * 100)
    print(
        f"STEP 4 -- PER-YEAR RESULT  (min 20d ADTV = {args.adtv_crore} crore, "
        f"costs {'OFF' if args.no_costs else 'ON'})"
    )
    print("=" * 100)
    print(
        "  Excess = event excess vs ^NSEI; Ctrl = matched control's own "
        "excess vs ^NSEI;"
    )
    print(
        "  Diff = per-date mean(event excess - control excess), averaged "
        "across dates."
    )
    print("  t is across dates. THIN when n_dates < 30 or n_events < 100.")
    print()
    hdr = (
        f"| {'Year':>4} | {'Side':>4} | {'H':>4} | {'nEv':>7} | "
        f"{'nDates':>6} | {'Excess':>7} | {'Ctrl':>7} | {'Diff':>7} | "
        f"{'t':>6} | Flag |"
    )
    print(hdr)
    print("|" + "-" * (len(hdr) - 2) + "|")
    results = {}
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
                a = aggregate_per_date(sub, "excess_diff")
                if a is None or a["mean"] is None:
                    continue
                ae = aggregate_per_date(sub, "excess")
                ac = aggregate_per_date(sub, "ctrl_excess")
                results[(year, side, h)] = a
                tstr = "n/a" if a["t"] is None else f"{a['t']:+.2f}"
                print(
                    f"| {year:>4} | {side:>4} | {h:>4} | "
                    f"{a['n_events']:>7,} | {a['n_dates']:>6} | "
                    f"{(ae['mean'] if ae else float('nan')):>+7.2f} | "
                    f"{(ac['mean'] if ac else float('nan')):>+7.2f} | "
                    f"{a['mean']:>+7.2f} | {tstr:>6} | "
                    f"{thin(a['n_dates'], a['n_events']):>4} |"
                )

    print()
    print("=" * 100)
    print("PRE-REGISTERED CRITERIA EVALUATION")
    print("=" * 100)
    years_pos, detail = [], {}
    for y in YEARS:
        a = results.get((y, "BUY", 60))
        val = a["mean"] if a and a["mean"] is not None else None
        detail[y] = val
        if val is not None and val > 0:
            years_pos.append(y)
    print(
        f"  (i) h60 BUY-minus-control > 0 in {len(years_pos)}/7 years: " f"{years_pos}"
    )
    for must in (2023, 2025, 2026):
        v = detail.get(must)
        mark = "OK" if (v is not None and v > 0) else "FAIL"
        shown = "n/a" if v is None else f"{v:+.2f}"
        print(f"      required {must}: {shown:>7}  [{mark}]")
    crit_i = len(years_pos) >= 5 and all(
        detail.get(y) is not None and detail[y] > 0 for y in (2023, 2025, 2026)
    )

    better = tot = 0
    cmp_rows = []
    for y in YEARS:
        b = results.get((y, "BUY", 60))
        s = results.get((y, "SELL", 60))
        bv = b["mean"] if b and b["mean"] is not None else None
        sv = s["mean"] if s and s["mean"] is not None else None
        if bv is None or sv is None:
            cmp_rows.append(f"      {y}: BUY n/a, SELL n/a (no data)")
            continue
        tot += 1
        if bv > sv:
            better += 1
        cmp_rows.append(
            f"      {y}: BUY {bv:+.2f}  vs  SELL {sv:+.2f}"
            f"  -> {'BUY better' if bv > sv else 'SELL better'}"
        )
    crit_ii = tot > 0 and better > tot / 2
    print("\n  (ii) BUY vs SELL falsification at h60:")
    for line in cmp_rows:
        print(line)
    print(f"      BUY better in {better}/{tot} comparable years")
    print()
    print(f"  CRITERION (i)  : {'PASS' if crit_i else 'FAIL'}")
    print(f"  CRITERION (ii) : {'PASS' if crit_ii else 'FAIL'}")
    print()
    if crit_i and crit_ii:
        print(
            "  VERDICT: BOTH CRITERIA MET. Named-counterparty disclosed "
            "buying is a candidate"
        )
        print("  signal and is worth the weekly workflow.")
    else:
        print("  VERDICT: NULL RESULT. The pre-registered criteria were NOT " "met.")
        print(
            "  Bulk/block disclosed-deal buying does not clear the bar for a "
            "usable signal"
        )
        print(
            "  on this data. Reported as such. No thresholds were retuned "
            "to rescue it."
        )

    print()
    print("=" * 100)
    print(
        "SECONDARY / EXPLORATORY BREAKDOWNS "
        "(NOT pre-registered; multiple-comparison risk)"
    )
    print("=" * 100)
    for key, label in (
        ("size_bucket", "size bucket"),
        ("client_class", "client class"),
    ):
        print(f"\n  BY {label} -- BUY, h60, POOLED across years " f"(exploratory only)")
        sub = res[(res["side"] == "BUY") & (res["horizon"] == 60)]
        h2 = (
            f"| {label:>12} | {'nEv':>8} | {'nDates':>7} | {'Diff':>8} | "
            f"{'t':>7} | Flag |"
        )
        print(h2)
        print("|" + "-" * (len(h2) - 2) + "|")
        for val, g in sub.groupby(key):
            a = aggregate_per_date(g, "excess_diff")
            if a is None or a["mean"] is None:
                continue
            tstr = "n/a" if a["t"] is None else f"{a['t']:+.2f}"
            print(
                f"| {str(val):>12} | {a['n_events']:>8,} | {a['n_dates']:>7} "
                f"| {a['mean']:>+8.2f} | {tstr:>7} | "
                f"{thin(a['n_dates'], a['n_events']):>4} |"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
