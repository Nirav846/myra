# MYRA — Build 2 High-Probability Conviction Scanners

## Project Context

Full-stack NSE/BSE analytics platform.
- **Backend:** Python 3.11 · FastAPI · SQLite (WAL) · Pandas / NumPy
- **Strategy files:** `myra_app/strategies/`
- **Frontend views:** `myra_web/src/views/`
- **FastAPI server:** `myra_web/myra_fastapi_server.py`
- **Router + sidebar:** `myra_web/src/App.tsx`

---

## Before Writing Any Code — Read These Files First

```
myra_app/strategies/liquidity_flip_detector.py      ← copy _db_path, _get_universe, _get_tech_data, _sanitize_float verbatim
myra_web/src/views/LiquidityFlipDetector.tsx        ← copy entire component structure, imports, polling, filter, sort, CSV pattern
myra_web/myra_fastapi_server.py                     ← copy state dict + lock + cache + endpoint pattern for both new scanners
```

The Python boilerplate (`_db_path`, `_get_universe`, `_get_tech_data`, `_sanitize_float`,
`_sector_map` block) is **identical** across all scanners. Do not rewrite it. Copy it.
The only part that changes between scanners is the `scan()` method.

---

## technical_data Schema (columns used)

```
date, open, high, low, close, volume,
delivery,       ← shares delivered (integer count, NOT ₹ value)
delivery_pct,   ← delivery / volume * 100 (float)
nifty_outperformance_score, sma_50, high_52w, low_52w
```

`delivery` is a share count. Delivery value in ₹ = delivery × close. Delivery value in ₹Cr = delivery × close / 1e7.

---

## SCANNER 1 — Invisible Hand Scanner

**Philosophy:** Every existing Myra scanner looks at delivery as a magnitude signal on a fixed window.
This scanner looks at delivery *quality* — specifically, what happens on days when price falls
and on days when the stock is completely flat. Those are the days when nobody is paying attention.
High delivery on bad/flat days = a systematic, deliberate buyer with a target price.
That buyer is the Invisible Hand.

This scanner surfaces stocks that look completely dead by every price metric — no trend,
no breakout, no news — but where the delivery math says someone is loading in size.
These are the setups that "pop with no warning" because the accumulation was invisible.

**Files to create:**
- `myra_app/strategies/invisible_hand_scanner.py` → class `InvisibleHandScanner`
- `myra_web/src/views/InvisibleHandScanner.tsx`
- API: `POST /api/invisible-hand/scan` · `GET /api/invisible-hand/status`
- Cache: `models/invisible_hand_cache.json`

---

### Python — `InvisibleHandScanner.scan()` Logic

```python
class InvisibleHandScanner:
    def __init__(self, min_mcap=200, max_mcap=50000,
                 window=20, hist_window=60, min_ih_score=35):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.window = window           # recent window for all current metrics
        self.hist_window = hist_window # historical window for DER baseline
        self.min_ih_score = min_ih_score
```

Fetch `window + hist_window + 10` days of technical data per symbol.

Split into two non-overlapping DataFrames:
- `hist_df` = older sessions, from index `[0 : -window]`, used only for DER baseline
- `curr_df` = last `window` sessions

---

#### Signal 1 — Delivery Efficiency Ratio (DER)

The core novel metric. Measures how much capital was absorbed per unit of price discovery.
A high DER means an enormous amount of stock changed hands while price barely moved —
the signature of a patient institutional buyer not wanting to reveal their hand.

```python
def _compute_der(df: pd.DataFrame) -> float:
    """
    DER = total delivery value (₹Cr) / max(|price drift %|, 0.5)
    Price drift = (last_close - first_close) / first_close * 100
    """
    if len(df) < 2:
        return 0.0
    delivery_vals = df["delivery"].values.astype(float)
    closes = df["close"].values.astype(float)
    delivery_value_cr = float(np.nansum(delivery_vals * closes)) / 1e7
    price_drift_abs = abs(closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0.5
    price_drift_abs = max(price_drift_abs, 0.5)  # floor to avoid division by near-zero
    return delivery_value_cr / price_drift_abs

hist_der = _compute_der(hist_df)
curr_der = _compute_der(curr_df)

# DER ratio: how much more efficient is current accumulation vs its own history?
der_ratio = curr_der / hist_der if hist_der > 0.1 else 1.0
# der_ratio > 1.5 means current window is absorbing 50% more capital per % price move
#              than the stock's own historical average

# Score on fixed scale: 0 at 1×, 100 at 3×
der_score = min(100.0, max(0.0, (der_ratio - 1.0) / 2.0 * 100))
```

