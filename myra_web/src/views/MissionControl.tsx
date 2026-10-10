/**
 * MissionControlView — MYRA Pipeline Control Room.
 *
 * Purpose change (not a feature deletion): this page used to be a market-
 * information dashboard (Morning Brief, Nifty Outlook, FII/Retail Divergence,
 * Stock Brief/Timeline, Market Breadth, PCR, Tactical Command Grid, watchlist).
 * Those widgets were removed *from this page only* — every scanner, analytical
 * page, API endpoint and navigation entry remains reachable elsewhere in the app.
 *
 * This page is now the operational overview and manual task-control surface:
 * what is happening right now, what completed, what failed and why, what is
 * next, and the controls to run one task or the whole pipeline.
 *
 * Execution state comes from the single shared hook usePipeline, which both this
 * page and Data Sync mount — there is exactly one execution path in the app.
 */
import { useEffect, useState } from 'react';
import {
  Activity, Play, Square, RefreshCw, Unplug, Server, CircleDot,
} from 'lucide-react';
import type { Librarian } from '../lib/Librarian';
import ErrorBoundary from '../components/ErrorBoundary';
import PipelineStatusPanel from '../components/PipelineStatusPanel';
import PipelineTaskList from '../components/PipelineTaskList';
import { usePipeline } from '../hooks/usePipeline';
import {
  describeEvent,
  formatClock,
  formatElapsed,
  runStatusVisual,
  taskLabel,
} from '../lib/pipeline';

