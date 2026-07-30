# MYRA Autonomous Improvement Loop

This directory contains a custom OpenCode agent system that runs
autonomous Plan-Build-Push cycles for the MYRA codebase.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   YOUR SESSION                        │
│  opencode myra-orchestrator                           │
└──────────────────┬───────────────────────────────────┘
                   │ task tool
                   ▼
┌──────────────────────────────────────────────────────┐
│             myra-orchestrator (primary)               │
│  Determines task type, routes to subagents, pushes   │
└──────┬──────────────────────────────────┬────────────┘
       │ task                             │ task
       ▼                                  ▼
┌──────────────────┐          ┌────────────────────────┐
│  myra-planner     │          │  myra-builder           │
│  (subagent)       │          │  (subagent)             │
│  Analyzes codebase│          │  Implements tasks       │
│  Outputs 3-7 tasks│          │  Verifies & commits     │
└──────────────────┘          └────────────────────────┘
```

### Files

| File | Role |
|------|------|
| `.opencode/agents/myra-orchestrator.md` | Primary agent — runs the loop |
| `.opencode/agents/myra-planner.md` | Subagent — analyzes codebase, outputs task list |
| `.opencode/agents/myra-builder.md` | Subagent — implements one task, tests, commits |
| `.agent/rules/02-strategy-backtest.md` | Rule — backtest workflow for strategy tasks |
| `.agent/rules/03-strategy-ideas.md` | Rule — pre-approved strategy ideas |
| `opencode.json` | Sets default agent to myra-orchestrator |

### Task Types

Tasks are prefixed `[MAINTENANCE]` or `[STRATEGY]`:

- **`[MAINTENANCE]`** — test gaps, code quality, documentation, performance.
  Builder implements directly, no backtest needed.
- **`[STRATEGY]`** — new scanners, scoring improvements.
  Builder MUST backtest first following `.agent/rules/02-strategy-backtest.md`.
  Only proceeds to code if the decision gate passes.

## Quick Start

```powershell
# Run one improvement cycle (Plan → Build → Push)
opencode myra-orchestrator
```

The orchestrator will:
1. Call the Planner to analyze the codebase
2. Execute each task through the Builder (with backtesting for strategy tasks)
3. Push all changes at the end

## Running Strategy Backtests Only

To run a specific strategy idea from the idea bank:

```powershell
opencode myra-orchestrator

# Then type:
# "Check Idea 2 from the strategy idea bank: Quality Score Filter.
#  Run the backtest first and report the results. Only code if profitable."
```

The Orchestrator will read `.agent/rules/03-strategy-ideas.md`, create a
strategy task, and the Builder will backtest before writing any code.

## Maintenance-Only Cycle

To skip strategy work and only fix issues:

```powershell
opencode myra-orchestrator

# Then type:
# "Run a maintenance-only cycle: test gaps, code quality, docs, performance."
```

The Planner will prioritise only `[MAINTENANCE]`-prefixed tasks.

## How to Add New Strategy Ideas

1. Edit `.agent/rules/03-strategy-ideas.md`.
2. Add your idea following the existing format:
   - Title, description of the scoring logic.
   - What data sources are needed.
   - How to backtest (sample size, dates, metrics).

The Planner will pick it up automatically on the next cycle.

## How to Modify the Backtest Rules

Edit `.agent/rules/02-strategy-backtest.md` to change:
- Backtest sample size (default: 300-500 symbols).
- Number of test dates (default: 8-12).
- Forward return windows (default: 60d, 120d, 180d).
- Decision gate thresholds.

## Verification

```powershell
# List available agents
opencode agent list

# Should show:
#   myra-orchestrator (primary)
#   myra-planner (subagent)
#   myra-builder (subagent)
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `myra-orchestrator` not found | `default_agent` in opencode.json is wrong | Check `opencode.json` has `"default_agent": "myra-orchestrator"` |
| Planner outputs no tasks | Codebase is clean or files missing | Check `.agent/rules/` files exist |
| Builder skips verification | Agent prompt not followed | Check `.opencode/agents/myra-builder.md` permissions |
| `task` tool fails | Subagent not registered | Check `opencode agent list` shows all 3 agents |
| Backtest data stale | No recent market data | Check `technical_data` table freshness |
