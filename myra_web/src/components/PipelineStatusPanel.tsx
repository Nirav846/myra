/**
 * PipelineStatusPanel — surfaces backend /api/health/doctor and /api/health/tasks
 * for the Mission Control page. Read-only. Failures to fetch degrade
 * silently (panel hidden, no crash) — uses useDataQuery + ErrorBoundary.
 *
 * Visual style: matches HealthStatusBar / MissionControl — Tailwind v4,
 * bg-[#1a1c24], border-[#ffffff1a], font-mono, text-xs/12px. Icons from
 * lucide-react. No new dependencies.
 */
import { useState, ReactElement } from 'react';
import { API_BASE } from '../config';
import { useDataQuery } from '../hooks/useDataQuery';
import {
  CheckCircle2, AlertTriangle, XCircle, RefreshCw, Clock, ChevronDown, ChevronUp,
} from 'lucide-react';

interface DoctorRun {
  id: number;
  when_utc: string;
  issues_found: number;
  issues_fixed: number;
  issues_failed: number;
  critical: string[];
}

interface SyncLogRow {
  task_name: string;
  last_run: string | null;
  last_status: string;
  error_message: string | null;
}

// Map internal snake_case task names to plain-English labels for the panel.
// Unknown keys fall through to a snake-case-to→→words transform so nothing
// reads as raw code.
const TASK_LABELS: Record<string, string> = {
  daily_ingest:        'Daily bhavcopy ingest',
  stale_catchup:       'Stale DB catch-up',
  db_backup:           'Nightly DB backup',
  etf_sync:            'ETF blocklist sync',
  index_sync:          'NIFTY index sync',
  fundamentals_sync:   'Weekly fundamentals sync',
  fundamentals_daily:  'Daily fundamentals refresh',
  shares_outstanding_sync: 'Shares outstanding refresh',
  institutional_sync:  'Institutional deals sync',
  fund_traction_sync:  'Fund traction sync',
  cross_buy_sync:      'Mutual-fund cross-buy sync',
  traction_sma_update: 'Traction SMA update',
  db_doctor:           'Database doctor',
};

