/**
 * Shared contract for the MYRA pipeline API (myra_web/routes/pipeline.py).
 *
 * Mission Control and Data Sync both consume this, so there is exactly one
 * description of the backend vocabulary and one place to format it.
 */

export type RunStatus =
  | 'idle' | 'running' | 'paused' | 'cancelling' | 'completed' | 'failed' | 'cancelled';

export type TaskStatus =
  | 'never' | 'queued' | 'running' | 'completed'
  | 'failed' | 'cancelled' | 'timed_out' | 'skipped' | 'no_new_data';

export interface OverallState {
  status: RunStatus;
  active_task_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  progress_pct: number | null;
  run_type: 'all' | 'single' | null;
  stop_on_fail: boolean;
  cancel_requested: boolean;
  paused: boolean;
  busy: boolean;
}

export interface TaskState {
  current_status: TaskStatus;
  stage: string | null;
  progress_pct: number | null;
  error_message: string | null;
  last_run: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface PipelineEvent {
  time: string;
  type: string;
  task?: string;
  task_id?: string;
  task_name?: string;
  message?: string;
  status?: string;
  error?: string | null;
  reason?: string;
  progress_pct?: number;
  duration_seconds?: number;
  state?: PipelineStatus;
}

export interface PipelineStatus {
  overall: OverallState;
  tasks: Record<string, TaskState>;
  order: string[];
  events: PipelineEvent[];
}

/** Canonical pipeline order, verified against myra_app.pipeline_control. */
export const PIPELINE_ORDER = [
  'daily_ingest',
  'enrichment',
  'etf_sync',
  'index_sync',
  'fundamentals_sync',
  'market_cap_sync',
  'shares_outstanding_sync',
  'institutional_sync',
  'fundamentals_enrich',
] as const;

export const TASK_LABELS: Record<string, string> = {
  daily_ingest: 'Daily Ingest',
  enrichment: 'Feature Enrichment',
  etf_sync: 'ETF Sync',
  index_sync: 'Index Sync',
  fundamentals_sync: 'Fundamentals Sync',
  market_cap_sync: 'Market Cap Sync',
  shares_outstanding_sync: 'Shares Refresh',
  institutional_sync: 'Institutional Sync',
  fundamentals_enrich: 'Fundamentals Enrich',
};

/** Tasks that gate the ones after them when running the full sequence. */
export const TASK_DEPENDENCIES: Record<string, string[]> = {
  enrichment: ['daily_ingest'],
};

export function taskLabel(key: string): string {
  return TASK_LABELS[key] ?? key.replace(/_/g, ' ');
}

export interface StatusVisual {
  label: string;
  cls: string;
  dot: string;
}

/** One vocabulary, one rendering. Every terminal state stays distinguishable. */
export function taskStatusVisual(status: TaskStatus | string): StatusVisual {
  switch (status) {
    case 'completed':
      return { label: 'Completed', cls: 'text-green-400 border-green-500/40 bg-green-500/10', dot: 'bg-green-400' };
    case 'running':
      return { label: 'Running', cls: 'text-blue-300 border-blue-500/40 bg-blue-500/10', dot: 'bg-blue-400 animate-pulse' };
    case 'paused':
      return { label: 'Paused', cls: 'text-amber-300 border-amber-500/40 bg-amber-500/10', dot: 'bg-amber-300' };
    case 'queued':
      return { label: 'Queued', cls: 'text-[#888] border-[#ffffff1a] bg-white/5', dot: 'bg-[#888]' };
    case 'failed':
      return { label: 'Failed', cls: 'text-red-400 border-red-500/40 bg-red-500/10', dot: 'bg-red-400' };
    case 'cancelled':
      return { label: 'Cancelled', cls: 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10', dot: 'bg-yellow-400' };
    case 'timed_out':
      return { label: 'Timed out', cls: 'text-orange-400 border-orange-500/40 bg-orange-500/10', dot: 'bg-orange-400' };
    case 'skipped':
      return { label: 'Skipped', cls: 'text-[#888] border-[#ffffff1a] bg-white/5', dot: 'bg-[#555]' };
    case 'no_new_data':
      // Attempt ran but ingested nothing — freshness NOT established.
      return { label: 'No new data', cls: 'text-yellow-300 border-yellow-500/40 bg-yellow-500/10', dot: 'bg-yellow-300' };
    case 'never':
    default:
      return { label: 'Never run', cls: 'text-[#888] border-[#ffffff1a] bg-white/5', dot: 'bg-[#555]' };
  }
}

export function runStatusVisual(status: RunStatus | string): StatusVisual {
  switch (status) {
    case 'running':
      return { label: 'Running', cls: 'text-blue-300 border-blue-500/40 bg-blue-500/10', dot: 'bg-blue-400 animate-pulse' };
    case 'cancelling':
      return { label: 'Cancelling', cls: 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10', dot: 'bg-yellow-400 animate-pulse' };
    case 'completed':
      return { label: 'Completed', cls: 'text-green-400 border-green-500/40 bg-green-500/10', dot: 'bg-green-400' };
    case 'failed':
      return { label: 'Failed', cls: 'text-red-400 border-red-500/40 bg-red-500/10', dot: 'bg-red-400' };
    case 'cancelled':
      return { label: 'Cancelled', cls: 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10', dot: 'bg-yellow-400' };
    default:
      return { label: 'Idle', cls: 'text-[#888] border-[#ffffff1a] bg-white/5', dot: 'bg-[#555]' };
  }
}

export function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0s';
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const diff = Date.now() - d.getTime();
  if (diff < 0) return 'just now';
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-GB', { hour12: false });
}

/** Plain-English sentence for an event row in the activity log. */
export function describeEvent(ev: PipelineEvent): { text: string; cls: string } {
  const name = ev.task_id ? taskLabel(ev.task_id) : '';
  switch (ev.type) {
    case 'run_started':
      return {
        text: ev.task === 'all' ? 'Full pipeline started' : `${taskLabel(ev.task || '')} started`,
        cls: 'text-blue-300',
      };
    case 'task_started':
      return { text: `${name} started`, cls: 'text-blue-300' };
    case 'stage':
      return { text: `${name}: ${ev.message ?? ''}`, cls: 'text-[#ccc]' };
    case 'note':
      return { text: `${name}: ${ev.message ?? ''}`, cls: 'text-[#888]' };
    case 'progress':
      return { text: `${name}: ${Math.round(ev.progress_pct ?? 0)}%`, cls: 'text-[#ccc]' };
    case 'task_completed': {
      if (ev.status === 'completed')
        return { text: `${name} completed${ev.duration_seconds ? ` in ${formatElapsed(ev.duration_seconds)}` : ''}`, cls: 'text-green-400' };
      if (ev.status === 'failed')
        return { text: `${name} failed — ${ev.error ?? 'unknown error'}`, cls: 'text-red-400' };
      if (ev.status === 'timed_out')
        return { text: `${name} timed out — ${ev.error ?? ''}`, cls: 'text-orange-400' };
      if (ev.status === 'cancelled')
        return { text: `${name} cancelled`, cls: 'text-yellow-400' };
      return { text: `${name} ${ev.status}`, cls: 'text-[#ccc]' };
    }
    case 'run_finished':
      return { text: `Pipeline ${ev.status ?? 'finished'}`, cls: ev.status === 'completed' ? 'text-green-400' : 'text-red-400' };
    case 'all_stopped':
      return { text: `Stopped at ${taskLabel(ev.task || '')} — ${ev.reason ?? ''}`, cls: 'text-red-400' };
    case 'tasks_skipped':
      return { text: `Remaining tasks ${ev.reason === 'cancelled' ? 'cancelled' : 'skipped'}`, cls: 'text-[#888]' };
    case 'cancellation_requested':
      return { text: 'Cancellation requested — waiting for the task to stop', cls: 'text-yellow-400' };
    case 'run_paused':
      return { text: 'Pipeline paused', cls: 'text-amber-300' };
    case 'run_resumed':
      return { text: 'Pipeline resumed', cls: 'text-blue-300' };
    case 'schedule_updated':
      return { text: 'Schedule configuration updated', cls: 'text-indigo-300' };
    default:
      return { text: ev.type, cls: 'text-[#888]' };
  }
}
