# MYRA — Build 5 New Stock Scanners (Full Stack)

## Project Context

This is MYRA, a full-stack NSE/BSE equity analytics platform.

- **Backend:** Python 3.11 · FastAPI · SQLite (WAL mode) · Pandas/NumPy
- **Frontend:** React 18 · TypeScript · Tailwind CSS · Lucide icons
- **Backend entry:** `myra_web/myra_fastapi_server.py`
- **Strategy files:** `myra_app/strategies/`
- **Frontend views:** `myra_web/src/views/`
- **Router:** `myra_web/src/App.tsx`
- **API base config:** `myra_web/src/config.ts` exports `API_BASE`

### DB Access Pattern

Always use `LibrarianCore.DB_MAP` — never hardcode filenames.

```python
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

def _db_path(key: str) -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

# Keys: "technical" → technical_data table (OHLCV + delivery)
#       "valuation" → fundamentals table (mcap, promoter_holding_pct, free_float_pct)
#       "meta"      → symbols_master table (sector, industry, index membership)
```

### technical_data Schema (key columns)

```
symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
volume INTEGER, delivery INTEGER, delivery_pct REAL,
nifty_outperformance_score REAL, sma_50 REAL, high_52w REAL, low_52w REAL
```

### fundamentals Schema (key columns)

```
symbol TEXT, date TEXT, market_cap REAL, marketCap REAL,
promoter_holding_pct REAL, free_float_pct REAL, sector TEXT
```

### FastAPI Background Scan Pattern (copy exactly for every scanner)

```python
# State dict (module-level)
_xyz_scan_state: dict = {
    "scan_status": "idle", "last_scan": None,
    "progress": 0, "message": "Idle — click Scan to start",
    "candidates": [], "bear_market": False,
}
_xyz_scan_lock = threading.Lock()
_XYZ_SCAN_CACHE = "models/xyz_scan_cache.json"

def _save_xyz_cache(): ...   # json.dump state to file
def _load_xyz_cache(): ...   # json.load from file

@app.get("/api/xyz/status")
async def xyz_status():
    # If idle, load from disk cache so results survive server restart
    ...

@app.post("/api/xyz/scan")
async def xyz_scan(payload: dict = Body(default={})):
    # Start background thread, return {"status": "started"} immediately
    ...
```

### React Frontend Pattern

Study `myra_web/src/views/DarvasBoxProScanner.tsx` for the exact component structure:

- `useState` for `scanStatus`, `isScanning`, `error`
- `useCallback` for `fetchScanStatus` + `startScan`
- `useEffect` for mount fetch + cleanup
- `setInterval` polling every 2000ms while scanning
- `useMemo` for `filteredData`
- Progress bar during scan, stats summary, ScrollableTable for results
- CSV export button
- Import `API_BASE` from `'../config'`
- Import `ScrollableTable` from `'../components/ScrollableTable'`
- Import `StarButton` from `'../components/StarButton'`
- Import `useWatchlist` from `'../lib/WatchlistContext'`
- Import `MarketCapRangeFilter` from `'../components/MarketCapRangeFilter'`

### App.tsx Routing Pattern

```tsx
// Lazy import at top
const XyzScanner = lazy(() => import('./views/XyzScanner'));

// Route entry in the router switch
{ path: '/xyz-scanner', element: <XyzScanner lib={lib} /> }

// Sidebar nav entry
{ path: '/xyz-scanner', label: 'Xyz Scanner', icon: <SomeIcon size={16}/> }
```

---

## TASK: Build all 5 scanners below, in this exact order.

For each scanner you must create:
1. `myra_app/strategies/<name>.py` — Python strategy class
2. Wire a `/api/<name>/scan` (POST) + `/api/<name>/status` (GET) endpoint in `myra_web/myra_fastapi_server.py`
3. `myra_web/src/views/<Name>.tsx` — React view following the DarvasBoxProScanner pattern
4. Add the route + sidebar entry to `myra_web/src/App.tsx`

---

## Scanner 1 — Liquidity Flip Detector

**File names:**
- `myra_app/strategies/liquidity_flip_detector.py` → class `LiquidityFlipDetector`
- `myra_web/src/views/LiquidityFlipDetector.tsx`
- API routes: `POST /api/liquidity-flip/scan` · `GET /api/liquidity-flip/status`
- Cache file: `models/liquidity_flip_cache.json`

