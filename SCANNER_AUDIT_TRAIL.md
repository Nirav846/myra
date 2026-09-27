# Scanner Audit Trail

Empirically **rejected or degraded** signal classes, with the evidence that killed
them. Purpose: a scanner idea that resembles something on this list should be
screened against the failure mode here *before* it gets built, not after it gets
shipped and trusted by users.

Each entry records what was tested, the control that mattered, and the result.
Numbers are forward excess return vs `^NSEI` where stated; `n` is the sample size
at that horizon.

---

## REJECTED CLASS: turnover-magnitude-only signals (no price/trend conditioning)

**The pattern.** A signal that fires on *how much* of the float traded, with no
gate on *where price went* — no trend, no 52-week position, no compression
requirement, no confirmation bar. The hypothesis is that turnover magnitude
itself carries information.

**This class has now failed twice, independently.** Two separate scanners, two
separate investigations, two different control designs, the same conclusion.

### 1. Float Turnover — no timing edge over random entry
- Test: first crossing of a float-turnover threshold, forward returns vs
  `NIFTY 500` buy-and-hold **and** vs random entry within the same stock/year.
- Control that mattered: **random entry in the same stock and year.** This
  isolates timing skill from stock selection — a signal can look good purely
  because the names it picks are good.
- Result: did not separate from its random-entry control. Whatever it was
  picking was stock beta, not entry timing.
- Evidence: `tools/backtest_float_turnover.py`

### 2. Float Exhaustion — flat pre-2024, worst signal post-2025
- Test: Float Exhaustion as the *sole* contributing scanner (`selective_scanner_count == 1`),
  so no confluence or agreement effect could contaminate it.
- Two independent regimes, never pooled:

| window | h20 | h40 | h60 | h90 | h120 | h180 | mean |
|---|---|---|---|---|---|---|---|
| **2023** (8 dates) | +0.8% | +0.7% | -1.0% | -0.5% | +0.6% | +0.7% | **+0.2%** |
| **2025-09..2026-09** (8 dates) | -4.2% | -4.9% | -5.2% | -8.1% | -8.8% | -13.2% | **-7.4%** |

- `n` is large in both windows (2023: 1183-1186 at every horizon; 2025+: 1274 at
  h20 falling to 343 at h180), so the post-2025 result is adequately powered.
- Post-2025 it is the **worst of all 11 selective scanners** (next worst:
  Operator Fingerprint -0.9%; best: Climax Accumulation +7.0%), and the
  degradation is **monotone in horizon length** — the signature of sustained
  relative underperformance, not noise.
- In the 2023 window every other scanner was solidly positive (Seasonal Delivery
  +15.3%, Operator Fingerprint +12.9%, Invisible Hand +11.6%); Float was the
  worst there too, just not harmful.
- **Status: KEPT, flagged `degraded — monitor`. NOT removed.**
  Pre-2024 excess was flat, not negative, so the honest read is "harmless in a
  bull market, clearly harmful in a weak one" — not "this used to work and
  stopped". Do not read the 2025+ deterioration as proof of a structural change
  in NSE turnover microstructure either: the two windows are one bull year and
  one weak year, and in the bull year nearly every signal looked good. Two
  windows cannot separate *signal decay* from *regime*.
- Evidence: `tools/backtest_confluence.py --decompose-float --until 2023-12-31`
  and `--since 2025-01-01`; snapshots in `models/calibration/`

### Why this class keeps failing
Turnover magnitude identifies **who is trading**, not **what is happening to
price**. Two names can print identical 20-day float turnover while one is
accumulating into a base and the other is distributing into strength, and a
magnitude-only rule cannot tell them apart. The information that makes delivery
signals work — as with Bottom Hunter — comes from the *differential* between
up-days and down-days, which is a directional structure, not a size.

**Screening rule for future ideas:** if the signal can be stated without reference
to price, reject it up front, or require a price/trend gate before spending a
backtest on it. A turnover component may be *part* of a signal, but it cannot be
the whole signal.

---

## REJECTED: naive multi-scanner confluence agreement as a quality proxy

- Hypothesis (Strategy Idea Bank, Idea 4): stocks flagged by 2+ scanners on the
  same day outperform single-scanner stocks.
- Result: forward excess return was **negative and monotonically worse** as
  agreement rose, across all 6 horizons.
- Worse, the first explanation offered for it — an early/late "crowding" story
  (coiled early-stage pairs good, mixed pairs bad) — **did not survive scrutiny**.
  The entire effect was produced by one scanner's arbitrary stage label; when
  Float Exhaustion was excluded, the relationship **reversed** (pure-early became
  the worst bucket, and pure-early+pure-late the best).
- Two checks that caught it, worth repeating:
  1. The comparator said "MIXED WORST" when mixed had merely beaten *one* of the
     two homogeneous buckets. It compared the best homogeneous against the worst
     mixed instead of requiring mixed to lose to *both*.
  2. A stage-invariant check summed `early+late` and omitted the ambiguous
     bucket, so it failed on every row containing Float Exhaustion — while the
     CSV it was supposed to validate was actually clean. The check was testing
     the wrong thing in the same shape as the comparator.
- Lesson: agreement between *correlated* scanners is not independent confirmation,
  and a stage/label taxonomy is an assumption, not a measurement. Validate the
  taxonomy's stability by re-running the result with each member reclassified
  before building a product feature on it.
- Evidence: `tools/backtest_confluence.py`, commits `1bf6fdd`, `256035c`, `96a0ec6`,
  `8e2cb8e`

---

## Caveats that apply to everything above

- Populations are drawn from current-date scanner unions, so results are
  **survivorship-biased** and are not point-in-time. Compare buckets to each
  other via excess return, never to an absolute zero.
- Absolute (non-excess) returns are **not** comparable across windows: 2023 was a
  strong bull year. Excess return vs `^NSEI` is the only cross-window comparison.
- The `^NSEI` benchmark table originally started 2025-05-29, which made any
  pre-2024 excess return silently `n/a`. It has since been backfilled to
  2021-01-01. Note that `myra_app/utils/index_sync.py:sync_nifty_benchmarks()`
  only pulls `period="1y"`, so **this gap will silently reappear** for anyone
  backtesting older windows until that is widened.
