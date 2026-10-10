import { useState, useEffect, useRef, useCallback, ReactNode } from 'react';
import {
  RefreshCw, Play, CheckCircle, XCircle, Clock, AlertTriangle,
  Database, HardDrive, Key, Server, StopCircle, X, ChevronRight, CalendarClock,
  Download, Dna, List, BarChart3, TrendingUp, Coins, Building2
} from 'lucide-react';
import { useHealthStatus } from '../hooks/useHealthStatus';
// Single execution state path — shared with Mission Control. Data Sync keeps the
// database diagnostics + scheduling config; it does NOT own its own SSE/queue.
import { usePipeline } from '../hooks/usePipeline';
import { describeEvent } from '../lib/pipeline';
import type { PipelineStatus } from '../lib/pipeline';
import { API_BASE } from '../config';

interface DBHealthStatus {
  connected: boolean;
  error?: string;
  count?: number;
}

interface CheckItem {
  exists: boolean;
  reachable?: boolean;
  path?: string;
}

interface PipelineChecks {
  [key: string]: CheckItem | Record<string, CheckItem>;
}

const TASK_META: Record<string, { name: string; key: string; duration: string; icon: ReactNode; color: string }> = {
  daily_ingest: { name: 'Daily Ingest', key: 'daily_ingest', duration: '2-5 min', icon: <Download size={16} />, color: 'cyan' },
  enrichment: { name: 'Feature Enrichment', key: 'enrichment', duration: '5-10 min', icon: <Dna size={16} />, color: 'fuchsia' },
  etf_sync: { name: 'ETF Sync', key: 'etf_sync', duration: '1-2 min', icon: <List size={16} />, color: 'green' },
  index_sync: { name: 'Index Sync', key: 'index_sync', duration: '1-3 min', icon: <BarChart3 size={16} />, color: 'yellow' },
  fundamentals_sync: { name: 'Fundamentals Sync', key: 'fundamentals_sync', duration: '10-20 min', icon: <TrendingUp size={16} />, color: 'blue' },
  market_cap_sync: { name: 'Market Cap Sync', key: 'market_cap_sync', duration: '3-5 min', icon: <Coins size={16} />, color: 'purple' },
  shares_outstanding_sync: { name: 'Shares Refresh', key: 'shares_outstanding_sync', duration: '1-3 min', icon: <BarChart3 size={16} />, color: 'cyan' },
  institutional_sync: { name: 'Institutional Sync', key: 'institutional_sync', duration: '1-3 min', icon: <Building2 size={16} />, color: 'amber' },
};

const TASK_DEPS: Record<string, string[]> = {
  enrichment: ['daily_ingest'],
};

const ORDER = ['daily_ingest', 'enrichment', 'etf_sync', 'index_sync', 'fundamentals_sync', 'market_cap_sync', 'shares_outstanding_sync', 'institutional_sync'];

function relativeTime(dateStr: string | null): string {
  if (!dateStr || dateStr === 'Never') return 'Never';
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    if (diffMs < 0) return 'Just now';
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    return dateStr.slice(0, 10);
  } catch {
    return dateStr || 'Never';
  }
}

function statusColor(status: string): string {
  switch (status) {
    case 'completed': return 'text-green-400';
    case 'running': return 'text-cyan-400';
    case 'failed': return 'text-red-400';
    case 'timed_out': return 'text-orange-400';
    case 'cancelled': return 'text-yellow-400';
    case 'queued': return 'text-[#888]';
    case 'skipped': return 'text-[#666]';
    case 'never': return 'text-[#666]';
    default: return 'text-[#888]';
  }
}

function statusBg(status: string): string {
  switch (status) {
    case 'completed': return 'bg-green-500/20 border-green-500/30';
    case 'running': return 'bg-cyan-500/20 border-cyan-500/30';
    case 'failed': return 'bg-red-500/20 border-red-500/30';
    case 'timed_out': return 'bg-orange-500/20 border-orange-500/30';
    case 'cancelled': return 'bg-yellow-500/20 border-yellow-500/30';
    case 'queued': return 'bg-[#ffffff0a] border-[#ffffff1a]';
    case 'skipped': return 'bg-[#ffffff0a] border-[#ffffff1a]';
    case 'never': return 'bg-[#ffffff0a] border-[#ffffff1a]';
    default: return 'bg-[#ffffff0a] border-[#ffffff1a]';
  }
}