**Concept:**
Find stocks transitioning from high-volume/low-delivery (churning) to high-volume/high-delivery (genuine conviction). This is the moment "dumb money noise" becomes "smart money accumulation." We call this the Liquidity Flip.

**Python Strategy Logic:**

```python
class LiquidityFlipDetector:
    def __init__(self, min_mcap=200, max_mcap=50000):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap

    def scan(self) -> pd.DataFrame:
        # 1. Get universe from fundamentals (same as AccumulationBaseScanner._get_universe)
        # 2. For each symbol, query technical_data for last 95 days
        # 3. Compute:

        # CHURN BASELINE (days -95 to -21):
        #   avg_vol_prior = mean(volume)
        #   avg_del_prior = mean(delivery_pct)
        #   prior_vol_rank = avg_vol_prior / universe_median_vol  (> 1.5 = high volume churner)

        # RECENT WINDOW (last 5 trading sessions):
        #   recent_del_5d = mean(delivery_pct[-5:])
        #   del50_days = count of days in last 5 where delivery_pct > 50
        #   recent_vol = mean(volume[-5:])

        # FLIP DETECTION:
        #   del_jump_pp = recent_del_5d - avg_del_prior
        #   is_flip = (avg_del_prior < 40) AND (recent_del_5d > 52) AND (del50_days >= 3)
        #   flip_type = 'STRONG FLIP' if avg_del_prior < 35 and recent_del_5d > 55
        #               'MODERATE FLIP' if avg_del_prior < 45 and recent_del_5d > 60
        #               else skip

        # PRICE CHECK (not already broken out):
        #   wk52_pos = (close - low_52w) / (high_52w - low_52w) * 100
        #   Filter: wk52_pos < 90 (hasn't already run)

        # OUTPUT COLUMNS:
        # symbol, sector, market_cap_cr, prior_del_pct, current_del_pct,
        # del_jump_pp, del50_days, flip_type, prior_vol_rank,
        # close, wk52_pos, flip_score
        #
        # flip_score = del_jump_pp * 2 + del50_days * 5 + (40 - avg_del_prior) * 0.5
        # Sort by flip_score DESC
```

**React UI Columns:** Symbol · Sector · MCap · Prior Del% · Current Del% · Jump(pp) · Del50 Days · Flip Type · Vol Rank · Close · 52W Pos · Score

**UI Color Logic:**
- Jump(pp): green > 20, yellow > 10, grey otherwise
- Del50 Days: green = 5, yellow = 4, amber = 3
- Flip Type: green badge = "STRONG FLIP", cyan badge = "MODERATE FLIP"
- Score: same Grade A/B/C/D badges as other scanners (≥70 = A, ≥50 = B, ≥30 = C, else D)

**Filters:** Min Jump(pp) slider (0–30), Min Del50 Days (3/4/5 buttons), Sector dropdown, MCap range, Watchlist-only toggle

---

## Scanner 2 — Operator Fingerprint Scanner

**File names:**
- `myra_app/strategies/operator_fingerprint_scanner.py` → class `OperatorFingerprintScanner`
- `myra_web/src/views/OperatorFingerprintScanner.tsx`
- API routes: `POST /api/operator-fingerprint/scan` · `GET /api/operator-fingerprint/status`
- Cache file: `models/operator_fingerprint_cache.json`

**Concept:**
Detect the Indian "operator accumulation fingerprint": price range compressing (ATR falling) while delivery% slowly drifts upward over 15–30 sessions, with volume building in quiet steps. Outputs a single "Coil Tension Score" (0–100). High score = spring ready to release.

**Python Strategy Logic:**

