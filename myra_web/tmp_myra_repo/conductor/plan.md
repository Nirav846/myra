# Track: Institutional Deep-Dive Pipe (TRILOGY-DFR)
**Status: DRAFT**
**Owner: Lead Quantitative Architect**

## 1. Objective
Implement a targeted, secondary analysis pipeline that filters technical scan results through institutional-grade Valuation (DCF) and Health (Red Flag) logic inspired by the `deep-financial-research` repository.

## 2. Background & Motivation
MYRA currently excels at Technical and Structural (SMC) analysis. To provide institutional-grade DD without overwhelming our data sources, we need an on-demand "Deep-Dive" pipe that only triggers for high-conviction technical setups.

## 3. Proposed Solution
### 3.1 Valuation Engine (DCF)
- **Algorithm:** Gordon Growth Model with 5-year forward projections.
- **Growth Decay:** 15% CAGR cap with 5% annual step-down.
- **Sector-Adjusted WACC:**
  - `Defensives (IT, FMCG, Pharma)`: 10% - 11%
  - `Cyclicals (Auto, Metals)`: 12% - 13%
  - `Aggressive (Infra, Power, Real Estate)`: 14% - 15%

### 3.2 Health Monitor (Red Flags)
- **Cash Quality:** `(Net Profit / Cash from Ops) > 1.2` (Warning: Accrual Heavy).
- **Receivables:** `Current Debtor Days > 120% of 3-Year Mean`.
- **Promoter Pledge:** `Pledged Shares > 25%` or `Pledge Increase > 5% QoQ`.
- **Debt Service:** `Interest Coverage Ratio < 3.0`.

### 3.3 The "Deep-Dive" Workflow
1. User runs a technical scan (e.g., "Find FVG demand zone stocks").
2. MYRA displays technical results.
3. MYRA prompts: `[D] Run Institutional Deep-Dive on these 5 stocks?`
4. On `D`, MYRA fetches deep history (5y Annuals) and runs the DFR logic.
5. Displays a refined **"Value + Health"** report.

## 4. Implementation Phasing
### Phase 1: Engine & Schema
- Create `myra_app/institutional_pipe.py`.
- Define `config/valuation_rules.json`.
- Add `deep_valuation` column to `fundamentals` table in DuckDB.

### Phase 2: Targeted Fetcher
- Update `myra_app/fetcher.py` to support deep annual history retrieval.
- Implement caching to prevent redundant deep fetches.

### Phase 3: Results Integration
- Hook into `myra_app/results_manager.py` to offer the Deep-Dive prompt.
- Format the final "Institutional Grade" report output.

## 5. Verification
- **Unit Tests:** `test/test_dcf_logic.py` (Verify math against DFR repo).
- **Integration Tests:** `test/test_institutional_pipe.py` (Verify end-to-end flow).
- **Data Audit:** `python myra_app/technical_audit.py`.
