/**
 * DataReadinessPanel — compact control-room summary of what MYRA actually has.
 *
 * Design rules this component follows (Phase 9):
 *  * It never derives its own verdict. `state` and `explanation` come from the
 *    backend, which is the only place that sees the calendar, the database and
 *    the EOD folder together.
 *  * The four facts are rendered as four separate rows — latest stored date,
 *    expected date, coverage, and the EOD source — so "recent date" is never
 *    mistaken for "complete data".
 *  * A missing fact renders "Not recorded"/"Unknown", never a guess.
 *  * Fundamental reporting period and retrieval time are labelled distinctly,
 *    because a value fetched today may describe last quarter.
 *  * It re-renders only when the verdict changes (see useDataReadiness).
 */
import {
  CalendarClock, Database, FileStack, CheckCircle2, AlertTriangle,
  Clock3, HelpCircle, RefreshCw,
} from 'lucide-react';
import type { ReadinessState } from '../lib/readiness';
import { useDataReadiness } from '../hooks/useDataReadiness';

const STATE_STYLE: Record<ReadinessState, { cls: string; dot: string; icon: typeof CheckCircle2 }> = {
  ready: { cls: 'text-green-300 border-green-500/40 bg-green-500/10', dot: 'bg-green-400', icon: CheckCircle2 },
  waiting_for_eod: { cls: 'text-blue-300 border-blue-500/40 bg-blue-500/10', dot: 'bg-blue-400', icon: Clock3 },
  partial: { cls: 'text-amber-300 border-amber-500/40 bg-amber-500/10', dot: 'bg-amber-400', icon: AlertTriangle },
  stale: { cls: 'text-amber-300 border-amber-500/40 bg-amber-500/10', dot: 'bg-amber-400', icon: Clock3 },
  failed: { cls: 'text-red-300 border-red-500/40 bg-red-500/10', dot: 'bg-red-400', icon: AlertTriangle },
  unknown: { cls: 'text-gray-300 border-white/15 bg-white/5', dot: 'bg-gray-400', icon: HelpCircle },
};

/**
 * Phase 10: plain-language labels for the recovery watchdog's reason codes.
 *
 * Keys must stay in sync with the `REASON_*` constants in
 * `myra_app/eod_recovery.py`.
 *
 * Most of these are *not* errors — "Skipped — exchange holiday" is the system
 * working correctly. Wording them this way stops a normal waiting state from
 * looking like a fault on a control-room screen.
 */
const RECOVERY_LABELS: Record<string, string> = {
  recovered: 'Recovered — data advanced to the expected session',
  partial_recovery: 'Partial — some files landed, more still expected',
  waiting_for_source: 'Waiting — source files still landing',
  no_new_data: 'No new data in the source yet',
  failed: 'Recovery attempt failed',
  skipped_already_running: 'Skipped — an ingest is already running',
  skipped_cooldown: 'Waiting — retry cooldown after an earlier attempt',
  skipped_retry_budget_exhausted: 'Paused — retry budget spent on this source state',
  skipped_official_holiday: 'Skipped — exchange holiday',
  skipped_calendar_unknown: 'Skipped — session could not be confirmed',
  skipped_already_current: 'Skipped — data already current',
  skipped_before_publication_window: 'Waiting — before the EOD publication cutoff',
};

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11px] font-mono text-[#777] shrink-0">{label}</span>
      <span className="text-[11px] font-mono text-[#ddd] text-right tabular-nums" title={hint}>
        {value}
      </span>
    </div>
  );
}

function unknown(v: string | number | null | undefined, fallback = 'Not recorded'): string {
  if (v === null || v === undefined || v === '') return fallback;
  return String(v);
}