```python
class OperatorFingerprintScanner:
    def __init__(self, min_mcap=200, max_mcap=50000, lookback_days=45):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.lookback_days = lookback_days

    def scan(self) -> pd.DataFrame:
        # For each symbol, fetch last (lookback_days + 30) sessions

        # ATR COMPRESSION:
        #   atr_old = mean daily range % over sessions [-45:-31]  (older window)
        #   atr_new = mean daily range % over sessions [-14:]     (recent window)
        #   compression_ratio = atr_new / atr_old  (< 0.75 = compressed)

        # DELIVERY DRIFT (linear slope of delivery_pct over last 20 sessions):
        #   Use np.polyfit(x, delivery_pct[-20:], 1)[0]
        #   delivery_drift = slope  (positive = drifting up)

        # QUIET ACCUMULATION DAYS (last 20 sessions):
        #   A "quiet accumulation day" = delivery_pct > session_avg AND
        #   abs(close - prev_close) / prev_close < 0.015
        #   quiet_accum_days = count of such days

        # VOLUME STAIRCASE (3 blocks of 5 sessions each, newest to oldest):
        #   vol_block_1 = mean(volume[-5:])
        #   vol_block_2 = mean(volume[-10:-5])
        #   vol_block_3 = mean(volume[-15:-10])
        #   volume_staircase = True if vol_block_1 > vol_block_2 > vol_block_3

        # COIL TENSION SCORE (0–100):
        #   compression_component = max(0, (1 - compression_ratio)) * 40   # up to 40 pts
        #   drift_component = max(0, delivery_drift) * 20                    # up to ~20 pts
        #   quiet_component = quiet_accum_days * 2                           # up to 40 pts
        #   staircase_bonus = 8 if volume_staircase else 0
        #   coil_tension_score = min(100, compression_component + drift_component + quiet_component + staircase_bonus)

        # FILTER:
        #   compression_ratio < 0.80     (must be compressing)
        #   delivery_drift > 0           (delivery must be trending up)
        #   coil_tension_score >= 20     (minimum coil)

        # OUTPUT COLUMNS:
        # symbol, sector, market_cap_cr, compression_ratio, delivery_drift,
        # quiet_accum_days, volume_staircase, coil_tension_score, grade,
        # close, atr_old_pct, atr_new_pct, base_duration_days
        #
        # base_duration_days = number of sessions where ATR < atr_old (how long coiling)
        # grade = A≥75, B≥55, C≥35, D<35
```

**React UI Columns:** Symbol · Sector · MCap · Compression Ratio · Del Drift · Quiet Days · Staircase · Base Duration · Tension Score · Close

**UI Color Logic:**
- Compression Ratio: green < 0.65, yellow < 0.80, grey ≥ 0.80
- Del Drift: green > 0.3, yellow > 0, red ≤ 0
- Volume Staircase: green checkmark if true
- Tension Score: same Grade A/B/C/D badge system

**Filters:** Min Tension Score slider (0–100), Min Quiet Days (0–15), Sector dropdown, MCap range, Staircase Only toggle

---

## Scanner 3 — Float Exhaustion Scanner

**File names:**
- `myra_app/strategies/float_exhaustion_scanner.py` → class `FloatExhaustionScanner`
- `myra_web/src/views/FloatExhaustionScanner.tsx`
- API routes: `POST /api/float-exhaustion/scan` · `GET /api/float-exhaustion/status`
- Cache file: `models/float_exhaustion_cache.json`

**Concept:**
Every NSE stock has a calculable free float in shares. When cumulative 20-day delivery volume exceeds 15–40% of that free float, available supply is physically exhausted — remaining holders won't sell cheap. This is a supply-side physics signal, not a price signal.

**Python Strategy Logic:**

```python
class FloatExhaustionScanner:
    def __init__(self, min_mcap=200, max_mcap=50000, window_days=20, min_float_util_pct=10.0):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.window_days = window_days
        self.min_float_util_pct = min_float_util_pct

    def scan(self) -> pd.DataFrame:
        # 1. Get universe from fundamentals, including:
        #    - market_cap (use COALESCE(market_cap, marketCap))
        #    - promoter_holding_pct (use COALESCE(promoter_holding_pct, insider_holding_pct, 50.0))
        #    - free_float_pct (use COALESCE(free_float_pct, 40.0))
        #    Filter: market_cap BETWEEN min_mcap*1e7 AND max_mcap*1e7

        # 2. For each symbol:
        #    latest_close = close from most recent date in technical_data
        #    shares_total_approx = market_cap / latest_close
        #    available_float_pct = free_float_pct OR (100 - promoter_holding_pct - 15)
        #    free_float_shares = shares_total_approx * available_float_pct / 100

        # 3. Query technical_data for last window_days sessions:
        #    cum_delivery = SUM(delivery)  — this is shares delivered, not qty*price
        #    up_day_delivery = SUM(delivery) WHERE close > open  (smart float)

        # 4. Compute:
        #    float_util_pct = cum_delivery / free_float_shares * 100
        #    smart_float_ratio = up_day_delivery / free_float_shares * 100
        #    absorption_rate = mean(delivery[-5:]) / mean(delivery[-window_days:])
        #      (ratio of recent to overall — is pace accelerating?)

        # 5. Exhaustion tier:
        #    T3 CRITICAL  = float_util_pct >= 40
        #    T2 HIGH      = float_util_pct >= 25
        #    T1 ELEVATED  = float_util_pct >= 15
        #    WATCH        = float_util_pct >= 10

        # FILTER: float_util_pct >= min_float_util_pct AND free_float_shares > 0

        # OUTPUT COLUMNS:
        # symbol, sector, market_cap_cr, free_float_shares, free_float_pct_used,
        # cum_delivery_20d, float_util_pct, smart_float_ratio, absorption_rate,
        # exhaustion_tier, close, wk52_pos
        # Sort by float_util_pct DESC

        # SANITIZE: float fields — replace NaN/Inf with None before returning
```