function labelFor(taskName: string): string {
  if (TASK_LABELS[taskName]) return TASK_LABELS[taskName];
  // Fallback: snake_case → Title Case with spaces.
  return taskName.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Format a timestamp as "X minutes ago" / "X hours ago" / etc. Returns '—' for
// missing or unparseable. Exact timestamp goes in the title attribute for hover.
function timeAgo(iso: string | null | undefined): string {
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
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

// Strip "[WARNING] " / "[CRITICAL] " / "[ERROR] " prefix from a doctor message
// and return both the cleaned text and the severity bucket for icon/color.
function splitSeverity(line: string): { severity: 'critical' | 'warning' | 'error' | 'other'; text: string } {
  const m = line.match(/^\[(CRITICAL|WARNING|ERROR)\]\s*(.*)$/);
  if (!m) return { severity: 'other', text: line };
  const tag = m[1].toUpperCase();
  if (tag === 'CRITICAL') return { severity: 'critical', text: m[2] };
  if (tag === 'WARNING')  return { severity: 'warning',  text: m[2] };
  return { severity: 'error', text: m[2] };
}

function severityIcon(sev: 'critical' | 'warning' | 'error' | 'other') {
  if (sev === 'critical') return <XCircle size={12} className="text-red-400 shrink-0" aria-hidden="true" />;
  if (sev === 'warning')  return <AlertTriangle size={12} className="text-yellow-400 shrink-0" aria-hidden="true" />;
  if (sev === 'error')    return <XCircle size={12} className="text-red-400 shrink-0" aria-hidden="true" />;
  return <AlertTriangle size={12} className="text-[#888] shrink-0" aria-hidden="true" />;
}

function severityColor(sev: 'critical' | 'warning' | 'error' | 'other'): string {
  if (sev === 'critical') return 'text-red-400';
  if (sev === 'warning')  return 'text-yellow-400';
  if (sev === 'error')    return 'text-red-400';
  return 'text-[#888]';
}

// Translate the raw status code from sync_log into a sentence-style label.
function statusLabel(row: SyncLogRow): { text: string; color: string; icon: ReactElement } {
  const s = (row.last_status || '').toLowerCase();
  const name = labelFor(row.task_name);
  if (s === 'success') {
    return { text: `${name} ran successfully`, color: 'text-green-400',
      icon: <CheckCircle2 size={12} className="text-green-400 shrink-0" aria-hidden="true" /> };
  }
  if (s === 'failed') {
    const detail = row.error_message ? ` — ${row.error_message}` : '';
    return { text: `${name} failed${detail}`, color: 'text-red-400',
      icon: <XCircle size={12} className="text-red-400 shrink-0" aria-hidden="true" /> };
  }
  // 'unknown' or anything else — the sync_log default for tasks that haven't
  // run since the column was added. Not an error, just no information yet.
  return { text: `${name} — no status yet`, color: 'text-[#888]',
    icon: <Clock size={12} className="text-[#888] shrink-0" aria-hidden="true" /> };
}

export default function PipelineStatusPanel() {
  const doctorQ = useDataQuery<{ doctor: DoctorRun | null }>(
    'pipeline_status_doctor',
    async () => {
      const res = await fetch(`${API_BASE}/health/doctor`);
      if (!res.ok) throw new Error(`doctor HTTP ${res.status}`);
      return res.json();
    },
    { ttlMs: 30_000 },
  );

  const tasksQ = useDataQuery<{ tasks: SyncLogRow[] }>(
    'pipeline_status_tasks',
    async () => {
      const res = await fetch(`${API_BASE}/health/tasks`);
      if (!res.ok) throw new Error(`tasks HTTP ${res.status}`);
      return res.json();
    },
    { ttlMs: 30_000 },
  );

  // Collapsible — defaults to collapsed to keep Mission Control scannable.
  const [expanded, setExpanded] = useState<boolean>(false);

  // --- State derivation ---
  const doctor = doctorQ.data?.doctor ?? null;
  const tasks = tasksQ.data?.tasks ?? [];
  const failedCount = tasks.filter((t) => (t.last_status || '').toLowerCase() === 'failed').length;
  const doctorHasCritical = doctor
    ? (doctor.critical || []).some((c) => splitSeverity(c).severity === 'critical')
    : false;
  const hasIssues = failedCount > 0 || doctorHasCritical || (doctor && doctor.issues_found > 0);

  // Distinguish fetch/network failure from "server answered with null".
  // useDataQuery: on error → data=null, error="message"; on 200 with null →
  // data={doctor:null}, error=null.
  const doctorFetchFailed = !doctorQ.loading && !!(doctorQ.error) && doctorQ.data === null;
  const tasksFetchFailed  = !tasksQ.loading  && !!(tasksQ.error)  && tasksQ.data === null;
  const anyFetchFailed    = doctorFetchFailed || tasksFetchFailed;

  // While both queries are still loading, don't flash a skeleton.
  if (doctorQ.loading && tasksQ.loading) return null;

  // Header summary line: state at a glance.
  let headerText: string;
  let headerColor: string;
  let HeaderIcon: ReactElement;
  if (anyFetchFailed) {
    headerText = 'Health check unavailable';
    headerColor = 'text-[#888]';
    HeaderIcon = <AlertTriangle size={14} className="text-[#888]" aria-hidden="true" />;
  } else if (!doctor && tasksQ.data) {
    headerText = 'Health check hasn\u2019t run yet';
    headerColor = 'text-[#888]';
    HeaderIcon = <Clock size={14} className="text-[#888]" aria-hidden="true" />;
  } else if (doctor && doctor.issues_found === 0 && failedCount === 0) {
    headerText = 'Everything’s running normally';
    headerColor = 'text-green-400';
    HeaderIcon = <CheckCircle2 size={14} className="text-green-400" aria-hidden="true" />;
  } else if (failedCount > 0 || doctorHasCritical) {
    headerText = `${failedCount > 0 ? `${failedCount} task${failedCount === 1 ? '' : 's'} failed` : ''}${failedCount > 0 && doctorHasCritical ? ' · ' : ''}${doctorHasCritical ? 'doctor found critical issues' : ''}`;
    headerColor = 'text-red-400';
    HeaderIcon = <XCircle size={14} className="text-red-400" aria-hidden="true" />;
  } else {
    headerText = `${doctor.issues_found} minor warning${doctor.issues_found === 1 ? '' : 's'}`;
    headerColor = 'text-yellow-400';
    HeaderIcon = <AlertTriangle size={14} className="text-yellow-400" aria-hidden="true" />;
  }

  return (
    <section
      aria-label="Pipeline status"
      className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-3"
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center justify-between gap-2 w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 rounded"
        aria-expanded={expanded}
        aria-controls="pipeline-status-detail"
      >
        <div className="flex items-center gap-2">
          {HeaderIcon}
          <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] font-mono">
            Pipeline Status
          </h3>
          <span className={`text-xs font-mono ${headerColor}`}>
            — {headerText}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[12px] font-mono text-[#888]" title={doctor ? `Last health check: ${doctor.when_utc}` : ''}>
            {doctor ? <>checked {timeAgo(doctor.when_utc)}</> : null}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              doctorQ.refetch();
              tasksQ.refetch();
            }}
            className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 rounded p-0.5"
            title="Refresh pipeline status"
            aria-label="Refresh pipeline status"
          >
            <RefreshCw
              size={12}
              className={doctorQ.loading || tasksQ.loading ? 'animate-spin' : ''}
              aria-hidden="true"
            />
          </button>
          {expanded ? (
            <ChevronUp size={14} className="text-[#888]" aria-hidden="true" />
          ) : (
            <ChevronDown size={14} className="text-[#888]" aria-hidden="true" />
          )}
        </div>
      </button>

      {/* Auto-expand when there are problems so they're immediately visible. */}
      {hasIssues && !expanded && (
        <div className="sr-only" aria-live="polite">
          {failedCount} task failure{failedCount === 1 ? '' : 's'}.
          {doctorHasCritical ? ' Doctor reported critical issues.' : ''}
        </div>
      )}

      {(expanded || hasIssues) && (
        <div id="pipeline-status-detail" className="flex flex-col gap-3 mt-1">
          {/* Failed-task callouts at the top — the most actionable signal. */}
          {failedCount > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-[12px] font-mono uppercase tracking-wider text-[#888]">
                Failed tasks ({failedCount})
              </p>
              <ul className="flex flex-col gap-1" role="list" aria-label="Failed background tasks">
                {tasks
                  .filter((t) => (t.last_status || '').toLowerCase() === 'failed')
                  .map((t) => {
                    const label = statusLabel(t);
                    return (
                      <li
                        key={t.task_name}
                        className="flex items-start gap-2 text-xs font-mono bg-red-950/30 border border-red-500/30 rounded p-2"
                      >
                        {label.icon}
                        <span className={`flex-1 ${label.color}`} title={t.last_run || ''}>
                          {label.text}
                          <span className="text-[#888]"> · {timeAgo(t.last_run)}</span>
                        </span>
                      </li>
                    );
                  })}
              </ul>
            </div>
          )}

          {/* Doctor findings, if any. */}
          {doctor && doctor.critical && doctor.critical.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-[12px] font-mono uppercase tracking-wider text-[#888]">
                Doctor findings ({doctor.critical.length})
              </p>
              <ul className="flex flex-col gap-1" role="list" aria-label="Doctor findings">
                {doctor.critical.map((c, i) => {
                  const { severity, text } = splitSeverity(c);
                  return (
                    <li
                      key={i}
                      className={`flex items-start gap-2 text-xs font-mono border rounded p-2 ${
                        severity === 'critical'
                          ? 'bg-red-950/30 border-red-500/30'
                          : 'bg-yellow-950/20 border-yellow-500/20'
                      }`}
                    >
                      {severityIcon(severity)}
                      <span className={`flex-1 ${severityColor(severity)}`}>{text}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Full task list (collapsed by default if everything's OK). */}
          {tasks.length > 0 && (
            <details className="text-xs font-mono" open={hasIssues}>
              <summary className="cursor-pointer text-[12px] font-mono uppercase tracking-wider text-[#888] hover:text-[#ccc] transition-colors list-none outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 rounded">
                All tasks ({tasks.length})
              </summary>
              <ul className="flex flex-col gap-1 mt-1" role="list" aria-label="All background tasks">
                {tasks.map((t) => {
                  const label = statusLabel(t);
                  return (
                    <li key={t.task_name} className="flex items-start gap-2 text-xs font-mono py-1 border-b border-[#ffffff0a] last:border-0">
                      {label.icon}
                      <span className={`flex-1 ${label.color}`} title={t.last_run || ''}>
                        {label.text}
                      </span>
                      <span className="text-[#888] shrink-0">{timeAgo(t.last_run)}</span>
                    </li>
                  );
                })}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}