import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';

interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  wyckoff_event: string;
  phase: string;
  phase_complete_pct: number;
  event_date: string;
  event_delivery_pct: number;
  vol_ratio: number;
  event_quality: number;
  range_low_90: number;
  range_high_90: number;
  close: number;
  days_since_event: number;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
}

function relativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Never';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return 'Never';
    const diffMs = Date.now() - d.getTime();
    if (diffMs < 0) return 'Just now';
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return dateStr || 'Never';
  }
}

const EVENT_COLORS: Record<string, string> = {
  'SC': 'bg-red-500/20 text-red-400 border-red-500/30',
  'AR': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  'ST': 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  'Spring': 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  'SOS': 'bg-green-500/20 text-green-400 border-green-500/30',
};

const PHASE_COLORS: Record<string, string> = {
  'Phase A': 'bg-[#ffffff1a] text-[#888] border-[#ffffff1a]',
  'Phase B': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  'Phase C': 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  'Phase D': 'bg-green-500/20 text-green-400 border-green-500/30 shadow-[0_0_8px_rgba(34,197,94,0.3)]',
};

function gradeBadge(quality: number): { label: string; color: string } {
  if (quality >= 75) return { label: 'A', color: 'bg-green-500/20 text-green-400' };
  if (quality >= 55) return { label: 'B', color: 'bg-blue-500/20 text-blue-400' };
  if (quality >= 40) return { label: 'C', color: 'bg-amber-500/20 text-amber-400' };
  return { label: 'D', color: 'bg-red-500/20 text-red-400' };
}

function daysColor(days: number): string {
  if (days <= 5) return 'text-green-400';
  if (days <= 15) return 'text-yellow-400';
  return 'text-[#888]';
}

const EVENT_TYPES = ['All', 'SC', 'AR', 'ST', 'Spring', 'SOS'];
const PHASE_TYPES = ['All', 'Phase A', 'Phase B', 'Phase C', 'Phase D'];