**React UI Columns:** Symbol · Sector · MCap · Free Float Shares · Float Used% · Smart Float% · Absorption Rate · Tier · Close · 52W Pos

**UI Color Logic:**
- Tier badge: red glow = "T3 CRITICAL", orange = "T2 HIGH", yellow = "T1 ELEVATED", grey = "WATCH"
- Float Used%: color-coded progress bar inline (fill red as it approaches 40%)
- Absorption Rate: green > 1.3 (accelerating), yellow 1.0–1.3, grey otherwise
- Smart Float%: green > 60% of float_util_pct (mostly up-day buying)

**Filters:** Min Float Util% slider (10–50), Exhaustion Tier multi-select (T3/T2/T1/Watch), Sector dropdown, MCap range

---

## Scanner 4 — Seasonal Delivery Harvester

**File names:**
- `myra_app/strategies/seasonal_delivery_harvester.py` → class `SeasonalDeliveryHarvester`
- `myra_web/src/views/SeasonalDeliveryHarvester.tsx`
- API routes: `POST /api/seasonal-delivery/scan` · `GET /api/seasonal-delivery/status`
- Cache file: `models/seasonal_delivery_cache.json`

**Concept:**
Map each stock's average delivery_pct by calendar month over historical years. Find stocks currently in their historically strong delivery month AND already showing above-average delivery in the current month. Institutions are creatures of seasonal habit — catch them early.

**Python Strategy Logic:**

```python
class SeasonalDeliveryHarvester:
    def __init__(self, min_mcap=200, max_mcap=50000,
                 min_hist_del=40.0, min_consistency_pct=55.0, min_years=2):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_hist_del = min_hist_del          # only months historically active
        self.min_consistency_pct = min_consistency_pct  # happens most years
        self.min_years = min_years

    def scan(self) -> pd.DataFrame:
        current_month = datetime.today().month
        current_year = datetime.today().year

        # 1. Get universe (same pattern as other scanners)

        # 2. For each symbol, query all historical technical_data:
        #    GROUP BY strftime('%Y', date) AS year, strftime('%m', date) AS month
        #    → avg_del_month per year-month pair

        # 3. Build seasonal profile per symbol per month:
        #    hist_avg_del = mean(avg_del_month) across all years EXCEPT current year
        #    years_of_data = count of distinct years for this month
        #    grand_avg = overall mean delivery_pct for the symbol across all months
        #    consistency_pct = (years where avg_del_month > grand_avg) / years_of_data * 100

        # 4. For current month, compute:
        #    current_del = mean(delivery_pct) from technical_data WHERE date >= start of current month
        #    trading_days_so_far = count of rows in current month

        # 5. Filter:
        #    hist_avg_del >= min_hist_del          (historically active delivery month)
        #    consistency_pct >= min_consistency_pct (reliable pattern)
        #    years_of_data >= min_years             (enough history)
        #    current_del > hist_avg_del             (already triggering this year)
        #    trading_days_so_far >= 3               (at least 3 days of data this month)

        # 6. Compute:
        #    seasonal_edge = current_del - hist_avg_del  (how far above historical avg)
        #    early_signal = True if trading_days_so_far <= 5 and seasonal_edge > 5
        #      (season starting early = best entry window)
        #    seasonal_score = seasonal_edge * 2 + consistency_pct * 0.4 + years_of_data * 3

        # OUTPUT COLUMNS:
        # symbol, sector, market_cap_cr, current_month, hist_avg_del, current_del,
        # seasonal_edge, consistency_pct, years_of_data, early_signal,
        # seasonal_score, close, wk52_pos
        # Sort by seasonal_score DESC
```