Filter: `der_ratio > 1.2` (accumulation efficiency above own historical average)

---

#### Signal 2 — Down-Day Absorption Score (DDAS)

**NOT Nifty RS.** Nifty RS measures relative price performance — lagging, price-based.
DDAS measures what happens to delivery specifically on days when THIS stock's price falls.
On a down day, retail sellers emerge. If delivery_pct is high despite falling price,
an institution was absorbing every share sold. The higher the delivery on down days,
the more deliberate the buyer.

```python
# Down days: sessions where close < prev_close by more than 0.2%
closes = curr_df["close"].values.astype(float)
prev_closes = np.roll(closes, 1)
prev_closes[0] = closes[0]
returns = (closes - prev_closes) / prev_closes * 100

del_pcts = curr_df["delivery_pct"].values.astype(float)

down_mask = returns < -0.2
down_del_pcts = del_pcts[down_mask]

if len(down_del_pcts) >= 4:
    ddas = float(np.nanmean(down_del_pcts))
else:
    # Fewer than 4 down days in the window: the stock barely fell at all.
    # This itself is a signal — treat it conservatively with mean delivery as proxy.
    ddas = float(np.nanmean(del_pcts)) * 0.85

# Score: 100 at DDAS=70%, linear
ddas_score = min(100.0, max(0.0, ddas / 70.0 * 100))
# Also store: how many down-day sessions for context
down_day_count = int(np.sum(down_mask))
```

Filter: `ddas > 42`

---

#### Signal 3 — Delivery Consistency Score (DCS)

Distinguishes systematic accumulation from episodic events (block deals, operator spikes).
A stock with mean 55% delivery but std dev 30% is having episodic delivery (noisy).
A stock with mean 48% delivery and std dev 6% is being systematically accumulated.

```python
mean_del = float(np.nanmean(del_pcts))
std_del  = float(np.nanstd(del_pcts))

# DCS = mean / (1 + std/10). Higher mean and lower std both improve score.
# std/10 normalizes std to a 0-3 scale (typical std is 5-25).
dcs_raw = mean_del / (1.0 + std_del / 10.0)

# Score: 100 at DCS_raw=40, linear
dcs_score = min(100.0, max(0.0, dcs_raw / 40.0 * 100))
```

Filter: `mean_del > 38`

---

#### Signal 4 — Quiet Conviction Days (QCD)

Sessions within the window where all three are true simultaneously:
  a) delivery_pct > 50%          ← meaningful delivery
  b) |daily return| < 1.5%       ← price barely moved
  c) volume between 0.6× and 1.4× the window's own average volume

These are the "nobody-is-watching" days. Price is flat, volume is average.
And yet, significant delivery is happening. This is the purest accumulation signal.

```python
vols = curr_df["volume"].values.astype(float)
avg_vol = float(np.nanmean(vols))

qcd = 0
for i in range(1, len(curr_df)):
    dp   = del_pcts[i]
    ret  = abs(returns[i])
    vol  = vols[i]
    if dp > 50 and ret < 1.5 and 0.6 * avg_vol <= vol <= 1.4 * avg_vol:
        qcd += 1

# Score: 100 at QCD=12 out of 20 possible sessions
qcd_score = min(100.0, max(0.0, qcd / 12.0 * 100))
```

---

#### Composite IH Score

```python
ih_score = (
    der_score  * 0.35
    + ddas_score * 0.30
    + dcs_score  * 0.20
    + qcd_score  * 0.15
)
# ih_score is 0–100

if ih_score >= 75: grade = "A"
elif ih_score >= 55: grade = "B"
elif ih_score >= 35: grade = "C"
else: grade = "D"
```

---

#### Additional Computed Fields