export default function WyckoffAutomatonView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [eventFilter, setEventFilter] = useState<string>('All');
  const [phaseFilter, setPhaseFilter] = useState<string>('All');
  const [minQualityFilter, setMinQualityFilter] = useState(0);
  const [maxDaysFilter, setMaxDaysFilter] = useState<number>(30);

  const [sortCol, setSortCol] = useState<string>('phase_complete_pct');
  const [sortAsc, setSortAsc] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/wyckoff/status`);
      if (!res.ok) return;
      const data = await res.json();
      setScanStatus(data);
      setIsScanning(data.scan_status === 'scanning');
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const triggerScan = useCallback(async () => {
    setIsScanning(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {};
      if (mcapRange) {
        body.min_mcap = mcapRange.min;
        body.max_mcap = mcapRange.max;
      }
      const res = await fetch(`${API_BASE}/wyckoff/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errData = await res.json();
        setError(errData.detail || 'Scan failed');
        setIsScanning(false);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Network error');
      setIsScanning(false);
    }
  }, [mcapRange]);

  useEffect(() => {
    if (!mcapRange) {
      fetchMarketCapMap().then((map) => {
        mcapMapRef.current = map;
        const mcapValues = Array.from(map.values()).filter((v) => v > 0);
        if (mcapValues.length > 0) {
          const sorted = [...mcapValues].sort((a, b) => a - b);
          setMcapRange({
            min: sorted[0],
            max: sorted[sorted.length - 1],
          });
        }
      });
    }
  }, [mcapRange]);

  const isStale = useMemo(() => {
    if (!scanStatus?.last_scan) return true;
    const diffMs = Date.now() - new Date(scanStatus.last_scan).getTime();
    return diffMs > 30 * 60 * 1000;
  }, [scanStatus?.last_scan]);

  const sectors = useMemo(() => {
    const set = new Set<string>();
    (scanStatus?.candidates ?? []).forEach((c) => {
      if (c.sector) set.add(c.sector);
    });
    return ['All', ...Array.from(set).sort()];
  }, [scanStatus?.candidates]);

  const filteredData = useMemo(() => {
    let data = scanStatus?.candidates ?? [];
    if (watchlistOnly) {
      data = data.filter((c) => isWatched(c.symbol));
    }
    if (sectorFilter !== 'All') {
      data = data.filter((c) => c.sector === sectorFilter);
    }
    if (eventFilter !== 'All') {
      data = data.filter((c) => c.wyckoff_event === eventFilter);
    }
    if (phaseFilter !== 'All') {
      data = data.filter((c) => c.phase === phaseFilter);
    }
    if (minQualityFilter > 0) {
      data = data.filter((c) => c.event_quality >= minQualityFilter);
    }
    if (maxDaysFilter < 999) {
      data = data.filter((c) => c.days_since_event <= maxDaysFilter);
    }
    data.sort((a, b) => {
      const aVal = a[sortCol as keyof Candidate] ?? 0;
      const bVal = b[sortCol as keyof Candidate] ?? 0;
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      return sortAsc ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
    });
    return data;
  }, [scanStatus?.candidates, watchlistOnly, isWatched, sectorFilter, eventFilter, phaseFilter, minQualityFilter, maxDaysFilter, sortCol, sortAsc]);

  const stats = useMemo(() => {
    const data = scanStatus?.candidates ?? [];
    return {
      total: data.length,
      avgQuality: data.length ? (data.reduce((s, c) => s + c.event_quality, 0) / data.length) : 0,
      phaseD: data.filter((c) => c.phase === 'Phase D').length,
      springCount: data.filter((c) => c.wyckoff_event === 'Spring').length,
      recent: data.filter((c) => c.days_since_event <= 5).length,
    };
  }, [scanStatus?.candidates]);

  const handleSort = (col: string) => {
    if (sortCol === col) {
      setSortAsc((prev) => !prev);
    } else {
      setSortCol(col);
      setSortAsc(false);
    }
  };

  function SortIcon({ col }: { col: string }) {
    if (sortCol !== col) return <ArrowUpDown size={12} className="inline ml-1 opacity-40" />;
    return sortAsc ? <ChevronUp size={12} className="inline ml-1" /> : <ChevronDown size={12} className="inline ml-1" />;
  }

  const exportCSV = () => {
    const data = filteredData;
    if (!data.length) return;
    const headers = ['Symbol', 'Sector', 'MCap Cr', 'Event', 'Phase', 'Phase%', 'Event Date', 'Days Ago', 'Del%', 'Vol Ratio', 'Range Low', 'Range High', 'Quality', 'Close'];
    const rows = data.map((c) => [
      c.symbol, c.sector ?? '', c.market_cap_cr, c.wyckoff_event, c.phase,
      c.phase_complete_pct, c.event_date, c.days_since_event,
      c.event_delivery_pct, c.vol_ratio, c.range_low_90, c.range_high_90,
      c.event_quality, c.close,
    ]);
    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `wyckoff_automaton_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col gap-4 px-2 py-4 w-full max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Box className="text-purple-400" size={24} />
          <h1 className="text-xl font-semibold text-[#fafafa]">Wyckoff Automaton</h1>
        </div>
        <div className="flex items-center gap-3">
          {isScanning && (
            <span className="flex items-center gap-1 text-sm text-yellow-400">
              <RefreshCw size={14} className="animate-spin" /> Scanning...
            </span>
          )}
          <button
            onClick={triggerScan}
            disabled={isScanning}
            className="flex items-center gap-1.5 px-4 py-1.5 bg-purple-500/20 text-purple-400 border border-purple-500/30 rounded-lg hover:bg-purple-500/30 disabled:opacity-50 text-sm transition-colors"
          >
            <RefreshCw size={15} className={isScanning ? 'animate-spin' : ''} />
            {isScanning ? 'Scanning...' : 'Run Scan'}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          <XCircle size={16} /> {error}
        </div>
      )}

      {/* Stale Banner */}
      {isStale && staleBannerOpen && scanStatus?.last_scan && (
        <div className="flex items-center justify-between p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg text-sm text-amber-400">
          <span className="flex items-center gap-2"><Clock size={16} /> Scan data is over 30 minutes old ({relativeTime(scanStatus.last_scan)}).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="text-[#888] hover:text-[#fafafa]">✕</button>
        </div>
      )}

      {/* Filters + MCap */}
      <div className="flex flex-wrap items-center gap-3">
        <MarketCapRangeFilter
          mcapMap={mcapMapRef.current}
          min={mcapRange?.min ?? 0}
          max={mcapRange?.max ?? 50000}
          onChange={(min, max) => setMcapRange({ min, max })}
        />

        <div className="flex items-center gap-2 text-sm">
          <Filter size={14} className="text-[#888]" />
          <select className="bg-[#1a1a1a] border border-[#333] rounded px-2 py-1 text-[#fafafa] text-xs" value={eventFilter} onChange={(e) => setEventFilter(e.target.value)}>
            {EVENT_TYPES.map((t) => <option key={t} value={t}>{t === 'All' ? 'All Events' : t}</option>)}
          </select>
          <select className="bg-[#1a1a1a] border border-[#333] rounded px-2 py-1 text-[#fafafa] text-xs" value={phaseFilter} onChange={(e) => setPhaseFilter(e.target.value)}>
            {PHASE_TYPES.map((t) => <option key={t} value={t}>{t === 'All' ? 'All Phases' : t}</option>)}
          </select>
          <select className="bg-[#1a1a1a] border border-[#333] rounded px-2 py-1 text-[#fafafa] text-xs" value={sectorFilter} onChange={(e) => setSectorFilter(e.target.value)}>
            {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="bg-[#1a1a1a] border border-[#333] rounded px-2 py-1 text-[#fafafa] text-xs" value={maxDaysFilter} onChange={(e) => setMaxDaysFilter(Number(e.target.value))}>
            <option value={999}>All Days</option>
            <option value={5}>≤ 5 Days</option>
            <option value={10}>≤ 10 Days</option>
            <option value={20}>≤ 20 Days</option>
            <option value={30}>≤ 30 Days</option>
          </select>
        </div>

        <label className="flex items-center gap-1.5 text-sm text-[#888] cursor-pointer">
          <input type="checkbox" checked={watchlistOnly} onChange={(e) => setWatchlistOnly(e.target.checked)} className="accent-purple-500" />
          Watchlist only
        </label>

        <div className="flex items-center gap-2 text-sm">
          <span className="text-[#888]">Min Quality:</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minQualityFilter}
            onChange={(e) => setMinQualityFilter(Number(e.target.value))}
            className="w-24 accent-purple-500"
          />
          <span className="text-[#fafafa] w-6 text-xs">{minQualityFilter}</span>
        </div>

        <button onClick={exportCSV} className="flex items-center gap-1 px-3 py-1 bg-[#1a1a1a] border border-[#333] rounded text-xs text-[#888] hover:text-[#fafafa]">
          <Download size={12} /> CSV
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-5 gap-3 text-sm">
        <StatCard label="Total Signals" value={stats.total} />
        <StatCard label="Avg Quality" value={`${stats.avgQuality.toFixed(1)}`} />
        <StatCard label="Phase D (Breakout)" value={stats.phaseD} color="text-green-400" />
        <StatCard label="Spring Detected" value={stats.springCount} color="text-purple-400" />
        <StatCard label="Recent (≤5d)" value={stats.recent} color="text-green-400" />
      </div>

      {/* Progress */}
      {isScanning && scanStatus && (
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-xs text-[#888]">
            <span>{scanStatus.message}</span>
            <span>{scanStatus.progress}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#1a1a1a] rounded-full overflow-hidden">
            <div className="h-full bg-purple-500 rounded-full transition-all duration-300" style={{ width: `${scanStatus.progress}%` }} />
          </div>
        </div>
      )}

      {/* Table */}
      {!isScanning && filteredData.length > 0 && (
        <ScrollableTable>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#888] uppercase text-xs tracking-wider">
                <Th sortable col="symbol" current={sortCol} asc={sortAsc} onClick={handleSort}><SortIcon col="symbol" /></Th>
                <Th sortable col="sector" current={sortCol} asc={sortAsc} onClick={handleSort}><SortIcon col="sector" /></Th>
                <Th sortable col="market_cap_cr" current={sortCol} asc={sortAsc} onClick={handleSort} right><SortIcon col="market_cap_cr" /></Th>
                <Th sortable col="wyckoff_event" current={sortCol} asc={sortAsc} onClick={handleSort}><SortIcon col="wyckoff_event" /></Th>
                <Th sortable col="phase" current={sortCol} asc={sortAsc} onClick={handleSort}><SortIcon col="phase" /></Th>
                <Th sortable col="phase_complete_pct" current={sortCol} asc={sortAsc} onClick={handleSort}><SortIcon col="phase_complete_pct" /></Th>
                <Th sortable col="event_date" current={sortCol} asc={sortAsc} onClick={handleSort}><SortIcon col="event_date" /></Th>
                <Th sortable col="days_since_event" current={sortCol} asc={sortAsc} onClick={handleSort} right><SortIcon col="days_since_event" /></Th>
                <Th sortable col="event_delivery_pct" current={sortCol} asc={sortAsc} onClick={handleSort} right><SortIcon col="event_delivery_pct" /></Th>
                <Th sortable col="vol_ratio" current={sortCol} asc={sortAsc} onClick={handleSort} right><SortIcon col="vol_ratio" /></Th>
                <Th right>Range</Th>
                <Th sortable col="event_quality" current={sortCol} asc={sortAsc} onClick={handleSort} right><SortIcon col="event_quality" /></Th>
                <Th sortable col="close" current={sortCol} asc={sortAsc} onClick={handleSort} right><SortIcon col="close" /></Th>
              </tr>
            </thead>
            <tbody>
              {filteredData.map((c) => {
                const g = gradeBadge(c.event_quality);
                return (
                  <tr key={c.symbol} className="border-b border-[#ffffff08] hover:bg-[#ffffff04]">
                    <td className="py-2 px-2">
                      <div className="flex items-center gap-1.5">
                        <StarButton symbol={c.symbol} isWatched={isWatched(c.symbol)} size={13} />
                        <span className="text-[#fafafa] font-medium">{c.symbol}</span>
                      </div>
                    </td>
                    <td className="py-2 px-2 text-[#888]">{c.sector ?? '—'}</td>
                    <td className="py-2 px-2 text-right text-[#fafafa]">{c.market_cap_cr?.toFixed(1) ?? '—'}</td>
                    <td className="py-2 px-2">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${EVENT_COLORS[c.wyckoff_event] ?? 'bg-[#ffffff1a] text-[#888]'}`}>
                        {c.wyckoff_event}
                      </span>
                    </td>
                    <td className="py-2 px-2">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${PHASE_COLORS[c.phase] ?? 'bg-[#ffffff1a] text-[#888]'}`}>
                        {c.phase}
                      </span>
                    </td>
                    <td className="py-2 px-2">
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-[#1a1a1a] rounded-full overflow-hidden">
                          <div className="h-full bg-green-500 rounded-full" style={{ width: `${c.phase_complete_pct}%` }} />
                        </div>
                        <span className="text-xs text-[#888]">{c.phase_complete_pct}%</span>
                      </div>
                    </td>
                    <td className="py-2 px-2 text-[#888] whitespace-nowrap">{c.event_date}</td>
                    <td className={`py-2 px-2 text-right ${daysColor(c.days_since_event)}`}>{c.days_since_event}d</td>
                    <td className="py-2 px-2 text-right text-[#fafafa]">{c.event_delivery_pct?.toFixed(1) ?? '—'}%</td>
                    <td className="py-2 px-2 text-right text-[#fafafa]">{c.vol_ratio?.toFixed(1) ?? '—'}x</td>
                    <td className="py-2 px-2 text-right text-[#888] whitespace-nowrap">
                      {c.range_low_90?.toFixed(1) ?? '—'} – {c.range_high_90?.toFixed(1) ?? '—'}
                    </td>
                    <td className="py-2 px-2 text-right">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-bold ${g.color}`}>{g.label}</span>
                    </td>
                    <td className="py-2 px-2 text-right text-[#fafafa]">{c.close?.toFixed(2) ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollableTable>
      )}

      {/* Empty State */}
      {!isScanning && scanStatus && filteredData.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[#888]">
          <AlertTriangle size={32} className="mb-2 opacity-40" />
          <p className="text-sm">No Wyckoff signals detected. Run a scan to check.</p>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="bg-[#1a1a1a] border border-[#333] rounded-lg p-3">
      <div className="text-[#888] text-xs mb-1">{label}</div>
      <div className={`text-lg font-semibold ${color ?? 'text-[#fafafa]'}`}>{value}</div>
    </div>
  );
}

function Th({ children, sortable, col, current, asc, onClick, right }: {
  children: React.ReactNode;
  sortable?: boolean;
  col?: string;
  current?: string;
  asc?: boolean;
  onClick?: (col: string) => void;
  right?: boolean;
}) {
  return (
    <th
      className={`py-2 px-2 ${right ? 'text-right' : 'text-left'} ${sortable ? 'cursor-pointer hover:text-[#fafafa] select-none' : ''}`}
      onClick={() => sortable && col && onClick?.(col)}
    >
      {children}
    </th>
  );
}