**React UI Columns:** Symbol · Sector · MCap · Current Month · Hist Avg Del% · This Month Del% · Seasonal Edge(pp) · Consistency% · Years · Early · Score · Close

**UI Color Logic:**
- Seasonal Edge(pp): green > 15, yellow > 8, grey otherwise
- Consistency%: green ≥ 80%, yellow 60–79%, grey < 60%
- Early Signal: bright green "EARLY" badge if true
- Score: Grade A/B/C/D badges (same thresholds)
- Month shown as name ("Jun", "Jul", etc.) not number

**Extra UI Feature:**
Add a "View Month" selector (Jan–Dec) in the filter bar so users can preview upcoming months. When a future month is selected, the scan switches to show stocks that HISTORICALLY have that month as their strongest delivery month (show hist_avg_del + consistency_pct, no current_del for future months).

**Filters:** Min Consistency% slider (55–95), Min Seasonal Edge slider (0–25), Years of Data (2+/3+/4+), Early Signal toggle, Sector dropdown

---

## Scanner 5 — Wyckoff Automaton

**File names:**
- `myra_app/strategies/wyckoff_automaton.py` → class `WyckoffAutomaton`
- `myra_web/src/views/WyckoffAutomaton.tsx`
- API routes: `POST /api/wyckoff/scan` · `GET /api/wyckoff/status`
- Cache file: `models/wyckoff_cache.json`

**Concept:**
Automated Wyckoff phase detection using delivery_pct as the confirming lens. A Selling Climax with 70% delivery is categorically different from one with 20%. Detect: SC (Selling Climax), AR (Automatic Rally), ST (Secondary Test), Spring/Shakeout, SOS (Sign of Strength).

**Python Strategy Logic:**

```python
class WyckoffAutomaton:
    def __init__(self, min_mcap=200, max_mcap=50000, lookback_days=90):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.lookback_days = lookback_days

    def scan(self) -> pd.DataFrame:
        # For each symbol, fetch last lookback_days sessions of OHLCV + delivery_pct

        # STATS over full window:
        #   avg_vol_90 = mean(volume)
        #   vol_std_90 = std(volume)
        #   avg_del_90 = mean(delivery_pct)
        #   range_low_90 = min(low)
        #   range_high_90 = max(high)

        # EVENT DETECTION (scan each of the last 30 sessions):

        # SC — Selling Climax:
        #   volume > avg_vol_90 * 2.5
        #   close > (low + (high - low) * 0.35)   ← closes off lows
        #   delivery_pct > 60
        #   close <= range_low_90 * 1.07           ← near recent lows
        #   → wyckoff_event = 'SC', phase = 'Phase A'

        # SPRING — Undercut & Recovery:
        #   low < range_low_90 * 0.985             ← undercuts the range
        #   close > range_low_90                   ← recovers above range
        #   delivery_pct > 55
        #   → wyckoff_event = 'Spring', phase = 'Phase C'

        # SOS — Sign of Strength:
        #   close > (range_low_90 + (range_high_90 - range_low_90) * 0.55)  ← above midpoint
        #   volume > avg_vol_90 * 1.5
        #   delivery_pct > avg_del_90 * 1.3        ← above-avg delivery
        #   close > open                            ← up bar
        #   → wyckoff_event = 'SOS', phase = 'Phase D'

        # AR — Automatic Rally (after SC):
        #   Look for: within 10 sessions after a SC event for same symbol
        #   close > SC_close * 1.03 on declining volume
        #   → wyckoff_event = 'AR', phase = 'Phase A'

        # ST — Secondary Test:
        #   Retest of SC low within 5%, on volume < avg_vol_90 * 0.7
        #   delivery_pct < avg_del_90               ← weak delivery on retest = bullish
        #   → wyckoff_event = 'ST', phase = 'Phase B'

        # QUALITY SCORE per event:
        #   vol_ratio = event_volume / avg_vol_90
        #   del_ratio = event_delivery_pct / avg_del_90
        #   event_quality = (vol_ratio * 40 + del_ratio * 30 + delivery_pct * 0.3)
        #   Capped at 100

        # PHASE PROGRESSION:
        #   For each symbol, if multiple events detected, find the most recent
        #   and assign phase_stage = 'Phase A' / 'B' / 'C' / 'D'
        #   phase_complete_pct = rough % completion toward breakout
        #     A=25, B=50, C=75 (Spring detected), D=90 (SOS detected)

        # OUTPUT: one row per (symbol, event) — most recent event per symbol
        # Columns: symbol, sector, market_cap_cr, wyckoff_event, phase,
        #          phase_complete_pct, event_date, event_delivery_pct,
        #          vol_ratio, event_quality, range_low_90, range_high_90,
        #          close, days_since_event
        # Sort by phase_complete_pct DESC, event_quality DESC
```

