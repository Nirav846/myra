# Implementation Plan: Glossary Simplification

## Objective
Provide a compact, high-signal glossary that covers both Technical and Institutional (SMC-1) metrics.

## Phase 1: Content Design
- [x] Create a simplified definitions list:
    - **D-POC**: Institutional Floor (Price).
    - **Absorption**: Accumulation Strength (>150% is Elite).
    - **Tightness**: Price Squeeze (<1.5% is Prime).
    - **RDV**: Delivery Velocity.
    - **AEON**: AI Conviction level (25/50/100% Load).

## Phase 2: UI Implementation
- [x] Update `GLOSSARY` string in `myra_app/myra.py`:
    - Fix any Rich markup errors (Fixed: Corrected yellow/green tags).
    - Compact the layout to use fewer vertical lines.

## Phase 3: Validation
- [ ] Run `myra.py` and trigger a scan.
- [ ] Verify the glossary is readable and doesn't cause UI flickering.
