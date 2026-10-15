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
  | 'unknown';

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
  publication_cutoff_ist: string;
  generated_at: string;
}

export interface ReadinessResponse {
  changed: boolean;
  fingerprint: string;
  readiness?: DataReadiness;
}
