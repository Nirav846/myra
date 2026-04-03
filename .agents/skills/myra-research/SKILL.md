---
name: myra-research
description: Specialized financial research for NSE API changes, bypasses, and data source discovery. Use when fetcher.py fails, NSE firewalls change, or when looking for new institutional data sources (TradingQnA, r/algotrading, GitHub).
---

# MYRA Research Skill

This skill provides a specialized workflow for investigating and patching data source issues in MYRA, specifically for NSE (National Stock Exchange of India) and other financial data providers.

## Core Workflow: "Search, Extract, Validate, Route"

### 1. Search (Deep Financial Discovery)
When a primary source fails or headers become obsolete:
- Search for the specific error or symptom (e.g., "NSE 403 Forbidden requests Python 2026").
- Prioritize developer forums and communities:
    - **TradingQnA (Zerodha)**: Best for NSE-specific API nuances.
    - **r/algotrading**: General bypass techniques and library updates.
    - **GitHub Issues**: Search within repositories like `nsepython`, `nsetools`, or `yfinance`.

### 2. Extract (Logic & Headers)
Extract the working solution, focusing on:
- **Cookies/Session logic**: How to initialize a session on the main page before hitting the API.
- **Headers**: Specific `User-Agent`, `Referer`, and `Accept` combinations.
- **Payloads**: Changes in JSON structure or required parameters.

### 3. Validate (Sandbox First)
**MANDATE**: Never patch `fetcher.py` or `myra_sources.json` directly without validation.
- Create a `temp_research_test.py` script.
- Implement only the fetching logic with the extracted headers/logic.
- Run the script and confirm it returns valid, non-empty data.

### 4. Route (The MYRA Way)
Once validated, update the system according to **Modular Architecture v2.5**:
- **Headers**: Update the `headers` section in `myra_sources.json`.
- **Endpoints**: Update or add to `data_streams` in `myra_sources.json`.
- **Fetcher**: If the logic change is structural (e.g., requires a new session handler), update `myra_app/fetcher.py`.

## Example Trigger Phrases
- "NSE API is returning 401 Unauthorized, find a fix."
- "Find a new source for NSE Bulk/Block deals as the current one is slow."
- "Search TradingQnA for the latest NSE cookie refresh logic."
- "Fetcher is broken, investigate the latest firewall changes."

## Guidelines
- **English Only**: All logging and comments in test scripts must be in English.
- **No Hallucination**: Only use URLs found in research or existing in `myra_sources.json`.
- **Security**: Never include personal API keys or session tokens in the codebase.