**React UI Columns:** Symbol · Sector · MCap · Event · Phase · Phase% · Event Date · Days Ago · Del% · Vol Ratio · Range Low · Range High · Quality · Close

**UI Color Logic:**
- Event badge: SC = red, AR = yellow, ST = amber, Spring = purple, SOS = green
- Phase badge: Phase A = grey, B = blue, C = amber, D = green glow
- Phase%: mini progress bar (25/50/75/90 filled)
- Quality: same Grade A/B/C/D badges
- Days Ago: green ≤ 5, yellow ≤ 15, grey otherwise

**Filters:** Event type multi-select (SC / AR / ST / Spring / SOS / All), Phase filter, Min Quality slider, Max Days Since Event (5/10/20/All), Sector dropdown

---

## Integration Checklist

After building each scanner:

1. **`myra_web/myra_fastapi_server.py`** — add the 3 lines at top of file:
   ```python
   # Module-level state dict + lock + cache path for each scanner
   # GET /api/<scanner>/status endpoint
   # POST /api/<scanner>/scan endpoint
   ```

2. **`myra_web/src/App.tsx`** — add:
   ```tsx
   const LiquidityFlipDetector = lazy(() => import('./views/LiquidityFlipDetector'));
   // ... (all 5)
   // Add to route switch + sidebar nav
   ```

3. **`myra_web/src/views/<Name>.tsx`** — strictly follow DarvasBoxProScanner.tsx structure:
   - Same progress polling pattern (setInterval 2000ms)
   - Same stale banner (> 30 min warning)
   - Same Stats Summary cards at top
   - Same CSV export button
   - Same sort/filter pattern with useMemo filteredData
   - Same ScrollableTable with sticky thead

4. **Python strategy** — strictly follow AccumulationBaseScanner structure:
   - `__init__` takes filter params
   - `_db_path(key)` helper
   - `_get_universe()` queries fundamentals
   - `_get_tech_data(symbol, min_date)` queries technical_data
   - `scan()` returns `pd.DataFrame`
   - `_sanitize_float(value)` for NaN/Inf cleanup
   - All float fields sanitized before returning candidates

---

## Code Quality Rules

- Use `COALESCE` for nullable columns (market_cap, promoter_holding_pct, etc.)
- Wrap db connections in `with sqlite3.connect(...) as conn:`
- Never use f-string SQL — always parameterized `conn.execute(sql, (param,))`
- `try/except sqlite3.OperationalError` for missing columns (fallback query)
- All scan state dicts must serialize to JSON — replace NaN/Inf with `None`
- TypeScript interfaces must match Python output column names exactly
- Use `?.toFixed(2) ?? '—'` for nullable numeric cells in React
- Use `text-[#fafafa]` for primary text, `text-[#888]` for secondary (match existing dark theme)
- Tailwind only — no inline styles in React components

## Build Order

Build in this order (simplest SQL → most complex):
1. Liquidity Flip Detector ← start here
2. Operator Fingerprint Scanner
3. Float Exhaustion Scanner
4. Seasonal Delivery Harvester
5. Wyckoff Automaton

Read the existing `myra_app/strategies/accumulation_base_scanner.py` and
`myra_web/src/views/DarvasBoxProScanner.tsx` before writing any code — 
follow their patterns exactly.