```python
# 52-week position
high_52w = float(curr_df["high_52w"].iloc[-1]) if pd.notna(curr_df["high_52w"].iloc[-1]) \
           else float(curr_df["high"].max())
low_52w  = float(curr_df["low_52w"].iloc[-1]) if pd.notna(curr_df["low_52w"].iloc[-1]) \
           else float(curr_df["low"].min())
wk52_pos = (latest_close - low_52w) / (high_52w - low_52w) * 100 \
           if (high_52w - low_52w) > 0 else 50.0

# Base duration: consecutive sessions going back where daily range < 3% of close
base_duration = 0
for i in range(len(curr_df) - 1, -1, -1):
    row = curr_df.iloc[i]
    daily_range_pct = (float(row["high"]) - float(row["low"])) / float(row["close"]) * 100 \
                      if float(row["close"]) > 0 else 99
    if daily_range_pct < 3.0:
        base_duration += 1
    else:
        break
```

---

#### Filters Before Appending

```python
if der_ratio <= 1.2:     continue   # DER must be above own history
if ddas <= 42:           continue   # Meaningful buying on down days
if mean_del <= 38:       continue   # Minimum delivery activity
if ih_score < self.min_ih_score: continue  # Minimum composite score
if wk52_pos >= 88:       continue   # Not already near 52-week high
```

---

#### Output Columns (exact dict keys → TypeScript interface field names)

```python
candidates.append({
    "symbol":          symbol,
    "sector":          _sector_map.get(symbol, "Unknown"),
    "market_cap_cr":   round(mcap / 1e7, 1),
    "der_ratio":       round(der_ratio, 2),
    "der_score":       round(der_score, 1),
    "ddas":            round(ddas, 1),           # mean delivery% on down days
    "ddas_score":      round(ddas_score, 1),
    "mean_del_pct":    round(mean_del, 1),
    "dcs_score":       round(dcs_score, 1),
    "qcd":             qcd,                       # count of quiet conviction days
    "qcd_score":       round(qcd_score, 1),
    "ih_score":        round(ih_score, 1),
    "grade":           grade,
    "down_day_count":  down_day_count,
    "base_duration":   base_duration,
    "close":           round(latest_close, 2),
    "wk52_pos":        round(wk52_pos, 1),
})
```

Float fields to sanitize: `der_ratio, der_score, ddas, ddas_score, mean_del_pct,
dcs_score, qcd_score, ih_score, close, wk52_pos, market_cap_cr`

Sort final list by `ih_score DESC`.

---

### FastAPI — Invisible Hand Endpoints

Module-level in `myra_fastapi_server.py` (copy exact pattern from Liquidity Flip):

```python
_ih_scan_state: dict = {
    "scan_status": "idle", "last_scan": None,
    "progress": 0, "message": "Idle — click Scan to start",
    "candidates": [],
}
_ih_scan_lock = threading.Lock()
_IH_SCAN_CACHE = "models/invisible_hand_cache.json"

@app.get("/api/invisible-hand/status")
async def invisible_hand_status(): ...

@app.post("/api/invisible-hand/scan")
async def invisible_hand_scan(payload: dict = Body(default={})): ...
```

Background thread must update `progress` per-symbol (same pattern as other scanners).

---

### React — `InvisibleHandScanner.tsx`

**Color theme:** `violet/purple` — use `text-violet-400`, `bg-violet-500/20`, `border-violet-500/30`
**Icon:** `import { Eye } from 'lucide-react'`  (the hidden/invisible theme)
**API prefix:** `/api/invisible-hand`

Copy the **entire component structure** from `LiquidityFlipDetector.tsx`:
- Same imports (ScrollableTable, StarButton, MarketCapRangeFilter, useWatchlist, API_BASE, Tooltip)
- Same `relativeTime()` helper
- Same stale banner, scan button, progress bar, status banner
- Same `fetchScanStatus` / `startScan` / `useEffect` mount / `setInterval` 2000ms polling pattern
- Same CSV export handler

**TypeScript interface:**
```typescript
interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  der_ratio: number;
  der_score: number;
  ddas: number;
  ddas_score: number;
  mean_del_pct: number;
  dcs_score: number;
  qcd: number;
  qcd_score: number;
  ih_score: number;
  grade: string;
  down_day_count: number;
  base_duration: number;
  close: number;
  wk52_pos: number;
}
```

