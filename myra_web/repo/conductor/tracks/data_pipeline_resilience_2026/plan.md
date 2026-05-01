# Implementation Plan: Data Pipeline Resilience (v9.0)

## Phase 1: Stability & Fault Tolerance
- [ ] Implement `run_with_hard_timeout` process wrapper in `engine.py`.
- [ ] Refactor `GhostSession.get` in `fetcher.py` to prioritize `requests` with strict timeouts.
- [ ] Enable `WAL` mode in `fetcher.py` for `network_cache.sqlite`.
- [ ] Add `ScanWatchdog` thread to `engine.py`.

## Phase 2: Data Integrity & Validation
- [ ] Add `validate_data` method to `DataFetcher` in `fetcher.py`.
- [ ] Implement `Global Safety Fuse` in `Engine.run_scan`.
- [ ] Add `fetch_bhavcopy_with_retry` loop to `DataFetcher`.
- [ ] Implement `how='left'` merge and universe coverage check in `_merge_zip_mto`.

## Phase 3: Intelligent Decision Engine
- [ ] Implement `score_data_quality` in `DataFetcher`.
- [ ] Refactor `fetch_ohlcv_delivery` to iterate all sources and select the one with the highest score.
- [ ] Implement `Delivery Density` enforcement.

## Phase 4: Adaptive Learning & Trust
- [ ] Implement `source_stats` persistence in `network_cache.sqlite`.
- [ ] Add `get_reliability` with Recency-Decay weights.
- [ ] Update `fetch_ohlcv_delivery` to use `Weighted Final Score` (70% Quality / 30% Reliability).
- [ ] Implement `Exploration Mode` (10% chance to audit all mirrors).

## Phase 5: Market Context Awareness
- [ ] Implement `Learning Gate` to freeze reliability updates on holidays.
- [ ] Add `Dynamic Thresholding` for special market sessions.
- [ ] Implement `Post-Holiday Cooldown` weight adjustment.

## Phase 6: Truth Validation Layer
- [ ] Implement `check_price_consistency` against previous day's snapshot.
- [ ] Add `check_sector_coverage` diversity guard.
- [ ] Implement `Cache Validation Hash` to prevent corruption propagation.
- [ ] Implement `Snapshot Staleness Guard` (blocking scans on >48h old data).