function statusIcon(status: string) {
  switch (status) {
    case 'completed': return <CheckCircle size={14} className="text-green-400" />;
    case 'running': return <RefreshCw size={14} className="text-cyan-400 animate-spin" />;
    case 'failed': return <XCircle size={14} className="text-red-400" />;
    case 'timed_out': return <AlertTriangle size={14} className="text-orange-400" />;
    case 'cancelled': return <StopCircle size={14} className="text-yellow-400" />;
    case 'queued': return <Clock size={14} className="text-[#888]" />;
    case 'never': return <Clock size={14} className="text-[#555]" />;
    default: return <Clock size={14} className="text-[#888]" />;
  }
}

export default function DataSyncView() {
  const { health, coverage } = useHealthStatus();
  // Execution + live status come from the shared hook. This page deliberately
  // owns no second SSE connection and no second run queue.
  const {
    status, events: sseEvents, connected, error, busy,
    startTask, cancel, refresh: refreshPipeline,
  } = usePipeline();
  const [checks, setChecks] = useState<PipelineChecks | null>(null);
  const [checksCollapsed, setChecksCollapsed] = useState(() => localStorage.getItem('datasync_checks_collapsed') === 'true');
  const [dbHealthCollapsed, setDbHealthCollapsed] = useState(() => localStorage.getItem('datasync_dbhealth_collapsed') === 'true');
  const [stopOnFail, setStopOnFail] = useState(true);
  const [scheduleConfig, setScheduleConfig] = useState<Record<string, any>>({});
  const [schedulePaused, setSchedulePaused] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const fetchChecks = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/pipeline/check`);
      if (!mountedRef.current) return;
      if (res.ok) setChecks(await res.json());
    } catch { /* ignore */ }
  }, []);

  const fetchScheduleConfig = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/pipeline/schedule`);
      if (!mountedRef.current) return;
      if (res.ok) setScheduleConfig(await res.json());
    } catch { /* ignore */ }
  }, []);

  const fetchSchedulePaused = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/pipeline/schedule/paused`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data = await res.json();
        setSchedulePaused(data.paused);
      }
    } catch { /* ignore */ }
  }, []);

  const toggleSchedulePause = async () => {
    try {
      const res = await fetch(`${API_BASE}/pipeline/schedule/pause`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setSchedulePaused(data.paused);
      }
    } catch { /* ignore */ }
  };

  // Live status and execution now live in usePipeline, shared with Mission
  // Control. This page keeps only the database diagnostics and the scheduling
  // configuration, so there is exactly one SSE connection and one run queue.

  // Initial load of the concerns this page owns
  useEffect(() => {
    fetchChecks();
    fetchScheduleConfig();
    fetchSchedulePaused();
  }, [fetchChecks, fetchScheduleConfig, fetchSchedulePaused]);

  const triggerRun = (task: string) => { setLocalError(null); return startTask(task, stopOnFail); };
  const cancelRun = cancel;

  // Force reset is refused by the backend (409) while a task is genuinely
  // executing — it must never mark a live operation as idle.
  const forceReset = async () => {
    try {
      const res = await fetch(`${API_BASE}/pipeline/force-reset`, { method: 'POST' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setLocalError(body.detail ?? 'Force reset refused');
      } else {
        setLocalError(null);
      }
      await refreshPipeline();
    } catch { /* ignore */ }
  };

  const toggleSchedule = async (taskKey: string, enabled: boolean) => {
    try {
      const res = await fetch(`${API_BASE}/pipeline/toggle-schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_key: taskKey, enabled }),
      });
      if (res.ok) {
        const data = await res.json();
        setScheduleConfig(data.config || {});
      }
    } catch { /* ignore */ }
  };

  const isRunning = status?.overall?.status === 'running' || status?.overall?.status === 'cancelling';
  const activeTaskId = status?.overall?.active_task_id ?? null;
  const shownError = error || localError;

  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!isRunning || !status?.overall?.started_at) {
      setElapsed(0);
      return;
    }
    const started = new Date(status.overall.started_at).getTime();
    const tick = () => setElapsed(Math.floor((Date.now() - started) / 1000));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [isRunning, status?.overall?.started_at]);

  const formatElapsed = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}m ${s}s`;
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Data Pipeline</h2>
          <p className="text-[12px] text-[#888] font-mono mt-0.5">
            {isRunning
              ? `Running: ${TASK_META[activeTaskId || '']?.name || activeTaskId || '...'}`
              : status?.overall?.message || 'Idle'}
            {!connected && <span className="ml-2 text-yellow-400">(polling mode)</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-[12px] text-[#888] font-mono cursor-pointer select-none">
            <input
              type="checkbox"
              checked={stopOnFail}
              onChange={(e) => setStopOnFail(e.target.checked)}
              className="accent-cyan-500 w-3 h-3"
            />
            Stop on fail
          </label>
          <button
            onClick={toggleSchedulePause}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[12px] font-mono border transition-colors ${
              schedulePaused
                ? 'bg-red-500/20 border-red-500/30 text-red-400'
                : 'bg-green-500/20 border-green-500/30 text-green-400'
            }`}
          >
            Auto-Sync: {schedulePaused ? 'OFF' : 'ON'}
          </button>
          {isRunning ? (
            <button
              onClick={cancelRun}
              disabled={status?.overall?.status === 'cancelling'}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500/20 border border-red-500/30 rounded text-[12px] text-red-400 font-mono hover:bg-red-500/30 transition-colors disabled:opacity-40"
              title="Requests cancellation. The run stays 'cancelling' until the task actually stops."
            >
              <StopCircle size={14} />
              {status?.overall?.status === 'cancelling' ? 'Cancelling...' : 'Cancel'}
            </button>
          ) : null}
          <button
            onClick={forceReset}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-[12px] text-[#888] font-mono hover:bg-[#ffffff15] hover:text-white transition-colors"
            title="Force-reset pipeline to idle (use if stuck)"
          >
            <RefreshCw size={14} />
            Force Reset
          </button>
          {!isRunning ? (
            <button
              onClick={() => triggerRun('all')}
              disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-cyan-500/20 border border-cyan-500/30 rounded text-[12px] text-cyan-400 font-mono hover:bg-cyan-500/30 transition-colors disabled:opacity-40"
            >
              <Play size={14} className={busy ? 'animate-pulse' : ''} />
              Sync All
            </button>
          ) : null}
          <button
            onClick={() => { refreshPipeline(); fetchChecks(); }}
            className="p-1.5 text-[#888] hover:text-white transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {shownError && (
        <div className="bg-red-950/40 border border-red-500/50 px-4 py-2 rounded-lg flex items-center gap-2 text-xs text-red-400 font-mono relative pr-8">
          <AlertTriangle size={14} />
          <span className="flex-1">{shownError}</span>
          <button
            onClick={() => setLocalError(null)}
            className="absolute top-1 right-1 p-1 text-red-400 hover:text-white transition-colors"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Run Progress — truthful: a real percentage when the backend reported
          one, otherwise an explicitly indeterminate bar. */}
      {isRunning && (
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg p-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[12px] font-mono text-cyan-400">
              {status?.overall?.message || 'Running...'}
            </span>
            <span className="text-[12px] font-mono text-[#888] flex items-center gap-2">
              {elapsed > 0 && <span>Running for {formatElapsed(elapsed)}</span>}
              {status?.overall?.progress_pct !== null &&
                status?.overall?.progress_pct !== undefined && (
                  <span>{Math.round(status.overall.progress_pct)}%</span>
                )}
            </span>
          </div>
          <div className="w-full h-1.5 bg-[#333] rounded overflow-hidden">
            {status?.overall?.progress_pct !== null &&
            status?.overall?.progress_pct !== undefined ? (
              <div
                className="h-full bg-cyan-400 rounded transition-all duration-500"
                style={{ width: `${Math.min(100, status.overall.progress_pct)}%` }}
              />
            ) : (
              <div className="h-full w-1/3 bg-cyan-400 rounded animate-pulse" />
            )}
          </div>
          {status?.overall?.progress_pct === null ||
          status?.overall?.progress_pct === undefined ? (
            <p className="text-[11px] font-mono text-[#666] mt-1">
              Progress unknown — this task reports stages, not item counts
            </p>
          ) : null}
        </div>
      )}

      {/* Task Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {ORDER.map((taskKey) => {
          const meta = TASK_META[taskKey];
          const info = status?.tasks[taskKey];
          const displayStatus = info?.current_status ?? 'never';
          const isThisRunning = status?.overall?.status === 'running' && activeTaskId === taskKey;
          const isFailedOrCrashed = ['failed', 'timed_out'].includes(displayStatus);

          return (
            <div
              key={taskKey}
              className={`bg-[#1a1c24] border rounded-lg p-4 flex flex-col gap-2 transition-all ${
                isThisRunning ? 'border-cyan-500/40 shadow-[0_0_15px_rgba(6,182,212,0.1)]' : 'border-[#ffffff1a]'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-lg leading-none">{meta.icon}</span>
                  <span className="text-sm font-semibold text-[#fafafa]">{meta.name}</span>
                </div>
                <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-[12px] font-mono border ${statusBg(displayStatus)}`}>
                  {statusIcon(displayStatus)}
                  <span className={statusColor(displayStatus)}>
                    {displayStatus === 'never' ? 'Never run' : isThisRunning ? 'Running' : displayStatus}
                  </span>
                </div>
              </div>
              {TASK_DEPS[taskKey] && (
                <div className="text-[12px] font-mono text-[#888] flex items-center gap-1 mt-0.5">
                  <span>requires:</span>
                  {TASK_DEPS[taskKey].map(dep => {
                    const depInfo = status?.tasks[dep];
                    const depRanToday = depInfo?.last_run
                      ? new Date(depInfo.last_run).toDateString() === new Date().toDateString()
                      : false;
                    return (
                      <span
                        key={dep}
                        className={depRanToday ? 'text-green-500' : 'text-yellow-600'}
                        title={depRanToday ? `${dep} ran today` : `${dep} has not run today — enrichment may produce stale scores`}
                      >
                        {dep} {depRanToday ? '✓' : '⚠'}
                      </span>
                    );
                  })}
                </div>
              )}

              <div className="flex items-center justify-between text-[12px] font-mono text-[#888]">
                <div className="flex items-center gap-1">
                  <Clock size={11} />
                  <span>Last: {relativeTime(info?.last_run)}</span>
                </div>
                <span className="text-[#888]">~{meta.duration}</span>
              </div>

              {isThisRunning && (
                info?.progress_pct !== null && info?.progress_pct !== undefined ? (
                  <div className="w-full h-1 bg-[#333] rounded overflow-hidden">
                    <div
                      className="h-full bg-cyan-400 rounded transition-all duration-300"
                      style={{ width: `${Math.min(100, info.progress_pct)}%` }}
                    />
                  </div>
                ) : (
                  <>
                    <div className="w-full h-1 bg-[#333] rounded overflow-hidden">
                      <div className="h-full w-1/3 bg-cyan-400 rounded animate-pulse" />
                    </div>
                    <p className="text-[11px] font-mono text-[#666]">
                      {info?.stage || 'Working — progress unknown'}
                    </p>
                  </>
                )
              )}

              {isFailedOrCrashed && info?.error_message && (
                <div className="bg-red-950/30 border border-red-500/20 rounded px-2 py-1 text-[12px] font-mono text-red-400 truncate" title={info.error_message}>
                  <AlertTriangle size={10} className="inline mr-1" />
                  {info.error_message}
                </div>
              )}

              <div className="flex items-center justify-between mt-1 pt-2 border-t border-[#ffffff0a]">
                <label className="flex items-center gap-1.5 text-[12px] text-[#888] font-mono cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={scheduleConfig[taskKey]?.enabled || false}
                    onChange={(e) => toggleSchedule(taskKey, e.target.checked)}
                    className="accent-cyan-500 w-3 h-3"
                  />
                  <CalendarClock size={11} />
                  Daily 18:00
                </label>
                {isFailedOrCrashed && !isThisRunning ? (
                  <button
                    onClick={() => triggerRun(taskKey)}
                    disabled={busy}
                    className="flex items-center gap-1 px-2.5 py-1 bg-orange-500/20 border border-orange-500/30 rounded text-[12px] font-mono text-orange-400 hover:bg-orange-500/30 transition-colors disabled:opacity-40"
                  >
                    <RefreshCw size={11} />
                    Retry
                    <ChevronRight size={11} />
                  </button>
                ) : (
                  <button
onClick={() => triggerRun(taskKey)}
                  disabled={busy}
                  className="flex items-center gap-1 px-2.5 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-[12px] font-mono text-[#ccc] hover:bg-[#ffffff15] hover:text-white transition-colors disabled:opacity-40"
                  >
                    {isThisRunning ? 'Running...' : 'Sync Now'}
                    <ChevronRight size={11} />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Pre-flight Checks */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg p-4">
        <div
          className="flex items-center gap-2 mb-3 cursor-pointer select-none"
          onClick={() => { const n = !checksCollapsed; setChecksCollapsed(n); localStorage.setItem('datasync_checks_collapsed', String(n)); }}
        >
          <Server size={14} />
          <h3 className="text-xs font-semibold text-[#888] uppercase tracking-wider flex-1">Pre-flight Checks</h3>
          <button
            onClick={(e) => { e.stopPropagation(); fetchChecks(); }}
            className="p-1 text-[#888] hover:text-white transition-colors"
            title="Refresh checks"
          >
            <RefreshCw size={12} />
          </button>
          <ChevronRight size={12} className={`transition-transform ${checksCollapsed ? '' : 'rotate-90'}`} />
        </div>
        {!checksCollapsed && (
        <>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {checks && typeof checks === 'object' && Object.entries(checks).map(([key, val]) => {
            if (key === 'databases') return null;
            const item = val as CheckItem;
            return (
              <div key={key} className="flex items-center gap-2 text-[12px] font-mono">
                {item.exists ? (
                  <CheckCircle size={12} className="text-green-400 shrink-0" />
                ) : (
                  <XCircle size={12} className="text-red-400 shrink-0" />
                )}
                <span className="text-[#aaa] truncate">{key}</span>
              </div>
            );
          })}
        </div>

        {checks && (checks as any).databases && (
          <>
            <div className="text-[12px] text-[#888] font-mono mt-3 mb-2 flex items-center gap-1">
              <Database size={11} />
              Databases
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {Object.entries((checks as any).databases).map(([key, val]) => {
                const item = val as CheckItem;
                return (
                  <div key={key} className="flex items-center gap-1.5 text-[12px] font-mono">
                    {item.reachable ? (
                      <CheckCircle size={10} className="text-green-400 shrink-0" />
                    ) : (
                      <XCircle size={10} className="text-red-400 shrink-0" />
                    )}
                    <span className="text-[#888] truncate" title={key}>{key}</span>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {checks && (checks as any).api_keys && (
          <>
            <div className="text-[12px] text-[#888] font-mono mt-3 mb-2 flex items-center gap-1">
              <Key size={11} />
              API Keys
            </div>
            <div className="flex gap-3">
              {Object.entries((checks as any).api_keys).map(([key, val]) => (
                <div key={key} className="flex items-center gap-1.5 text-[12px] font-mono">
                  {val ? (
                    <CheckCircle size={10} className="text-green-400 shrink-0" />
                  ) : (
                    <AlertTriangle size={10} className="text-yellow-400 shrink-0" />
                  )}
                  <span className="text-[#888]">{key}</span>
                </div>
              ))}
            </div>
          </>
        )}
        </>
        )}
      </div>

      {/* Database Health */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg p-4">
        <div
          className="flex items-center gap-2 mb-3 cursor-pointer select-none"
          onClick={() => { const n = !dbHealthCollapsed; setDbHealthCollapsed(n); localStorage.setItem('datasync_dbhealth_collapsed', String(n)); }}
        >
          <Database size={14} />
          <h3 className="text-xs font-semibold text-[#888] uppercase tracking-wider flex-1">Database Health</h3>
          <ChevronRight size={12} className={`transition-transform ${dbHealthCollapsed ? '' : 'rotate-90'}`} />
        </div>
        {!dbHealthCollapsed && (
        <>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(health as Record<string, DBHealthStatus>).map(([dbName, status]) => (
            <div key={dbName} className="flex items-center justify-between text-[12px] font-mono px-2 py-1.5 bg-[#0e1117] rounded border border-[#ffffff0a]">
              <span className="text-[#888] uppercase tracking-wider">{dbName}</span>
              <span className="flex items-center gap-1.5 shrink-0">
                <span className={status.connected ? "text-green-400" : "text-red-400"}>
                  {status.connected ? "CONN" : "DISC"}
                </span>
                <div className={`w-1.5 h-1.5 rounded-full ${status.connected ? 'bg-green-400' : 'bg-red-400'} animate-pulse`} />
              </span>
            </div>
          ))}
        </div>
        {coverage?.total_symbols > 0 && (
          <>
            <div className="text-[12px] text-[#888] font-mono mt-4 mb-2 flex items-center gap-1">
              <HardDrive size={11} />
              Fundamentals Coverage
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              <div className="flex justify-between text-[12px] font-mono px-2 py-1 bg-[#0e1117] rounded border border-[#ffffff0a]">
                <span className="text-[#888]">shares_outstanding</span>
                <span className={coverage.shares_outstanding > 0 ? 'text-green-400' : 'text-yellow-400'}>{coverage.shares_outstanding}/{coverage.total_symbols}</span>
              </div>
              <div className="flex justify-between text-[12px] font-mono px-2 py-1 bg-[#0e1117] rounded border border-[#ffffff0a]">
                <span className="text-[#888]">insider_holding</span>
                <span className={coverage.insider_holding_pct > 0 ? 'text-green-400' : 'text-yellow-400'}>{coverage.insider_holding_pct}/{coverage.total_symbols}</span>
              </div>
              <div className="flex justify-between text-[12px] font-mono px-2 py-1 bg-[#0e1117] rounded border border-[#ffffff0a]">
                <span className="text-[#888]">promoter_holding</span>
                <span className={coverage.promoter_holding_pct > 0 ? 'text-green-400' : 'text-yellow-400'}>{coverage.promoter_holding_pct}/{coverage.total_symbols}</span>
              </div>
              <div className="flex justify-between text-[12px] font-mono px-2 py-1 bg-[#0e1117] rounded border border-[#ffffff0a]">
                <span className="text-[#888]">industry</span>
                <span className={coverage.industry > 0 ? 'text-green-400' : 'text-yellow-400'}>{coverage.industry}/{coverage.total_symbols}</span>
              </div>
              <div className="flex justify-between text-[12px] font-mono px-2 py-1 bg-[#0e1117] rounded border border-[#ffffff0a]">
                <span className="text-[#888]">free_float</span>
                <span className={coverage.free_float_pct > 0 ? 'text-green-400' : 'text-yellow-400'}>{coverage.free_float_pct}/{coverage.total_symbols}</span>
              </div>
            </div>
            {coverage.shares_stale && (
              <div className="text-[12px] font-mono text-yellow-400 mt-1">
                ⚠ Shares data is stale — run "Shares Refresh" from pipeline tasks
              </div>
            )}
          </>
        )}
        </>
        )}
      </div>

      {/* Live Event Stream — shared with Mission Control via usePipeline */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-[#888] uppercase tracking-wider flex items-center gap-2">
            <Server size={14} />
            Live Event Stream
            {!connected && <span className="text-yellow-400 normal-case tracking-normal">(reconnecting…)</span>}
            {sseEvents.length > 0 && (
              <span className="text-[#888] font-normal normal-case tracking-normal">
                ({sseEvents.length} events)
              </span>
            )}
          </h3>
        </div>
        <div className="h-40 overflow-y-auto font-mono text-[12px] space-y-0.5">
          {sseEvents.length === 0 ? (
            <p className="text-[#888] py-4 text-center">
              Waiting for events — run a task to see activity here
            </p>
          ) : (
            sseEvents.map((ev, i) => {
              const { text, cls } = describeEvent(ev);
              const time = new Date(ev.time).toLocaleTimeString('en-IN', {
                hour: '2-digit', minute: '2-digit', second: '2-digit',
              });
              return (
                <div key={`${ev.time}-${i}`} className="flex gap-2 py-0.5 border-b border-[#ffffff04] last:border-0">
                  <span className="text-[#888] shrink-0 w-16">{time}</span>
                  <span className={cls}>{text}</span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
