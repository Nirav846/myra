# MYRA (Myra Yield & Research Analytics)

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![SQLite](https://img.shields.io/badge/Database-SQLite%20(Sidecars)-blue)
![Parquet](https://img.shields.io/badge/Storage-Parquet%20Lake-orange)
![License](https://img.shields.io/badge/License-MIT-green.svg)

**MYRA** is an atomic trading system for the National Stock Exchange (NSE) of India. It combines a factor-based positional scoring engine (v2.5), institutional activity tracking, and resilient data pipelines for 1-24 month holdings.

---

## 🚀 Key Features

*   **⚡ v2.5 Positional Engine**: Factor-based ranking with trend, stability, delivery, liquidity, base, and fundamental scores
*   **🏦 Institutional Intelligence**: Tracks insider trades (> ₹10L), large deals, and delivery divergence scoring
*   **🧬 Strategy Framework**: Modular BaseStrategy with market mood detection, Kelly criterion sizing, and AI hooks
*   **🛡️ Resilient Data Pipeline**: Watchdog for stuck scans, process timeouts, and adaptive source selection
*   **🏎️ Performance First**: Vectorized operations, multiprocessing worker pool, and optimized SQLite sidecars

---

## 🏗️ Architecture: Atomic Trading System

MYRA operates on a modular architecture with SQLite sidecars and a Parquet Indicator Lake to prevent file locking and schema contention.

### 1. SQLite Sidecars
*   `technical.db`: OHLCV, delivery, VWAP data
*   `institutional.db`: Insider trades, large deals
*   `meta.db`: Symbols master, benchmarks
*   `valuation.db`: Fundamentals, quarterly results

### 2. Modular Components
*   **Engine (UNIVERSAL SQL v12)**: Unified precompute for scans
*   **PositionalScorer**: Vectorized scoring with regime adjustment
*   **Librarian**: Decomposed into Core, Intelligence, Ingestor, Sync modules
*   **Factors**: BaseFactor abstract class with DeliveryFactor, RSFactor, IASFactor
*   **Strategies**: BaseStrategy framework with 30+ implementations

---

## 🛠️ Tech Stack

*   **Core Language:** Python 3.10+
*   **Databases:** SQLite (sidecars)
*   **Data Lake Storage:** Apache Parquet
*   **Analytics & Quant:** `pandas`, `numpy`, `pandas_ta`
*   **Machine Learning:** `xgboost`, `tensorflow`
*   **UI / CLI Experience:** `rich`, `myra_log`
*   **NSE Data:** `myra_core` (localized), `morningstartools`

---

## ⚙️ Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/myra.git
    cd myra
    ```

2.  **Set up a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

---

## 🖥️ Usage Examples

**Run the Core Scanner:**
```bash
python myra_app/myra.py
# Select strategy (e.g., 34 for Surpriver v2, 31 for AEON Agent)
```

**Positional Analysis:**
```bash
python myra_app/positional_engine.py --regime neutral --universe nifty500
```

**Data Backfill:**
```bash
python tools/backfill_year.py --target institutional --year 2024
```

---

## 📂 Project Structure

```text
MYRA/
├── myra_core/          # Localized dependencies from PKScreener
├── myra_app/           # Main application
│   ├── engine.py       # Universal SQL precompute engine
│   ├── positional_engine.py  # v2.5 scoring system
│   ├── factors/        # Modular factor implementations
│   ├── strategies/     # 30+ strategy implementations
│   └── librarian/      # Modular data persistence
├── tools/              # Utilities and maintenance scripts
├── test/               # Test suite
├── data/               # SQLite sidecars
├── conductor/          # Workflow orchestration
└── PROJECT_RULES.md    # Engineering guidelines
```

---

## 🤝 Contribution Guidelines

MYRA is built on strict engineering principles. Before contributing:

1. Read `PROJECT_RULES.md`
2. No loops on large datasets (use vectorized operations)
3. All dates must be `datetime64[ns]`
4. Thread-safe database access with locks
5. Include tests for new features

**PR Title Format:**
* Performance: `⚡ [description]`
* Security: `🔒 [description]`
* Features: `✨ [description]`

---

## 📊 Strategy Ecosystem

### Core Strategies
*   **Surpriver v2**: Multi-window institutional accumulation detection
*   **AEON Agent**: Evolutionary Strategy optimization for SMC timing
*   **Smart Money**: Delivery spikes, absorption, institutional flow

### Alpha Strategies
*   Delivery clusters, liquidity vacuums, supply absorption
*   RS leaders, bear traps, stage 2 continuation
*   Bottom hunter, multibagger early detection

### Scanners
*   Technical: RSI divergence, VWAP pullback, breakouts
*   Institutional: Insider signals, large deal momentum
*   Fundamental: Value, growth, quality screens
