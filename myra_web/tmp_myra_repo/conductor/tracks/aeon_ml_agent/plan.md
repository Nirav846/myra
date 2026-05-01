# Implementation Plan: MYRA-AEON Evolutionary Agent (ML-1)

## Objective
Implement and train the Evolutionary Strategy (ES) Agent to optimize SMC-1 entry and exit timing.

## Phase 1: Neural Core & Environment
- [x] Implement `EvolutionaryAgent` class with weight gene mapping.
- [x] Create `SMCEnvironment` with vectorized evaluation for high-speed training.
- [x] Implement `DeepEvolutionStrategy` (NES-style) gradient estimation.

## Phase 2: Evolutionary Training
- [x] Create `train_aeon.py` to optimize agent genes on Nifty 50 historical data.
- [~] Execute training (100 iterations) to establish the base population. (Active: Background Process).
- [ ] Implement `aeon_monitor.py` to track population fitness.

## Phase 3: Real-time Conviction (Inference)
- [x] Implement `AEONEngine` in `myra_app/ml_engine.py` to load joblib weights.
- [x] Add Strategy 31 (AEON Agent) to `myra.py`.
- [ ] Refine `AEON_Conviction` display with color-coded confidence levels.

## Phase 4: Validation
- [ ] Run Strategy 31 and confirm "TACTICAL", "CORE", or "CONVICTION" labels are shown.
- [ ] Cross-reference Agent conviction with SMC Phases.