export default function DataReadinessPanel({ busy }: { busy: boolean }) {
  const { readiness, loading, error, lastUpdated, refresh } = useDataReadiness(busy);

  if (loading && !readiness) {
    return (
      <section
        className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4"
        aria-label="Data readiness"
        aria-busy="true"
      >
        <h2 className="text-xs font-semibold uppercase tracking-wider font-mono text-[#888]">
          Data readiness
        </h2>
        <p className="text-xs font-mono text-[#666] mt-2">Checking stored data…</p>
      </section>
    );
  }

  if (!readiness) {
    return (
      <section
        className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4"
        aria-label="Data readiness"
      >
        <h2 className="text-xs font-semibold uppercase tracking-wider font-mono text-[#888]">
          Data readiness
        </h2>
        <p className="text-xs font-mono text-amber-300 mt-2">
          Readiness unavailable{error ? ` — ${error}` : ''}. The stored data was not assessed.
        </p>
      </section>
    );
  }

  const style = STATE_STYLE[readiness.state] ?? STATE_STYLE.unknown;
  const Icon = style.icon;
  const t = readiness.technical;
  const cov = readiness.coverage;
  const src = readiness.source;
  const fund = readiness.fundamentals;
  const rec = readiness.recovery;

  const coverageText =
    cov.symbols_at_latest !== null && cov.universe
      ? `${cov.symbols_at_latest} / ${cov.universe} symbols`
      : 'Unknown — no established universe';

  const coverageTitle =
    cov.expected_session_complete === true
      ? 'The expected session is represented for most tracked symbols.'
      : cov.expected_session_complete === false
        ? 'The expected session is only partially represented.'
        : 'Completeness could not be established (no reliable denominator).';

  return (
    <section
      className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-3"
      aria-label="Data readiness"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Database size={14} className="text-blue-400" aria-hidden="true" />
        <h2 className="text-xs font-semibold uppercase tracking-wider font-mono text-[#888]">
          Data readiness
        </h2>
        <span
          className={`flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded border ${style.cls}`}
          title={readiness.explanation}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
          <Icon size={11} aria-hidden="true" />
          {readiness.label}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={refresh}
          className="flex items-center gap-1 text-[11px] font-mono px-2 py-1 rounded border
                     border-[#ffffff1a] text-[#ccc] hover:text-white hover:bg-white/5 transition-colors"
          title="Re-check stored data"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          Re-check
        </button>
      </div>

      <p className="text-[11px] font-mono text-[#bbb]" data-testid="readiness-explanation">
        {readiness.explanation}
      </p>

      <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1">
        {/* Technical market data */}
        <div className="flex flex-col">
          <div className="flex items-center gap-1 mb-1">
            <CalendarClock size={11} className="text-[#666]" aria-hidden="true" />
            <span className="text-[10px] font-mono uppercase tracking-wider text-[#666]">
              Technical market data
            </span>
          </div>
          <Row label="Latest stored date" value={unknown(t.latest_date)} />
          <Row
            label="Expected trading date"
            value={unknown(t.expected_date, 'Unknown — calendar unavailable')}
            hint={`Derived from the trading calendar and the ${readiness.publication_cutoff_ist} IST publication cutoff.`}
          />
          <Row label="Session coverage" value={coverageText} hint={coverageTitle} />
        </div>

        {/* EOD ingestion */}
        <div className="flex flex-col">
          <div className="flex items-center gap-1 mb-1">
            <FileStack size={11} className="text-[#666]" aria-hidden="true" />
            <span className="text-[10px] font-mono uppercase tracking-wider text-[#666]">
              EOD ingestion
            </span>
          </div>
          <Row
            label="Source files"
            value={
              src.available
                ? src.complete !== null && src.total_files
                  ? `${src.complete} / ${src.total_files} at ${unknown(src.last_date)}`
                  : `Unassessed — ${unknown(src.reason, 'no measurement')}`
                : 'Source folder not found'
            }
            hint={src.reason ?? undefined}
          />
          <Row
            label="Last successful ingest"
            value={unknown(readiness.ingestion.last_success, 'Not recorded')}
            hint="Recorded by the ingest task when it actually inserted rows."
          />
          <Row
            label="Last attempt"
            value={
              readiness.ingestion.last_attempt
                ? `${readiness.ingestion.last_attempt.replace('T', ' ').slice(0, 19)}${
                    readiness.ingestion.last_status ? ` (${readiness.ingestion.last_status})` : ''
                  }`
                : 'Not recorded'
            }
          />
          <Row
            label="Auto-recovery"
            value={
              rec.reason ? RECOVERY_LABELS[rec.reason] ?? rec.reason : 'Not recorded'
            }
            hint={
              rec.at
                ? `${rec.detail ? `${rec.detail} — ` : ''}${rec.at.replace('T', ' ').slice(0, 19)}`
                : 'The watchdog decides this; a waiting state is normal and is not an error.'
            }
          />
        </div>

        {/* Fundamentals — reporting period vs retrieval, deliberately distinct */}
        <div className="flex flex-col sm:col-span-2">
          <div className="flex items-center gap-1 mb-1">
            <Database size={11} className="text-[#666]" aria-hidden="true" />
            <span className="text-[10px] font-mono uppercase tracking-wider text-[#666]">
              Fundamentals
            </span>
          </div>
          <div className="grid sm:grid-cols-2 gap-x-6">
            <Row
              label="Reporting period"
              value={unknown(fund.reporting_date, 'Not recorded')}
              hint={
                fund.reporting_date_source
                  ? `Period the figures describe, from ${fund.reporting_date_source}. This is NOT a retrieval time.`
                  : 'Period the figures describe. Not the same as when we fetched them.'
              }
            />
            <Row
              label="Last retrieved"
              value={unknown(fund.retrieval_date, 'Not recorded')}
              hint="When MYRA last fetched these rows. This is NOT a publication date."
            />
          </div>
        </div>
      </div>

      <p className="text-[10px] font-mono text-[#555]">
        {lastUpdated
          ? `Assessed ${new Date(lastUpdated).toLocaleTimeString()} · updates only when the verdict changes`
          : 'Not yet assessed'}
        {error ? ` · transport error: ${error}` : ''}
        {/*
          A displayed verdict is only as current as the last successful poll.
          When the transport is down the panel still shows that last verdict, so
          say plainly that it is no longer confirmed rather than letting a stale
          "ready" read as current.
        */}
        {error && readiness ? ' · shown verdict is not currently confirmed' : ''}
      </p>
    </section>
  );
}