**Stats summary cards (4 cards):**
- Candidates: `filteredData.length`
- Grade A: `filteredData.filter(d => d.grade === 'A').length` in violet
- Avg DER Ratio: mean of `der_ratio` in cyan
- Avg DDAS: mean of `ddas` + "%" in amber

**Grade A highlight panel:** same green panel as LiquidityFlipDetector, show symbol + sector + DER ratio + DDAS

**Table columns:** Symbol · Sector · MCap · DER Ratio · DDAS% · Mean Del% · DCS · QCD · IH Score · Base Days · Close · 52W Pos

**Column color logic:**
- DER Ratio: violet > 2.0, cyan 1.5–2.0, grey otherwise
- DDAS%: green > 60, yellow > 48, grey otherwise
- Mean Del%: green > 55, yellow > 45, grey otherwise
- DCS: green > 70, yellow > 50, grey otherwise
- QCD: green ≥ 8, yellow ≥ 5, grey otherwise
- IH Score: use Grade A/B/C/D badge same as other scanners
- 52W Pos%: green < 75, yellow < 88, red ≥ 88

**Tooltips (add to column headers using `<Tooltip content="...">`):**
- DER Ratio: "Delivery Efficiency Ratio — ₹Cr absorbed ÷ price drift%. Higher = more stock absorbed with less price movement. Your own history is the baseline."
- DDAS%: "Down-Day Absorption Score — mean delivery% on sessions where THIS stock fell. High score = someone was buying every dip."
- DCS: "Delivery Consistency Score — systematic vs episodic. High = accumulation is regular, not just block-deal spikes."
- QCD: "Quiet Conviction Days — sessions with >50% delivery, flat price, avg volume. The purest accumulation signal."

**Filters:**
- MarketCapRangeFilter (same as all scanners)
- Watchlist-only star button (same)
- Sector dropdown (same)
- Min IH Score slider: 0–80 step 5 (default 35), value shown in violet
- Min QCD buttons: Any / 4+ / 6+ / 8+
- Grade multi-select: A / B / C / D buttons (same style as Del50Days buttons in LiquidityFlipDetector)

**Default sort:** `ih_score` descending.

---

## SCANNER 2 — The Trigger

**Philosophy:** The Invisible Hand finds stocks in early accumulation (weeks 1-4).
The Trigger finds stocks at the breakout moment — days away from moving, not weeks.

The Trigger uses a **three-gate system**. All three gates must pass. No exceptions, no weighting.
Each gate measures a completely independent dimension of supply depletion:

- **Gate 1 (Supply Physics):** Float has been meaningfully absorbed — the float math says supply is shrinking
- **Gate 2 (Seller Behaviour):** On the bad days, fewer and fewer shares are being delivered by sellers — they are giving up
- **Gate 3 (Mechanical Pinch):** Volume has dried up and price is compressed — the coil is loaded

When supply is physically gone (Gate 1), remaining sellers are behaviorally exhausted (Gate 2),
and the market mechanics show the coil is wound tight (Gate 3) — that is the moment.

The Trigger will output 3–12 stocks at any time. These are not suggestions. They are setups.

**Files to create:**
- `myra_app/strategies/trigger_scanner.py` → class `TriggerScanner`
- `myra_web/src/views/TriggerScanner.tsx`
- API: `POST /api/trigger/scan` · `GET /api/trigger/status`
- Cache: `models/trigger_cache.json`

---

### Python — `TriggerScanner.scan()` Logic

```python
class TriggerScanner:
    def __init__(self, min_mcap=300, max_mcap=50000,
                 min_float_util_pct=12.0,
                 vol_pinch_ratio=0.72,
                 price_range_max_pct=2.8):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_float_util_pct = min_float_util_pct
        self.vol_pinch_ratio = vol_pinch_ratio     # 5d vol < X × 20d vol
        self.price_range_max_pct = price_range_max_pct  # 5d H-L range < X% of close
```

Fetch `promoter_holding_pct` and `free_float_pct` from fundamentals (same as FloatExhaustionScanner).
Fetch last 45 days of technical data per symbol.

---

#### Gate 1 — Float Absorption (Supply Physics)

Re-implement the FloatExhaustionScanner float math exactly.
Do NOT import FloatExhaustionScanner — recompute inline.

