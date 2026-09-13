import { useState, useEffect } from 'react';
import { API_BASE } from '../config';
import { 
  CheckCircle, 
  XCircle, 
  ChevronDown, 
  ChevronUp, 
  RefreshCw,
  Calendar,
  Database,
  TrendingUp,
  HardDrive,
  ScanLine
} from 'lucide-react';

interface HealthData {
  latest_ohlcv_date: string | null;
  days_behind: number | null;
  ohlcv_symbols_today: number | null;
  enrichment_complete: boolean | null;
  fundamentals_total: number | null;
  fundamentals_with_promoter: number | null;
  fundamentals_with_free_float: number | null;
  nifty_benchmark_latest: string | null;
  last_backup_date: string;
  scanner_cache_counts: Record<string, number>;
}

function fmtDate(dateStr: string | null): string {
  if (!dateStr) return '\u2014';
  try {
    return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return dateStr;
  }
}

function daysAgo(dateStr: string): number | null {
  if (!dateStr) return null;
  try {
    const d = new Date(dateStr + 'T00:00:00');
    const now = new Date();
    return Math.floor((now.getTime() - d.getTime()) / 86400000);
  } catch {
    return null;
  }
}

function pctColor(pct: number): string {
  if (pct >= 70) return 'text-success';
  if (pct >= 50) return 'text-warning';
  return 'text-error';
}

function daysColor(days: number | null): string {
  if (days === null) return 'text-text-tertiary';
  if (days <= 1) return 'text-success';
  if (days === 2) return 'text-warning';
  return 'text-error';
}

function getDaysBadgeStyle(days: number | null): string {
  if (days === null) return 'bg-text-tertiary/20 text-text-tertiary';
  if (days <= 1) return 'bg-success-bg text-success border border-success-border';
  if (days === 2) return 'bg-warning-bg text-warning border border-warning-border';
  return 'bg-error-bg text-error border border-error-border';
}

