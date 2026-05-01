# NSE Surpriver v2 (Quant-Anomaly)

## Objective
Implement a multi-window institutional accumulation and anomaly detection engine inspired by the `tradytics/surpriver` repository.

## Components
- Multi-Window Consistency (5, 10, 15, 20, 30 days)
- Delivery-Weighted Z-Score Analysis
- Supply Absorption Vibe Check
- Multi-Scale Volatility Compression (Tightness)

## Status
- [x] Strategy Implementation (`myra_app/strategies/surpriver_v2.py`)
- [x] UI Integration (`myra_app/myra.py`)
- [x] Glossary Updates
- [x] Final Validation with Live Data

## Documents
- [Specification](./spec.md)
- [Implementation Plan](./plan.md)