```python
# Shares calculation
latest_close = float(df["close"].iloc[-1])
if latest_close <= 0: continue

shares_total_approx = mcap / latest_close
available_float_pct = ff_pct if ff_pct > 0 else max(5.0, 100 - promoter_pct - 15)
free_float_shares   = shares_total_approx * available_float_pct / 100
if free_float_shares <= 0: continue

# Last 20 sessions
w20 = df.tail(20)
cum_delivery_20d   = float(np.nansum(w20["delivery"].values.astype(float)))
float_util_pct     = cum_delivery_20d / free_float_shares * 100

# Gate 1 pass condition
gate1_pass = float_util_pct >= self.min_float_util_pct

# Gate 1 strength score (0–100)
gate1_score = min(100.0, float_util_pct / 0.40 * 100)
# (40% float consumed = score 100)
```

---

#### Gate 2 — Seller Extinction (Behavioural)

Sellers who are still in a position but not selling are NOT the same as sellers who have sold.
Seller extinction = the sellers who remain are no longer willing to sell at current prices.
We detect this by watching delivery_pct on down-day sessions over the last 15 sessions:
if delivery on those sessions is DECLINING over time, sellers are giving up.

```python
w15 = df.tail(15).reset_index(drop=True)
closes_15   = w15["close"].values.astype(float)
del_pcts_15 = w15["delivery_pct"].values.astype(float)

# Identify down-day sessions within last 15
prev_closes = np.roll(closes_15, 1)
prev_closes[0] = closes_15[0]
session_returns = (closes_15 - prev_closes) / prev_closes * 100

down_idx   = np.where(session_returns < -0.15)[0]   # sessions where stock fell > 0.15%
down_del   = del_pcts_15[down_idx]

if len(down_idx) < 3:
    # Fewer than 3 down sessions in last 15 = stock barely fell at all.
    # This is itself a strong signal (someone defending). Auto-pass Gate 2.
    gate2_pass = True
    gate2_score = 70.0
    seller_slope = 0.0
    avg_down_del = float(np.nanmean(del_pcts_15)) * 0.85   # approx
else:
    # Compute slope of down-day delivery over time
    x = np.arange(len(down_del), dtype=float)
    if len(down_del) >= 2:
        seller_slope = float(np.polyfit(x, down_del, 1)[0])
    else:
        seller_slope = 0.0
    avg_down_del = float(np.nanmean(down_del))

    # Gate 2 conditions (either/or — both indicate seller exhaustion):
    # A) Sellers delivering progressively FEWER shares each bad day (slope negative)
    # B) Sellers never showed up in force on any down day (avg delivery < 38%)
    cond_a = seller_slope < -0.20    # delivery shrinking by 0.2pp per down session
    cond_b = avg_down_del < 38.0     # sellers never pushed in force
    gate2_pass = cond_a or cond_b

    # Gate 2 strength: stronger for more negative slope + lower avg down-day delivery
    slope_score  = min(50.0, max(0.0, -seller_slope / 0.25 * 50)) if cond_a else 0.0
    avg_score    = min(50.0, max(0.0, (45 - avg_down_del) / 45 * 50)) if cond_b else 0.0
    gate2_score  = min(100.0, slope_score + avg_score)
```

---

#### Gate 3 — Volume Pinch (Mechanical)

Volume drying up + price compressing = coil is loaded.
When sellers are gone, volume naturally falls. Price compresses because there's no disagreement.
This is the mechanical signature of a stock sitting on a compressed spring.

```python
w20_vols  = df["volume"].values.astype(float)[-20:]
w5_vols   = w20_vols[-5:]
w20_highs = df["high"].values.astype(float)[-20:]
w5_highs  = w20_highs[-5:]
w5_lows   = df["low"].values.astype(float)[-5:]

vol_ratio_5_20 = float(np.nanmean(w5_vols)) / float(np.nanmean(w20_vols)) \
                 if np.nanmean(w20_vols) > 0 else 1.0

price_range_5d_pct = (float(np.nanmax(w5_highs)) - float(np.nanmin(w5_lows))) \
                     / latest_close * 100 if latest_close > 0 else 99.0

gate3_pass = vol_ratio_5_20 < self.vol_pinch_ratio and \
             price_range_5d_pct < self.price_range_max_pct

# Gate 3 strength: lower vol ratio + tighter price range = stronger pinch
vol_score   = min(50.0, max(0.0, (self.vol_pinch_ratio - vol_ratio_5_20) / self.vol_pinch_ratio * 50 / 0.5))
range_score = min(50.0, max(0.0, (self.price_range_max_pct - price_range_5d_pct) / self.price_range_max_pct * 50 / 0.5))
gate3_score = min(100.0, vol_score + range_score)
```

