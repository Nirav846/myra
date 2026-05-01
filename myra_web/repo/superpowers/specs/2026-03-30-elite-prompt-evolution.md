# Specification: Elite Prompt Evolution (MYRA v2.5 Hardened)

## 1. Objective
Enhance the reliability, transparency, and signal-to-noise ratio of Gemini CLI interactions within the MYRA stock screener workspace.

## 2. State Preservation Pattern
### 2.1 The Session Map
For any task requiring more than 3 conversational turns, the agent MUST maintain a `data/SESSION_MAP.md` file.
- **Header:** `# Session Map: <Task_Name>`
- **Columns:** `| ID | Task | Status (PENDING/DONE) | Notes |`
- **Maintenance:** Update the file after every major tool call or state change.

## 3. Reasoning & Thinking Blocks
### 3.1 Thinking Mandate
All complex institutional or quantitative logic (SMC, FVG, Quant-Anomaly, ML Training) MUST be preceded by a `<thinking>` block.
- **Required Content:**
  - Mathematical assumptions (e.g., Lookback period = 756 bars).
  - Formula definitions.
  - Data source validation (e.g., DuckDB vs SQLite).

## 4. Communication Protocol (TRILOGY STYLE)
### 4.1 Surgical Tone
Eliminate all conversational filler and "AI-isms" (e.g., "I'll do that for you," "Certainly").
### 4.2 Standard Headers
- `### [ARCHITECT]` — System design and strategic planning.
- `### [ENGINEER]` — File modifications and tool execution.
- `### [AUDITOR]` — Validation, syntax checks, and health audits.

## 5. Implementation Strategy
### 5.1 Update GEMINI.md
Codify these standards into the main workspace instructions to ensure persistence across all future sessions.
