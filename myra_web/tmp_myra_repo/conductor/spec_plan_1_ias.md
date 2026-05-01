# SPECIFICATION: MYRA Governance & IAS Engine (Plan 1)

## 1. Objective
Build a dual-layer data ingestion engine for long-term governance metrics and implement the **Institutional Activity Score (IAS)** as the primary ranking factor for positional investing.

## 2. Data Layer (`db/governance.db`)
Dedicated sidecar for structural data.

### 2.1 Schema
- **`sast_disclosures`**:
    - `disclosure_id` (TEXT PRIMARY KEY) - Hash of symbol + date + acq_name + qty.
    - `symbol` (TEXT INDEX)
    - `date` (TEXT)
    - `acq_name` (TEXT)
    - `qty_pct` (REAL)
    - `type` (TEXT) - 'BUY' / 'SELL'
- **`pledged_history`**:
    - `symbol`, `date`, `promoter_holding`, `pledged_pct`, `change_qoq`
- **`shareholding_history`**:
    - `symbol`, `date`, `fii_pct`, `dii_pct`, `promoter_pct`

## 3. Ingestion Strategy
### 3.1 Daily Incremental (SAST)
- **Endpoint**: `/api/corporate-sast-reg29`
- **Logic**: Fetch last 3 days of disclosures. Upsert into `sast_disclosures` using `disclosure_id`.
- **Frequency**: Every daily sync (post-market).

### 3.2 Weekly Full Sweep (Pledge & Shareholding)
- **Endpoint**: `/api/corporate-pledged-info` & `/api/equity-shareholding`
- **Logic**: 
    - Full sweep of Nifty 500 (Primary) + Rest of market (Best effort).
    - Calculate `change_qoq` by comparing with previous snapshot in `db`.
- **Frequency**: Every Saturday.

## 4. Institutional Activity Score (IAS) v1.0
Calculated as a weighted aggregate of 5 pillars.

### 4.1 Scoring Matrix
1. **SAST Score (35%)**: Based on net 30d accumulation %.
    - >= 1.0%: 10 pts | >= 0.5%: 8 pts | > 0%: 6 pts.
    - Single transaction > 1%: +2 bonus.
2. **Delivery Score (25%)**: Based on 5d Avg Delivery.
    - > 55%: 10 pts | > 45%: 8 pts | > 35%: 6 pts.
    - Cluster Bonus: 3+ spikes in 5 days: +2 pts.
3. **Price Structure (15%)**:
    - Tight range + Higher Lows: 9 pts.
    - Tight range: 7 pts.
4. **Volume Pattern (15%)**:
    - Dry-up + Spike: 10 pts.
    - Declining trend: 7 pts.
5. **Volatility Compression (10%)**:
    - ATR_5 < ATR_20 * 0.7: 10 pts.

### 4.2 Interaction Bonuses
- **Confluence**: SAST >= 8 AND Delivery >= 8 -> +1.0 IAS.
- **Trap**: Bear Trap detected + High Delivery -> +1.0 IAS.
- **Ready**: Compression >= 8 + Price @ Resistance -> +0.5 IAS.

## 5. Backtesting & ML Pipeline
- Implement `IASManager.export_to_parquet(symbol)` to generate multi-year training sets.
- Columns: OHLCV + Delivery + IAS_Components + Forward_Returns (3m, 6m).

## 6. Verification Steps
1. Test `/api/corporate-sast-reg29` with 3-day lookback.
2. Verify `pledged_info` parsing for SME vs Largecap.
3. Validate IAS calculation for a known high-conviction symbol (e.g., COFORGE).