---

#### Bonus Signals (affect score, not gate pass/fail)

```python
# Defense Bars: sessions in last 20 where stock held despite being 
# expected to fall (opened lower but closed near flat, with high delivery)
w20_df   = df.tail(20)
opens20  = w20_df["open"].values.astype(float)
closes20 = w20_df["close"].values.astype(float)
highs20  = w20_df["high"].values.astype(float)
dels20   = w20_df["delivery_pct"].values.astype(float)

defense_bars = 0
for i in range(1, len(w20_df)):
    gap_down = (opens20[i] - closes20[i - 1]) / closes20[i - 1] * 100  # gap at open vs prev close
    recovery = (closes20[i] - opens20[i]) / (highs20[i] - opens20[i] + 0.01) * 100
    if gap_down < -0.3 and recovery > 50 and dels20[i] > 50:
        # Opened lower, recovered majority of intraday range, high delivery = defended
        defense_bars += 1

# Base Duration: consecutive recent sessions where daily range < 3% of close
base_duration = 0
all_highs = df["high"].values.astype(float)
all_lows  = df["low"].values.astype(float)
all_closes = df["close"].values.astype(float)
for i in range(len(df) - 1, -1, -1):
    rng_pct = (all_highs[i] - all_lows[i]) / all_closes[i] * 100 \
              if all_closes[i] > 0 else 99.0
    if rng_pct < 3.5:
        base_duration += 1
    else:
        break

# Breakout proximity: how close to top of the recent 20-session base?
base_high_20 = float(np.nanmax(w20_highs))
base_low_20  = float(np.nanmin(df["low"].values.astype(float)[-20:]))
breakout_prox = (latest_close - base_low_20) / (base_high_20 - base_low_20) \
                if (base_high_20 - base_low_20) > 0 else 0.5
# 1.0 = at base high (imminent breakout), 0.0 = at base low
```

---

#### Trigger Score (only computed if all 3 gates pass)

```python
if not (gate1_pass and gate2_pass and gate3_pass):
    continue

trigger_score = (
    gate1_score * 0.30
    + gate2_score * 0.25
    + gate3_score * 0.25
    + defense_bars * 4.0          # bonus: max ~20 pts for 5 bars
    + breakout_prox * 10.0        # bonus: up to 10 pts
    + min(base_duration, 10) * 0.5  # bonus: up to 5 pts for long base
)
trigger_score = min(100.0, trigger_score)

if trigger_score >= 75: grade = "A"
elif trigger_score >= 55: grade = "B"
elif trigger_score >= 35: grade = "C"
else: grade = "D"
```

---

#### Trigger Output Columns

```python
candidates.append({
    "symbol":            symbol,
    "sector":            _sector_map.get(symbol, "Unknown"),
    "market_cap_cr":     round(mcap / 1e7, 1),
    # Gate scores
    "float_util_pct":    round(float_util_pct, 1),
    "gate1_score":       round(gate1_score, 1),
    "avg_down_del":      round(avg_down_del, 1),
    "seller_slope":      round(seller_slope, 3),
    "gate2_score":       round(gate2_score, 1),
    "vol_ratio_5_20":    round(vol_ratio_5_20, 3),
    "price_range_5d_pct": round(price_range_5d_pct, 2),
    "gate3_score":       round(gate3_score, 1),
    # Bonus
    "defense_bars":      defense_bars,
    "base_duration":     base_duration,
    "breakout_prox":     round(breakout_prox, 3),
    # Composite
    "trigger_score":     round(trigger_score, 1),
    "grade":             grade,
    # Price
    "close":             round(latest_close, 2),
    "wk52_pos":          round(wk52_pos, 1),
})
```

Float fields to sanitize: all numeric fields except `defense_bars`, `base_duration`.

