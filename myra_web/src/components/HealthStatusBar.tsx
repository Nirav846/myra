import { useState, useEffect } from 'react';
import { API_BASE } from '../config';
import { CheckCircle, XCircle, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';

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
  if (pct >= 70) return 'text-green-400';
  if (pct >= 50) return 'text-yellow-400';
  return 'text-red-400';
}

function daysColor(days: number | null): string {
  if (days === null) return 'text-[#888]';
  if (days <= 1) return 'text-green-400';
  if (days === 2) return 'text-yellow-400';
  return 'text-red-400';
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
      <div className="fixed top-0 inset-x-0 z-[100] h-9 bg-[#0e1117]/95 border-b border-[#ffffff1a] flex items-center px-4 text-xs text-[#888] gap-2 animate-pulse">
        <div className="w-2 h-2 rounded-full bg-yellow-400" />
        Loading system health\u2026
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="fixed top-0 inset-x-0 z-[100] h-9 bg-[#0e1117]/95 border-b border-yellow-500/30 flex items-center px-4 text-xs text-yellow-400 gap-2">
        <div className="w-2 h-2 rounded-full bg-yellow-400" />
        System health unavailable
        <button
          onClick={() => {
            setStatus('loading');
            setData(null);
          }}
          className="ml-2 p-0.5 hover:text-white transition-colors"
        >
          <RefreshCw size={12} />
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
      ? 'text-[#888]'
      : backupAge <= 2
        ? 'text-green-400'
        : backupAge <= 7
          ? 'text-yellow-400'
          : 'text-red-400';

  return (
    <div className="fixed top-0 inset-x-0 z-[100] h-9 bg-[#0e1117]/95 border-b border-[#ffffff1a] flex items-center px-4 text-xs text-[#ccc] gap-3 font-mono">
      {/* Data Freshness */}
      <span className="flex items-center gap-1.5 shrink-0">
        <span className="text-[#888]">Freshness:</span>
        <span className={daysColor(data.days_behind)}>
          {data.latest_ohlcv_date ? fmtDate(data.latest_ohlcv_date) : '\u2014'}
        </span>
        {data.days_behind !== null && data.days_behind > 0 && (
          <span
            className={`px-1 py-0.5 rounded text-[12px] leading-none ${daysColor(data.days_behind)} bg-current/10`}
          >
            {data.days_behind}d behind
          </span>
        )}
        {data.ohlcv_symbols_today !== null && (
          <span className="text-[#888]">({data.ohlcv_symbols_today} sym)</span>
        )}
      </span>

      <span className="text-[#888]">|</span>

      {/* Enrichment */}
      <span className="flex items-center gap-1 shrink-0">
        {data.enrichment_complete ? (
          <CheckCircle size={12} className="text-green-400" />
        ) : (
          <XCircle size={12} className="text-red-400" />
        )}
        <span className={data.enrichment_complete ? 'text-green-400' : 'text-red-400'}>
          {data.enrichment_complete ? 'Enrichment OK' : 'Incomplete'}
        </span>
      </span>

      <span className="text-[#888]">|</span>

      {/* Fundamentals Coverage */}
      <span className="flex items-center gap-2 shrink-0">
        <span className="text-[#888]">Prom:</span>
        <span className={pctColor(promPct)}>{promPct}%</span>
        <span className="text-[#888]">FF:</span>
        <span className={pctColor(ffPct)}>{ffPct}%</span>
      </span>

      <span className="text-[#888]">|</span>

      {/* Backups */}
      <span className="flex items-center gap-1.5 shrink-0">
        <span className="text-[#888]">Backup:</span>
        <span className={backupColor}>
          {data.last_backup_date && data.last_backup_date !== 'unknown'
            ? fmtDate(data.last_backup_date)
            : 'N/A'}
        </span>
      </span>

      <span className="text-[#888]">|</span>

      {/* Scanner Counts (collapsible) */}
      <button
        onClick={() => setShowScanners(!showScanners)}
        className="flex items-center gap-1 text-[#888] hover:text-[#ccc] transition-colors shrink-0"
      >
        Scanners
        {showScanners ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
      </button>

      {showScanners && (
        <div className="absolute top-full left-0 mt-0 bg-[#0e1117] border border-[#ffffff1a] rounded-b-lg p-2 shadow-lg grid grid-cols-2 gap-x-4 gap-y-1 text-[12px] z-50">
          {Object.entries(data.scanner_cache_counts || {}).map(([name, count]) => (
            <span key={name} className="flex justify-between gap-3 whitespace-nowrap">
              <span className="text-[#888]">{name.replace(/_/g, ' ')}:</span>
              <span className="text-[#ccc] font-bold">{count}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
