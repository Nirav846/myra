/**
 * PipelineTaskList — the operational timeline for the MYRA pipeline.
 *
 * Shared by Mission Control and Data Sync so both agree on task status. Renders
 * one row per task in the canonical pipeline order, with truthful status,
 * stage, progress and per-task run/retry control.
 *
 * Progress honesty: a percentage is drawn ONLY when the backend reported a real
 * one. No task in this pipeline currently exposes processed/total counts, so the
 * running row shows an indeterminate bar plus the named stage — never a
 * fabricated or timer-driven percentage.
 */
import { Play, RotateCw, AlertTriangle, Lock } from 'lucide-react';
import {
  TASK_DEPENDENCIES,
  taskLabel,
  taskStatusVisual,
  timeAgo,
  formatElapsed,
} from '../lib/pipeline';
import type { PipelineStatus, TaskState } from '../lib/pipeline';

interface Props {
  status: PipelineStatus | null;
  busy: boolean;
  disabled?: boolean;
  onRunTask: (task: string) => void;
  /** Task key currently running, for sequence position + next-step copy. */
  activeTaskId?: string | null;
}

function ProgressBar({ task }: { task: TaskState }) {
  const running = task.current_status === 'running';
  const pct = task.progress_pct;

  if (pct !== null && pct !== undefined) {
    const width = Math.max(0, Math.min(100, pct));
    return (
      <div
        className="h-1.5 w-full rounded-full bg-white/5 overflow-hidden"
        role="progressbar"
        aria-valuenow={Math.round(width)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-300 ${
            task.current_status === 'completed' ? 'bg-green-500' : 'bg-blue-500'
          }`}
          style={{ width: `${width}%` }}
        />
      </div>
    );
  }

  if (running) {
    // Indeterminate: honest "we don't know the percentage" bar.
    return (
      <>
        <div
          className="h-1.5 w-full rounded-full bg-white/5 overflow-hidden"
          role="progressbar"
          aria-label="Progress unknown"
        >
          <div className="h-full w-1/3 rounded-full bg-blue-400/70 animate-pulse" />
        </div>
        <p className="text-[11px] font-mono text-[#666]">
          Progress unknown — this task reports stages, not item counts
        </p>
      </>
    );
  }

  return null;
}

export default function PipelineTaskList({
  status,
  busy,
  disabled,
  onRunTask,
  activeTaskId,
}: Props) {
  const order = status?.order?.length ? status.order : Object.keys(status?.tasks ?? {});
  const tasks = status?.tasks ?? {};
  const runType = status?.overall?.run_type;

  // Where we are in the current sequence, so we can name the next task.
  const activeIndex = activeTaskId ? order.indexOf(activeTaskId) : -1;

  if (!order.length) {
    return (
      <p className="text-xs font-mono text-[#888] py-6 text-center">
        No pipeline tasks reported. Is the backend running?
      </p>
    );
  }

  return (
    <ol className="flex flex-col" role="list" aria-label="Pipeline tasks">
      {order.map((key, index) => {
        const task = tasks[key];
        if (!task) return null;
        const visual = taskStatusVisual(task.current_status);
        const running = task.current_status === 'running';
        const deps = TASK_DEPENDENCIES[key] ?? [];
        const depUnmet = deps.some((d) => tasks[d]?.current_status !== 'completed');
        const isNext = busy && runType === 'all' && index === activeIndex + 1;

        return (
          <li
            key={key}
            className={`flex flex-col gap-1.5 py-2.5 border-b border-[#ffffff0a] last:border-0 ${
              running ? 'bg-blue-500/5 -mx-2 px-2 rounded' : ''
            }`}
          >
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-mono text-[#555] w-4 shrink-0">{index + 1}</span>
              <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${visual.dot}`} aria-hidden="true" />
              <span className="text-xs font-mono text-[#fafafa] font-semibold">
                {taskLabel(key)}
              </span>
              <span
                className={`text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border ${visual.cls}`}
              >
                {visual.label}
              </span>

              <div className="flex-1" />

              {isNext && (
                <span className="text-[10px] font-mono text-blue-300">next</span>
              )}

              <button
                type="button"
                onClick={() => onRunTask(key)}
                disabled={disabled || busy}
                title={
                  busy
                    ? 'A run is already in progress'
                    : task.current_status === 'failed' || task.current_status === 'timed_out'
                      ? `Retry ${taskLabel(key)}`
                      : depUnmet && deps.length
                        ? `Depends on ${deps.map(taskLabel).join(', ')} — that has not completed yet`
                        : `Run ${taskLabel(key)}`
                }
                className="flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded
                           border border-[#ffffff1a] text-[#ccc] hover:text-white hover:border-white/30
                           hover:bg-white/5 transition-colors disabled:opacity-30
                           disabled:cursor-not-allowed"
              >
                {task.current_status === 'failed' || task.current_status === 'timed_out' ? (
                  <RotateCw size={11} aria-hidden="true" />
                ) : (
                  <Play size={11} aria-hidden="true" />
                )}
                {task.current_status === 'failed' || task.current_status === 'timed_out'
                  ? 'Retry'
                  : 'Run'}
              </button>
            </div>

            <ProgressBar task={task} />

            {/* Current stage / message while running */}
            {running && task.stage && (
              <p className="text-[11px] font-mono text-blue-300 pl-6">
                {task.stage}
              </p>
            )}

            {/* Why this task cannot sensibly run yet */}
            {!running && deps.length > 0 && depUnmet && task.current_status !== 'running' && (
              <p className="text-[11px] font-mono text-[#666] pl-6 flex items-center gap-1">
                <Lock size={10} aria-hidden="true" />
                Requires {deps.map(taskLabel).join(', ')}
              </p>
            )}

            {/* Failure detail — always visible, never collapsed into a tooltip */}
            {task.error_message && (
              <p
                className="text-[11px] font-mono text-red-300 bg-red-950/30 border border-red-500/30
                           rounded px-2 py-1 pl-6 flex items-start gap-1"
              >
                <AlertTriangle size={11} className="mt-0.5 shrink-0" aria-hidden="true" />
                <span className="break-words">{task.error_message}</span>
              </p>
            )}

            {/* Last-run + duration */}
            {(task.last_run || task.duration_seconds !== null) && (
              <p className="text-[11px] font-mono text-[#666] pl-6">
                {task.last_run ? `last run ${timeAgo(task.last_run)}` : 'never run'}
                {task.duration_seconds !== null && task.duration_seconds !== undefined && (
                  <> · took {formatElapsed(task.duration_seconds)}</>
                )}
              </p>
            )}
          </li>
        );
      })}
    </ol>
  );
}