Sort by `trigger_score DESC`.

---

### FastAPI — Trigger Endpoints

```python
_trigger_scan_state: dict = {
    "scan_status": "idle", "last_scan": None,
    "progress": 0, "message": "Idle — click Scan to start",
    "candidates": [],
}
_trigger_scan_lock = threading.Lock()
_TRIGGER_SCAN_CACHE = "models/trigger_cache.json"

@app.get("/api/trigger/status")
async def trigger_status(): ...

@app.post("/api/trigger/scan")
async def trigger_scan(payload: dict = Body(default={})): ...
```

---

### React — `TriggerScanner.tsx`

**Color theme:** `amber/orange` — use `text-amber-400`, `bg-amber-500/20`, `border-amber-500/30`
**Icon:** `import { Zap } from 'lucide-react'`
**API prefix:** `/api/trigger`

Copy the full component structure from `LiquidityFlipDetector.tsx` exactly.
Change theme colors and icon only.

**TypeScript interface:**
```typescript
interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  float_util_pct: number;
  gate1_score: number;
  avg_down_del: number;
  seller_slope: number;
  gate2_score: number;
  vol_ratio_5_20: number;
  price_range_5d_pct: number;
  gate3_score: number;
  defense_bars: number;
  base_duration: number;
  breakout_prox: number;
  trigger_score: number;
  grade: string;
  close: number;
  wk52_pos: number;
}
```

**Stats summary cards:**
- Triggers Ready: `filteredData.length` (total passing all 3 gates)
- Grade A: count in amber
- Avg Float Util%: mean `float_util_pct` in red/orange
- Avg Base Days: mean `base_duration` in cyan

**CRITICAL UI ELEMENT — Gate Status Display:**
Each row in the table must show three gate indicators. Display them as three small colored
pill badges in a "Gates" column, before the Score column:

```typescript
// In each table row, add a "Gates" cell:
<td className="px-3 py-3">
  <div className="flex gap-1">
    {/* Gate 1: Float */}
    <Tooltip content={`Float Util: ${row.float_util_pct.toFixed(1)}% of free float absorbed`}>
      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-orange-500/20 text-orange-400 border border-orange-500/30">
        G1
      </span>
    </Tooltip>
    {/* Gate 2: Seller */}
    <Tooltip content={`Seller slope: ${row.seller_slope.toFixed(2)}pp/session · Avg down-day del: ${row.avg_down_del.toFixed(1)}%`}>
      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-500/20 text-red-400 border border-red-500/30">
        G2
      </span>
    </Tooltip>
    {/* Gate 3: Pinch */}
    <Tooltip content={`Vol ratio: ${row.vol_ratio_5_20.toFixed(2)} · Price range: ${row.price_range_5d_pct.toFixed(2)}%`}>
      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-400 border border-blue-500/30">
        G3
      </span>
    </Tooltip>
  </div>
</td>
```

All rows in results have passed all 3 gates, so all G1/G2/G3 badges are always shown as passed.
The Tooltip provides the underlying metric values for each gate.

**Table columns:**
Symbol · Sector · MCap · Float Util% · Avg Down Del% · Seller Slope · Vol Ratio · Range 5d% · Gates · Defense · Base Days · Prox% · Score · Close

**Column color logic:**
- Float Util%: red ≥ 25, orange ≥ 15, yellow ≥ 12
- Avg Down Del%: green < 35, yellow < 45, grey otherwise (lower = fewer sellers)
- Seller Slope: green < -0.3, cyan < -0.1, grey (more negative = sellers giving up faster)
- Vol Ratio: green < 0.55, yellow < 0.72 (lower = more pinched)
- Range 5d%: green < 1.5, yellow < 2.5 (tighter = more coiled)
- Defense Bars: green ≥ 3, yellow ≥ 1, grey 0
- Prox%: show as integer percent. green > 70 (near breakout), yellow 50–70, grey otherwise
- Score: Grade A/B/C/D badge

**Grade A highlight panel:**
Show compact cards for Grade A triggers with: symbol, sector, float util%, base duration, breakout prox — use amber glow border.

