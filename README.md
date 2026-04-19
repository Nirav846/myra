# MYRA (Myra Yield & Research Analytics)

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![SQLite](https://img.shields.io/badge/Database-SQLite%20(Atomic%20Trilogy)-blue)
![Parquet](https://img.shields.io/badge/Storage-Parquet%20Lake-orange)
![License](https://img.shields.io/badge/License-MIT-green.svg)

**MYRA** is a specialized, high-performance stock screening and research analytics platform designed explicitly for the National Stock Exchange (NSE) of India. Built for the modern quant era, it emphasizes high-fidelity technical analysis (OHLCV, Delivery, VWAP), institutional activity tracking, and an advanced, self-healing data architecture.

---

## 🚀 Key Features

*   **⚡ High-Performance Scanner Engine**: Optimized for extreme speed on low-resource hardware (e.g., AMD APUs). Leverages Smart Money Concepts (SMC), Volume Spread Analysis (VSA), and multi-timeframe technical screening.
*   **🏦 Institutional Intelligence**: Tracks institutional moves natively. Features include tracking insider trades with strict materiality filters (> ₹10 Lakhs) and calculating the 'Underwater Signal' (LTP < Insider Cost Basis) to identify institutional accumulation zones.
*   **🧬 Evolutionary ML (AEON Agent)**: Implements Deep Evolution Strategies (DES) and Dilated Convolutional Neural Networks (CNN) for sequence-to-sequence forecasting and vectorised ML conviction scoring.
*   **🛡️ Self-Healing Data Layer**: Automatic retrieval and backfill of missing metrics via the robust `DataAdapter` and `Librarian` modules, ensuring your pipelines never dry up.
*   **🏎️ Performance First**: Strict adherence to vectorized Pandas/NumPy operations, DB query batching, and eliminating hidden Python loops for O(N) or better algorithmic scaling.

---

## 🏗️ Architecture: Modular Architecture v3.2 (Atomic Trilogy)

MYRA operates on a decoupled, highly resilient architecture known as the **Atomic Trilogy (v3.2 Stable)**. This design isolates data, indicators, and execution logic to prevent file locking, schema contention, and performance bottlenecks. All multi-step DB writes must be thread-safe, utilizing `with lib._db_lock:` and WAL mode.

### 1. SQLite Sidecars (The Data Base)
Instead of a monolithic database, MYRA uses eight active, domain-specific SQLite databases mapped in LibrarianCore:
*   `technical.db`: Core price action, volume, and raw market data.
*   `meta.db`: System metadata, job states, and configuration.
*   `institutional.db`: Insider trading, bulk deals, and corporate actions.
*   `governance.db`: Compliance, audits, and access logs.
*   `valuation.db`: Fundamental metrics and derived valuations.
*   `scoring.db`: Cross-sectional scoring and rankings.
*   `calendar.db`: Trading holidays and market schedules.
*   `network_cache.sqlite`: Aggressive caching for stealth session requests.

### 2. Parquet Indicator Lake (The Cache)
**Strict Rule 26 Enforcement:** To avoid schema bloat in SQLite and accelerate read times, *all* calculated technical indicators (e.g., SMA, RSI, VWAP) MUST be read strictly from the isolated Parquet Indicator Lake via `precompute_indicators()`. Do NOT read technical indicators from SQLite.

### 3. Unified Data Access (The Gateway)
The `DataAdapter` and `IndicatorManager` serve as the singular interface, abstracting the SQL/Parquet split. Strategy logic simply asks for data, and the adapter routes the request seamlessly. Standardized quantitative parameters like the SMC Fair Value Gap (FVG) threshold must be clearly separated: detection threshold is 0.2%, and mitigation/invalidation threshold is 1.5%.

---

## 🛠️ Tech Stack

*   **Core Language:** Python
*   **Databases:** SQLite (Sidecars)
*   **Data Lake Storage:** Apache Parquet
*   **Analytics & Quant:** `pandas`, `numpy`, `pandas_ta`
*   **Machine Learning:** `xgboost`, `tensorflow` (Dilated CNNs)
*   **UI / CLI Experience:** `rich`, `myra_log` (for minimalist terminal interactions)
*   **NSE Data:** `PKNSETools`, `PKDevTools`, `morningstartools`
*   *(Note: DuckDB integration has been officially deprecated in v3.0)*

---

## ⚙️ Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/myra.git
    cd myra
    ```

2.  **Set up a virtual environment (Recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

---

## 🖥️ Usage Examples

MYRA is primarily driven via its CLI. Ensure you are in the project root before running commands.

**Run the Core Scanner:**
```bash
python myra_app/cli.py --scan "SMC" --timeframe "1D"
```

**Fetch and Update Institutional Data (Insider Trades):**
```bash
python tools/backfill_year.py --target institutional --year 2024
```

*(Note: Actual CLI commands may vary based on the specific modules you are invoking. Check `myra_app/cli.py` for full options.)*

---

## 📂 Project Structure

```text
MYRA/
├── myra_core/          # Core engine logic, adapters, and ML agents
├── myra_app/           # Presentation layer, CLI, and high-level orchestrators
├── tools/              # Utilities, data fetchers, and maintenance scripts
├── test/               # Comprehensive pytest suite
├── docs/               # Additional documentation and research
├── data/               # (Generated) SQLite Sidecars
│   └── indicators/     # (Generated) Parquet Lake
├── conductor/          # Workflow and job scheduling orchestration
└── PROJECT_RULES.md    # Strict engineering mandates and guidelines
```

---

## 🤝 Contribution Guidelines & Standards

We welcome contributions, but MYRA is built on a strict engineering philosophy to maintain its edge.

### Core Philosophy:
> **Performance > Readability > Convenience**

Before submitting a PR, you **must** read and adhere to `PROJECT_RULES.md`.

**Key Mandates:**
*   **No Loops:** `iterrows()`, `apply()` on large datasets, and `DataFrame.append()` inside loops are strictly banned. Use vectorized `.isin`, `.map`, and boolean masking.
*   **Date Handling:** All dates must be native `datetime64[ns]`. Do not use `.strftime()` for comparisons.
*   **Testing:** PRs must include tests and be fully deterministic. If you introduce a loop (only when vectorization is impossible), you must include a benchmark.
*   **PR Titles:**
    * Security: `🔒 [security fix description]`
    * Performance: `⚡ [performance improvement description]`
    * Testing: `🧪 [testing improvement description]`

Please review the full ruleset in [PROJECT_RULES.md](PROJECT_RULES.md) before pushing code.
