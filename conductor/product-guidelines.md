# Product Guidelines

## Core Principles
- **Local-First:** Prioritize local data processing and DuckDB storage.
- **Institutional Quality:** Use professional data sources and institutional-grade math.
- **Language Mandate:** All communication, logging, and comments MUST be in English.

## Architectural Isolation (MYRA v2.5)
- **Fetcher-Only Access:** Only `fetcher.py` interacts with the internet.
- **Librarian-Only Access:** Only `librarian.py` interacts with DuckDB.
- **No Direct Recomputing:** Technicals (SMA, ATR, VWAP) must be delta-computed and stored.
- **ML Lifecycle:** Trend models must use a 60-day lookback and provide probability-based confidence scores.
- **Stage Filtering:** Smart Money signals must be filtered by Weinstein Stage (1 & 2 for entry, 4 for warning).
- **Momentum Cross-referencing:** Institutional deals must be cross-referenced with Stage 2 breakouts for momentum signals.
- **Absorption Mandate:** Support reversals must show evidence of institutional absorption (Narrowing spread + High Closing Position).
- **VSA Quality:** VSA signals must prioritize "Effort vs Result" patterns backed by high (>60%) institutional delivery.
- **Thread Safety:** Maintain thread-safe data lake access for concurrent engine reads/writes.

## Coding Style
- Follow PEP 8 for Python code.
- Ensure strict typing and comprehensive docstrings.
- Use explicit error handling with multi-tiered fallbacks in `fetcher.py`.

## User Experience (UI/UX)
- CLI-first design with a non-scrolling, responsive full-screen "Mission Control" layout.
- **Result Prioritization:** Always show Stage 2 (↗) leaders first, then by Star rating and Money Flow.
- **Visual Decorators:** Diamond Row (Cyan) for top 5% performers; dimming for Stage 4 risks.
- Consistent branding across Telegram alerts and CLI output.
- Performance: Minimize network latency and optimize SQL queries (DuckDB).
- System Visibility: Real-time DuckDB status, system date, and live background sync progress (task name + percentage) in the footer.