**Tooltips on headers:**
- Float Util%: "% of estimated free float absorbed by cumulative 20-day delivery. >12% = meaningful supply compression."
- Avg Down Del%: "Mean delivery% on sessions when THIS stock fell. Low = sellers barely showed up even on bad days."
- Seller Slope: "Rate of change of down-day delivery over last 15 sessions. Negative = sellers delivering fewer shares each time — they're giving up."
- Vol Ratio: "5-day volume vs 20-day average. <0.72 = volume has meaningfully dried up — very little supply remains active."
- Prox%: "How close current price is to the top of the 20-session base. >70% = approaching breakout point."

**Filters:**
- MarketCapRangeFilter (same)
- Watchlist-only (same)
- Sector dropdown (same)
- Min Trigger Score slider: 0–80 step 5 (default 30), value in amber
- Min Defense Bars buttons: Any / 1+ / 2+ / 3+
- Min Base Duration buttons: Any / 5+ / 10+ / 15+

---

## App.tsx Integration

```tsx
// Add lazy imports
const InvisibleHandScanner = lazy(() => import('./views/InvisibleHandScanner'));
const TriggerScanner       = lazy(() => import('./views/TriggerScanner'));

// Add routes (copy exact pattern from existing scanner routes)
{ path: '/invisible-hand', element: <InvisibleHandScanner lib={lib} /> }
{ path: '/trigger',        element: <TriggerScanner lib={lib} /> }

// Sidebar nav entries (copy style from existing nav items)
{ path: '/invisible-hand', label: 'Invisible Hand',  icon: <Eye size={16} /> }
{ path: '/trigger',        label: 'The Trigger',     icon: <Zap size={16} /> }
```

---

## Code Quality Rules (same as always)

- Copy `_db_path`, `_get_universe`, `_get_tech_data`, `_sanitize_float`,
  `_sector_map` block verbatim from `liquidity_flip_detector.py` — do not rewrite
- Never f-string SQL — always parameterized `conn.execute(sql, (param,))`
- Wrap all db connections: `with sqlite3.connect(...) as conn:`
- `try/except sqlite3.OperationalError` for `sma_50 / high_52w / low_52w` columns
- All float fields sanitized before appending to candidates list
- TypeScript field names must exactly match Python dict keys
- Use `?.toFixed(2) ?? '—'` pattern for nullable numeric cells
- `text-[#fafafa]` primary text · `text-[#888]` secondary · Tailwind only, no inline styles

---

## Important Implementation Notes

### Why down-day delivery beats Nifty RS
`nifty_outperformance_score` measures relative PRICE performance — it tells you what already
happened. DDAS and avg_down_del measure what institutional buyers DID on the sessions when
this specific stock fell. This answers the real question: "When sellers emerged in this stock,
was someone absorbing every share?" Price-based RS cannot answer that.

### The Trigger is a gate system, not a scoring system
The three gates must ALL pass before a stock enters the results. Do not soften this to
"2 out of 3" or "weighted average." The value of the scanner is its precision. A stock
where the float is consumed but sellers are still showing up heavily is NOT ready.
A stock with a volume pinch but no float absorption is NOT ready. All three together = ready.

### DER ratio uses the symbol's OWN history as baseline
Do not compare DER across the universe (market-cap-contaminated). Compare each symbol
against its own hist_window (60-day) DER. A ₹500Cr-turnover large-cap and a ₹5Cr-turnover
small-cap both get evaluated against their own typical absorption efficiency.

### Expected output size
- Invisible Hand: 15–35 stocks on a typical trading day
- The Trigger: 3–12 stocks on a typical trading day
If outputs are consistently larger, tighten filter thresholds by 10%. If consistently 0–2,
relax `vol_pinch_ratio` to 0.78 and `min_float_util_pct` to 10.0.

---

## Build Order

1. `invisible_hand_scanner.py` (Strategy) → test `scanner.scan()` returns non-empty DataFrame
2. FastAPI endpoints for Invisible Hand → test `/api/invisible-hand/status` returns JSON
3. `InvisibleHandScanner.tsx` → wire to API, verify scan + polling + table
4. `App.tsx` Invisible Hand route + sidebar
5. `trigger_scanner.py` (Strategy) → test gate logic with print debugging
6. FastAPI endpoints for Trigger
7. `TriggerScanner.tsx`
8. `App.tsx` Trigger route + sidebar
