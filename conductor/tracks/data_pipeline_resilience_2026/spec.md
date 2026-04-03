# Specification: Data Pipeline Resilience (v9.0)

## Goal
Transform MYRA's data acquisition layer into an institution-grade, resilient, and adaptive engine capable of handling NSE market data nuances without manual intervention.

## Architectural Layers

### Layer 1: Stability & Fault Tolerance
- **Problem:** Zombie workers and multiprocessing deadlocks causing scanner freezes.
- **Solution:** Isolated process wrappers with hard timeouts (Fix 4) and Requests-First fetching (Fix 5).
- **Persistence:** SQLite WAL mode (Fix 6).

### Layer 2: Data Integrity & Reliability
- **Problem:** Partial Bhavcopies poisoning the Indicator Lake.
- **Solution:** strict validation gate (Fix 9), global safety fuses (Fix 10), and specialized NSE retries (Fix 11).

### Layer 3: Intelligent Decision Engine
- **Problem:** First-come-first-serve source selection accepting low-quality data.
- **Solution:** Confidence-based Quality Scoring (Fix 13) and Global Best-Source Selection (Fix 14).

### Layer 4: Adaptive Learning & Memory
- **Problem:** Stateless per-run decisions missing historical reliability.
- **Solution:** Source Reliability Memory (Fix 16) and Weighted Recency-Decay Fusion (Fix 17).

### Layer 5: Market Context Intelligence
- **Problem:** Reliability poisoning on holidays or special sessions.
- **Solution:** Holiday shields (Fix 19), dynamic thresholding (Fix 20), and post-holiday cooldowns (Fix 22).

### Layer 6: Truth Validation (Absolute Correctness)
- **Problem:** High-scoring stale data served as fresh.
- **Solution:** Cross-day consistency checks (Fix 27), sector coverage guards (Fix 28), and cache integrity hashes (Fix 29).

## Key Components to Modify
- `myra_app/fetcher.py`: Core fetch logic, scoring, and reliability memory.
- `myra_app/engine.py`: Multiprocessing orchestration and global safety fuses.
- `myra_app/librarian_ingestor.py`: Ingestion logic and partial data detection.