// Keeps the props contract App.tsx already uses; the market widgets that needed
// `navigateTo` are gone from this page but the route signature is unchanged.
export default function MissionControlView({ lib }: { lib: Librarian; navigateTo: (id: string) => void }) {
  const { status, events, connected, loading, error, busy, startTask, cancel, refresh } = usePipeline();

  const [stopOnFail, setStopOnFail] = useState(true);
  const [elapsed, setElapsed] = useState(0);

  const overall = status?.overall;
  const isRunning = overall?.status === 'running';
  const isCancelling = overall?.status === 'cancelling';
  const activeTaskId = overall?.active_task_id ?? null;

  // Live elapsed clock — driven by the real start timestamp, never a fake timer.
  useEffect(() => {
    if (!isRunning || !overall?.started_at) {
      setElapsed(0);
      return;
    }
    const started = new Date(overall.started_at).getTime();
    if (isNaN(started)) { setElapsed(0); return; }
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [isRunning, overall?.started_at]);

  const visual = runStatusVisual(overall?.status ?? 'idle');

  return (
    <div className="flex flex-col gap-4 p-4 max-w-5xl w-full mx-auto">

      {/* ── 1. Header + overall status ─────────────────────────────────── */}
      <header className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Activity size={16} className="text-yellow-500" aria-hidden="true" />
          <h1 className="text-sm font-semibold uppercase tracking-wider font-mono text-[#fafafa]">
            Pipeline Control Room
          </h1>

          <span
            className={`flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded border ${visual.cls}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${visual.dot}`} aria-hidden="true" />
            {visual.label}
          </span>

          {/* Backend connection state */}
          <span
            className={`flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded border ${
              connected
                ? 'text-green-400 border-green-500/40 bg-green-500/10'
                : 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10'
            }`}
            title={connected ? 'Live event stream connected' : 'Event stream unavailable — polling for status'}
          >
            {connected ? <Server size={11} aria-hidden="true" /> : <Unplug size={11} aria-hidden="true" />}
            {connected ? 'Live' : 'Polling'}
          </span>

          <div className="flex-1" />

          <button
            type="button"
            onClick={refresh}
            className="flex items-center gap-1 text-[11px] font-mono px-2 py-1 rounded border
                       border-[#ffffff1a] text-[#ccc] hover:text-white hover:bg-white/5 transition-colors"
            title="Refresh pipeline status"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
            Refresh
          </button>
        </div>

        {/* Run All + stop-on-failure */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => startTask('all', stopOnFail)}
            disabled={busy}
            className="flex items-center gap-1.5 text-xs font-mono px-3 py-1.5 rounded
                       bg-yellow-600 hover:bg-yellow-500 text-black font-semibold
                       transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Play size={13} aria-hidden="true" />
            Run all 8 tasks
          </button>

          <label className="flex items-center gap-1.5 text-[11px] font-mono text-[#888] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={stopOnFail}
              onChange={(e) => setStopOnFail(e.target.checked)}
              className="accent-yellow-600"
            />
            Stop on failure
          </label>

          <div className="flex-1" />

          {(isRunning || isCancelling) && (
            <button
              type="button"
              onClick={cancel}
              disabled={isCancelling}
              className="flex items-center gap-1.5 text-xs font-mono px-3 py-1.5 rounded
                         border border-red-500/40 text-red-300 hover:bg-red-500/10
                         transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Square size={12} aria-hidden="true" />
              {isCancelling ? 'Cancelling…' : 'Cancel run'}
            </button>
          )}
        </div>

        {error && (
          <p className="text-[11px] font-mono text-red-300 bg-red-950/30 border border-red-500/30 rounded px-2 py-1">
            {error}
          </p>
        )}
      </header>

      {/* ── 2. Current activity ────────────────────────────────────────── */}
      <section
        className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-2"
        aria-label="Current activity"
      >
        <h2 className="text-xs font-semibold uppercase tracking-wider font-mono text-[#888]">
          Current activity
        </h2>

        {!status ? (
          <p className="text-xs font-mono text-[#888]">Connecting to the backend…</p>
        ) : isRunning || isCancelling ? (
          <>
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-lg font-mono text-[#fafafa] font-semibold">
                {activeTaskId ? taskLabel(activeTaskId) : 'Starting…'}
              </span>
              {overall?.started_at && (
                <span className="text-[11px] font-mono text-[#888]">
                  started {formatClock(overall.started_at)} · {formatElapsed(elapsed)} elapsed
                </span>
              )}
            </div>
            <p className="text-xs font-mono text-blue-300">
              {overall?.message || 'Working…'}
            </p>
            {overall?.progress_pct !== null && overall?.progress_pct !== undefined ? (
              <div className="h-1.5 w-full max-w-md rounded-full bg-white/5 overflow-hidden">
                <div className="h-full bg-blue-500 transition-[width] duration-300"
                     style={{ width: `${Math.min(100, overall.progress_pct)}%` }} />
              </div>
            ) : (
              <>
                <div className="h-1.5 w-full max-w-md rounded-full bg-white/5 overflow-hidden">
                  <div className="h-full w-1/3 bg-blue-400/70 animate-pulse rounded-full" />
                </div>
                <p className="text-[11px] font-mono text-[#666]">
                  Progress unknown — this task reports stages, not item counts
                </p>
              </>
            )}
            {isCancelling && (
              <p className="text-[11px] font-mono text-yellow-400">
                Cancellation requested. The run stays “cancelling” until the task actually stops —
                data may still be being written.
              </p>
            )}
          </>
        ) : (
          <div className="flex items-center gap-2">
            <CircleDot size={14} className="text-[#555]" aria-hidden="true" />
            <p className="text-xs font-mono text-[#888]">
              No task is running.
            </p>
          </div>
        )}

        {/* Retained final result of the most recent run */}
        {!busy && overall?.finished_at && (
          <p className="text-[11px] font-mono text-[#666]">
            Last run {visual.label.toLowerCase()} at {formatClock(overall.finished_at)}
          </p>
        )}
      </section>

      {/* ── 3. Pipeline timeline / task list ────────────────────────────── */}
      <section
        className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4"
        aria-label="Pipeline tasks"
      >
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider font-mono text-[#888]">
            Pipeline
          </h2>
          <span className="text-[10px] font-mono text-[#555]">
            {status?.order?.length ?? 0} tasks · run individually or as a sequence
          </span>
        </div>
        <PipelineTaskList
          status={status}
          busy={busy}
          onRunTask={(task) => startTask(task, true)}
          activeTaskId={activeTaskId}
        />
      </section>

      {/* ── 4. Execution history ────────────────────────────────────────── */}
      <section
        className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-2"
        aria-label="Execution history"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider font-mono text-[#888]">
            Execution history
          </h2>
          <span className="text-[10px] font-mono text-[#555]">most recent first</span>
        </div>

        {events.length === 0 ? (
          <p className="text-xs font-mono text-[#666] py-2">
            No run activity recorded yet. Start a task to populate this log.
          </p>
        ) : (
          <ul className="flex flex-col max-h-64 overflow-y-auto" role="list">
            {events.map((ev, i) => {
              const { text, cls } = describeEvent(ev);
              return (
                <li
                  key={`${ev.time}-${i}`}
                  className="flex items-baseline gap-2 text-[11px] font-mono py-0.5 border-b border-[#ffffff08] last:border-0"
                >
                  <span className="text-[#555] shrink-0 tabular-nums">{formatClock(ev.time)}</span>
                  <span className={`break-words ${cls}`}>{text}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* ── 5. Compact system / API health ──────────────────────────────── */}
      <ErrorBoundary>
        <PipelineStatusPanel />
      </ErrorBoundary>
    </div>
  );
}