export default function HealthStatusBar() {
  const [status, setStatus] = useState<'loading' | 'error' | 'success'>('loading');
  const [data, setData] = useState<HealthData | null>(null);
  const [showScanners, setShowScanners] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchHealth = async () => {
      try {
        const res = await fetch(`${API_BASE}/data-health`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json: HealthData = await res.json();
        if (!cancelled) {
          setData(json);
          setStatus('success');
        }
      } catch {
        if (!cancelled) {
          setStatus('error');
        }
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 300_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (status === 'loading') {
    return (
      <div 
        className="fixed top-0 inset-x-0 z-[100] h-10 bg-gradient-to-b from-[#0e1117] to-[#0e1117]/95 border-b border-border-default flex items-center px-4 text-xs text-text-secondary gap-2 font-mono backdrop-blur-sm animate-pulse"
        role="status"
        aria-label="Loading system health status"
      >
        <div className="w-2 h-2 rounded-full bg-warning animate-pulse" />
        Loading system health…
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div 
        className="fixed top-0 inset-x-0 z-[100] h-10 bg-gradient-to-b from-[#0e1117] to-[#0e1117]/95 border-b border-error-border flex items-center px-4 text-xs text-warning gap-2 font-mono backdrop-blur-sm"
        role="alert"
        aria-label="System health error"
      >
        <div className="w-2 h-2 rounded-full bg-error shadow-[0_0_8px_rgba(248,113,113,0.5)]" />
        System health unavailable
        <button
          onClick={() => {
            setStatus('loading');
            setData(null);
          }}
          className="ml-2 p-1 hover:bg-white/10 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          aria-label="Retry loading health status"
        >
          <RefreshCw size={14} />
        </button>
      </div>
    );
  }

  if (!data) return null;

  const promPct = data.fundamentals_total
    ? Math.round((data.fundamentals_with_promoter! / data.fundamentals_total) * 100)
    : 0;
  const ffPct = data.fundamentals_total
    ? Math.round((data.fundamentals_with_free_float! / data.fundamentals_total) * 100)
    : 0;
  const backupAge =
    data.last_backup_date && data.last_backup_date !== 'unknown'
      ? daysAgo(data.last_backup_date)
      : null;
  const backupColor =
    backupAge === null
      ? 'text-text-tertiary'
      : backupAge <= 2
        ? 'text-success'
        : backupAge <= 7
          ? 'text-warning'
          : 'text-error';

  return (
    <div 
      className="fixed top-0 inset-x-0 z-[100] h-10 bg-gradient-to-b from-[#0e1117] to-[#0e1117]/95 border-b border-border-default flex items-center px-4 text-xs text-text-primary gap-3 font-mono backdrop-blur-sm"
      role="status"
      aria-label="System health status"
    >
      {/* Data Freshness */}
      <span className="flex items-center gap-1.5 shrink-0">
        <Calendar size={12} className="text-text-tertiary" />
        <span className="text-text-secondary">Freshness:</span>
        <span className={daysColor(data.days_behind)}>
          {data.latest_ohlcv_date ? fmtDate(data.latest_ohlcv_date) : '—'}
        </span>
        {data.days_behind !== null && data.days_behind > 0 && (
          <span
            className={`px-2 py-0.5 rounded-full text-[11px] font-semibold leading-none ${getDaysBadgeStyle(data.days_behind)}`}
          >
            {data.days_behind}d behind
          </span>
        )}
        {data.ohlcv_symbols_today !== null && (
          <span className="text-text-tertiary">({data.ohlcv_symbols_today} sym)</span>
        )}
      </span>

      <span className="text-border-default">|</span>

      {/* Enrichment */}
      <span className="flex items-center gap-1.5 shrink-0">
        {data.enrichment_complete ? (
          <CheckCircle size={12} className="text-success" />
        ) : (
          <XCircle size={12} className="text-error" />
        )}
        <span className={data.enrichment_complete ? 'text-success' : 'text-error'}>
          {data.enrichment_complete ? 'Enrichment OK' : 'Incomplete'}
        </span>
      </span>

      <span className="text-border-default">|</span>

      {/* Fundamentals Coverage */}
      <span className="flex items-center gap-2 shrink-0">
        <Database size={12} className="text-text-tertiary" />
        <span className="text-text-secondary">Prom:</span>
        <span className={pctColor(promPct)}>{promPct}%</span>
        <span className="text-text-secondary">FF:</span>
        <span className={pctColor(ffPct)}>{ffPct}%</span>
      </span>

      <span className="text-border-default">|</span>

      {/* Backups */}
      <span className="flex items-center gap-1.5 shrink-0">
        <HardDrive size={12} className="text-text-tertiary" />
        <span className="text-text-secondary">Backup:</span>
        <span className={backupColor}>
          {data.last_backup_date && data.last_backup_date !== 'unknown'
            ? fmtDate(data.last_backup_date)
            : 'N/A'}
        </span>
      </span>

      <span className="text-border-default">|</span>

      {/* Scanner Counts (collapsible) */}
      <button
        onClick={() => setShowScanners(!showScanners)}
        className="flex items-center gap-1 text-text-secondary hover:text-text-primary transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded px-1"
        aria-expanded={showScanners}
        aria-controls="scanner-counts-panel"
      >
        <ScanLine size={12} />
        Scanners
        {showScanners ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
      </button>

      {showScanners && (
        <div 
          id="scanner-counts-panel"
          className="absolute top-full left-0 mt-1 bg-bg-elevated border border-border-default rounded-lg shadow-xl p-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px] z-50 min-w-[300px]"
          role="region"
          aria-label="Scanner result counts"
        >
          {Object.entries(data.scanner_cache_counts || {}).map(([name, count]) => (
            <span key={name} className="flex justify-between gap-3 whitespace-nowrap">
              <span className="text-text-secondary">{name.replace(/_/g, ' ')}:</span>
              <span className="text-text-primary font-bold">{count}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
