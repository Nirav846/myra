/**
 * Data-readiness types, mirroring `myra_app/data_readiness.py` (Phase 9).
 *
 * The central point of this contract: the *latest* stored date, the *expected*
 * trading date and *coverage* are three different facts. A recent MAX(date) is
 * not evidence of completeness, so they are never collapsed into one "ready"
 * boolean on the client — the backend decides the state and the UI renders it.
 */

/** Overall readiness verdict rendered as the control-room headline. */
export type ReadinessState =
  | 'ready'
  | 'waiting_for_eod'
  | 'partial'
  | 'stale'
  | 'failed'
  | 'blocked'
  | 'unknown';

/**
 * Phase 16: can MYRA produce a screener result from what is stored?
 *
 * Kept separate from `state` for the same reason `recovery` is: the readiness
 * state answers "do we have the data we should have?", and this answers "are
 * the *derived* artifacts the screening path reads present and usable?". Data
 * can be perfectly current while screening is impossible.
 */
export interface ReadinessScreening {
  /** Verdict reusing the ReadinessState vocabulary. Drives the escalation. */
  state: ReadinessState;
  /** Stable reason code, e.g. 'lake_empty', 'stale', 'coverage_partial'. */
  reason: string;
  /** Human-readable specifics for the control room. */
  detail: string | null;
  available: boolean;
  expected: number | null;
  coverage: number | null;
  lake: Record<string, unknown> | null;
}

export interface ReadinessTechnical {
  /** Latest market date represented in stored observations (ISO). */
  latest_date: string | null;
  /** Latest trading day whose EOD data should already be present (ISO). */
  expected_date: string | null;
  /** Most recent stored sessions, newest first. */
  sessions: string[];
  /** Symbols with an observation on each of `sessions`. */
  session_symbol_counts: Record<string, number>;
  error: string | null;
}

export interface ReadinessCoverage {
  symbols_at_expected: number | null;
  symbols_at_latest: number | null;
  /** Established universe the denominator is drawn from; null when unknown. */
  universe: number | null;
  /** True only when a denominator exists and the session is well covered. */
  expected_session_complete: boolean | null;
}

export interface ReadinessSource {
  available: boolean;
  ready: boolean | null;
  reason: string | null;
  last_date: string | null;
  complete: number | null;
  total_files: number | null;
  unstable_files?: number | null;
  delivery_pending?: string[];
}

export interface ReadinessFundamentals {
  /** Period the figures describe (NOT when we fetched them). */
  reporting_date: string | null;
  reporting_date_source: string | null;
  /** When *we* last retrieved the row. Never a publication date. */
  retrieval_date: string | null;
  symbols: number | null;
  with_core_metrics: number | null;
  error: string | null;
}

export interface ReadinessIngestion {
  last_success: string | null;
  last_attempt: string | null;
  last_status: string | null;
}

/**
 * Phase 10: why the automatic recovery watchdog last did (or did not) act.
 *
 * This is deliberately *not* folded into `state`. The readiness state answers
 * "do we have the data we should have?"; this answers "what did the watchdog
 * most recently decide, and why?". Conflating them would make a correctly
 * waiting-for-EOD system look like a failure.
 */
export interface ReadinessRecovery {
  /** Stable reason code, e.g. 'skipped_holiday', 'source_incomplete'. */
  reason: string | null;
  /** Human-readable specifics (which date, which source fact). */
  detail: string | null;
  /** When the watchdog last recorded this outcome (ISO, local). */
  at: string | null;
  /** Digest of the (date, source state) the decision was made against. */
  fingerprint: string | null;
}

/**
 * Phase 18: one *screening mode*'s availability.
 *
 * Phase 16 modelled screening as a single verdict — the indicator lake is usable
 * or it is not. That was one axis too few: MYRA has two independent modes, and a
 * single verdict let a working `TriggerScanner` hide behind a blocked lake while
 * letting a healthy scanner make the incomplete v2.5 ranking look operational.
 *
 * The vocabulary here is deliberately three-valued and separate from
 * `ReadinessState`, because "the data is current" and "this mode can run" are
 * different questions with different remedies.
 */
export type ScreeningModeState = 'operational' | 'degraded' | 'blocked' | 'unknown';

export interface ReadinessScreeningMode {
  mode: string;
  /** Human-facing mode name, e.g. "TriggerScanner (web/API)". */
  label: string;
  /** The score this mode emits. Not interchangeable with another mode's. */
  score: string;
  /** Explicit warning that this mode's score cannot be compared to another's. */
  comparability: string;
  /** What the mode reads, so the remedy is obvious. */
  reads: string;
  /** True when the mode depends on the derived indicator lake. */
  reads_lake: boolean;
  state: ScreeningModeState;
  /** Stable reason code, e.g. 'lake_empty', 'inputs_unsourced'. */
  reason: string;
  /** Human-readable specifics. */
  detail: string;
  /** Inputs the mode needs that are absent. Names, never fabricated values. */
  missing: string[];
  /** Runtime defects that block the mode regardless of the data. */
  blocked_by?: string[];
}

export interface ReadinessScreeningModes {
  /** One honest status over all modes; never stronger than the weakest mode. */
  overall: {
    state: ScreeningModeState;
    label: string;
    detail: string;
    operational_modes: string[];
    unavailable_modes: string[];
    blocked: string[];
  };
  modes: Record<string, ReadinessScreeningMode>;
  /** Every input the v2.5 scorer reads. */
  required_v25_inputs?: string[];
  /** The subset the lake producer actually emits — a strict subset by design. */
  lake_produced_v25_inputs?: string[];
}

export interface DataReadiness {
  state: ReadinessState;
  label: string;
  explanation: string;
  technical: ReadinessTechnical;
  coverage: ReadinessCoverage;
  source: ReadinessSource;
  fundamentals: ReadinessFundamentals;
  ingestion: ReadinessIngestion;
  recovery: ReadinessRecovery;
  screening: ReadinessScreening;
  /**
   * Optional: an older backend predates Phase 18 and omits this axis entirely.
   * The panel must then render nothing rather than infer a mode's health.
   */
  screening_modes?: ReadinessScreeningModes;
  publication_cutoff_ist: string;
  generated_at: string;
}

export interface ReadinessResponse {
  changed: boolean;
  fingerprint: string;
  readiness?: DataReadiness;
}
