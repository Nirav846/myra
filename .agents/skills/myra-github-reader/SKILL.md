---
name: myra-github-reader
description: Automated scanning of financial open-source repositories (nsepython, OpenStockIndia, yfinance) to extract updated API logic and session handling. Use when a data source breaks or to find how others are bypassing NSE firewalls.
---

# MYRA GitHub Reader Skill

This skill allows MYRA to autonomously monitor and extract logic from relevant open-source projects to keep its data-fetching layer (`fetcher.py`) resilient.

## Target Repositories
- **nsepython**: The gold standard for NSE-specific API handling.
- **OpenStockIndia**: Focuses on Indian market data unification.
- **yfinance / nse-india**: For general financial data and unofficial NSE API access.

## Core Workflow: "Clone, Analyze, Adapt, Route"

### 1. Clone/Fetch (Ephemeral Research)
When instructed to scan a repository:
- Use `web_fetch` to read the repository's `README.md`, `requirements.txt`, and core files (e.g., `nsepython/nsepython.py`).
- Look for the latest commits and issue discussions.

### 2. Analyze (Logic Extraction)
Focus on:
- **Session Handlers**: How they manage the `nsit`, `nse_session`, or other cookies.
- **Dynamic Headers**: Any new headers they've added to their `headers` dictionary.
- **API Endpoints**: New or changed URLs for bhavcopy, delivery data, or corporate actions.

### 3. Adapt (The MYRA Context)
- Extract only the core logic (e.g., a specific `requests` call or session management class).
- **MANDATE**: Do not copy entire libraries. Adapt the logic to work within MYRA's `fetcher.py` and `librarian.py` layers.

### 4. Route (Validation)
- Create a temporary script (e.g., `github_logic_test.py`) to verify the extracted logic.
- Follow the **Trust but Verify** protocol from `GEMINI.md`:
    - Run the standalone script.
    - Confirm it bypasses current NSE firewalls.
    - If successful, incorporate the logic into `myra_app/fetcher.py`.

## Example Trigger Phrases
- "Scan the latest commits in the nsepython repository for updated session logic."
- "Extract the delivery data fetching logic from the OpenStockIndia GitHub."
- "Check yfinance issues for 'NSE 403' solutions."
- "Check how nsepython handles NSE's new ZIP bhavcopy format."

## Guidelines
- **Targeted Extraction**: Only extract the code directly related to the fix or feature.
- **Dependency Management**: Check `requirements.txt` of the target repository to see if new libraries are needed (ensure they are added to MYRA's `authorized_libraries` in `myra_sources.json`).
- **English Only**: All adapted code comments and logs must be in English.
