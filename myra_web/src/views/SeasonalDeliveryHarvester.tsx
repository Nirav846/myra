import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star, Info } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';

interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  current_month: string;
  hist_avg_del: number;
  current_del: number | null;
  seasonal_edge: number | null;
  consistency_pct: number;
  years_of_data: number;
  early_signal: boolean;
  seasonal_score: number;
  close: number;
  wk52_pos: number;
  grade?: string;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
  bear_market?: boolean;
  scanned_date?: string | null;
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

const GRADE_COLORS: Record<string, string> = {
  A: 'bg-green-500/20 text-green-400 border-green-500/30',
  B: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  C: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  D: 'bg-red-500/20 text-red-400 border-red-500/30',
};

const MONTHS = [
  { value: null, label: 'Current Month' },
  { value: 1, label: 'Jan' }, { value: 2, label: 'Feb' }, { value: 3, label: 'Mar' },
  { value: 4, label: 'Apr' }, { value: 5, label: 'May' }, { value: 6, label: 'Jun' },
  { value: 7, label: 'Jul' }, { value: 8, label: 'Aug' }, { value: 9, label: 'Sep' },
  { value: 10, label: 'Oct' }, { value: 11, label: 'Nov' }, { value: 12, label: 'Dec' },
];

export default function SeasonalDeliveryHarvesterView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [targetMonth, setTargetMonth] = useState<number | null>(null);
  const [minConsistencyFilter, setMinConsistencyFilter] = useState(55);
  const [minEdgeFilter, setMinEdgeFilter] = useState(0);
  const [earlyOnlyFilter, setEarlyOnlyFilter] = useState(false);

  const [scanDate, setScanDate] = useState('');

  const [sortCol, setSortCol] = useState<string>('seasonal_score');
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const candidates = scanStatus?.candidates ?? [];

  const availableSectors = useMemo(() => {
    const sectors = new Set(candidates.map(c => c.sector ?? 'Unknown'));
    return ['All', ...Array.from(sectors).filter(s => s !== 'Unknown').sort(), 'Unknown'];
  }, [candidates]);

  const filteredData = useMemo(() => {
    let data = [...candidates];
    if (mcapRange) {
      const map = mcapMapRef.current;
      data = data.filter(d => {
        const mcap = map.get(d.symbol);
        return mcap !== undefined && mcap >= mcapRange.min && mcap <= mcapRange.max;
      });
    }
    if (watchlistOnly) data = data.filter(d => isWatched(d.symbol));
    if (sectorFilter !== 'All') data = data.filter(d => d.sector === sectorFilter);
    if (minConsistencyFilter > 55) data = data.filter(d => d.consistency_pct >= minConsistencyFilter);
    if (minEdgeFilter > 0) data = data.filter(d => (d.seasonal_edge ?? 0) >= minEdgeFilter);
    if (earlyOnlyFilter) data = data.filter(d => d.early_signal);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, minConsistencyFilter, minEdgeFilter, earlyOnlyFilter, isWatched, sortCol, sortAsc]);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortAsc(s => !s);
    else { setSortCol(col); setSortAsc(false); }
  };

  const SortIcon = ({ column }: { column: string }) => {
    if (sortCol !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return sortAsc
      ? <ChevronUp size={10} className="inline ml-1 text-green-400" />
      : <ChevronDown size={10} className="inline ml-1 text-green-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/seasonal-delivery/status`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: ScanStatus = await res.json();
        if (!mountedRef.current) return;
        setScanStatus(data);
        setError(null);

        if (data.scan_status === 'completed' || data.scan_status === 'error') {
          clearPolling();
          setIsScanning(false);
        } else if (data.scan_status === 'scanning' && !pollTimerRef.current) {
          pollTimerRef.current = setInterval(fetchScanStatus, 2000);
          setIsScanning(true);
        }
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || 'Error connecting to backend');
      }
    }
  }, [clearPolling]);

  const startScan = useCallback(async () => {
    if (!mountedRef.current) return;
    setIsScanning(true);
    setError(null);
    clearPolling();

    try {
      const res = await fetch(`${API_BASE}/seasonal-delivery/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_mcap: mcapRange?.min ?? 200,
          max_mcap: mcapRange?.max ?? 50000,
          target_month: targetMonth,
          ...(scanDate.trim() && { scan_date: scanDate }),
        }),
      });
      if (!mountedRef.current) return;
      if (res.ok) {
        await fetchScanStatus();
        pollTimerRef.current = setInterval(fetchScanStatus, 2000);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Failed to start scan' }));
        setError(err.detail || 'Failed to start scan');
        setIsScanning(false);
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || 'Error connecting to backend');
        setIsScanning(false);
      }
    }
  }, [fetchScanStatus, clearPolling, mcapRange, targetMonth, scanDate]);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchScanStatus, clearPolling]);

  const isStale = scanStatus?.last_scan && (Date.now() - new Date(scanStatus.last_scan).getTime() > 30 * 60 * 1000);

  const handleCSV = () => {
    if (filteredData.length === 0) return;
    const headers = [
      'Symbol', 'Sector', 'Market Cap Cr', 'Month', 'Hist Avg Del%', 'This Month Del%',
      'Seasonal Edge(pp)', 'Consistency%', 'Years', 'Early Signal',
      'Seasonal Score', 'Close', '52W Pos%',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.market_cap_cr, r.current_month, r.hist_avg_del,
      r.current_del ?? '', r.seasonal_edge ?? '', r.consistency_pct, r.years_of_data,
      r.early_signal ? 'Yes' : 'No', r.seasonal_score, r.close, r.wk52_pos,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `seasonal_delivery_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Seasonal Delivery Harvester">
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" aria-hidden="true" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-green-500/20 p-2 rounded" aria-hidden="true">
            <Box className="text-green-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Seasonal Delivery Harvester</h1>
            <p className="text-xs font-mono text-[#888]">Calendar-Driven Institutional Delivery Patterns</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-400/50"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
            ) : (
              <><Box size={14} fill="currentColor" aria-hidden="true" /> Scan</>
            )}
          </button>
          <button
            onClick={() => fetch(`${API_BASE}/cache/seasonal-delivery`, { method: 'DELETE' })}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {isScanning && (
        <div className="bg-green-500/10 border border-green-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-green-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span>{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-green-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
          </div>
        </div>
      )}

      {!isScanning && scanStatus && scanStatus.scan_status !== 'idle' && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded text-xs font-mono border ${
          scanStatus.scan_status === 'completed' ? 'bg-green-500/10 border-green-500/30 text-green-300' :
          scanStatus.scan_status === 'error' ? 'bg-red-500/10 border-red-500/30 text-red-300' :
          'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
        }`} role="status" aria-live="polite">
          {scanStatus.scan_status === 'completed' ? <CheckCircle size={14} className="text-green-400" aria-hidden="true" /> :
           scanStatus.scan_status === 'error' ? <XCircle size={14} className="text-red-400" aria-hidden="true" /> :
           <Clock size={14} aria-hidden="true" />}
          <span>
            {scanStatus.scan_status === 'completed' ? `Completed (${relativeTime(scanStatus.last_scan)})` :
             scanStatus.scan_status === 'error' ? 'Scan failed' :
             scanStatus.message}
          </span>
          <span className="ml-auto text-[#888]">{scanStatus.message}</span>
        </div>
      )}

      {scanDate && scanStatus?.scan_status === 'completed' && scanStatus.scanned_date && scanStatus.scanned_date !== scanDate && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded text-[12px] font-mono text-cyan-400 bg-cyan-500/5 border border-cyan-500/20">
          <Info size={12} aria-hidden="true" />
          <span>Selected date is a holiday or weekend — adjusted to {scanStatus.scanned_date} (previous trading day)</span>
        </div>
      )}

      {error && !isScanning && (
        <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono text-red-300" role="alert">
          <AlertTriangle size={14} className="shrink-0" aria-hidden="true" />
          <span>Error: {error}</span>
        </div>
      )}

      <section className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end" aria-label="Filters">
        <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
          <Filter size={14} aria-hidden="true" /> <span className="font-mono uppercase font-semibold">Filters</span>
        </div>
        <div className="max-w-[220px] flex-shrink-0">
          <MarketCapRangeFilter onChange={setMcapRange} />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Watchlist</div>
          <button
            onClick={() => setWatchlistOnly(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 ${
              watchlistOnly
                ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-yellow-400'
            }`}
            aria-label={watchlistOnly ? 'Show all symbols' : 'Filter to starred watchlist only'}
            aria-pressed={watchlistOnly}
          >
            <Star size={11} fill={watchlistOnly ? 'currentColor' : 'none'} aria-hidden="true" />
            Only Starred
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">View Month</div>
          <select
            value={targetMonth ?? 0}
            onChange={e => setTargetMonth(e.target.value ? Number(e.target.value) : null)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-green-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-green-500/50"
          >
            {MONTHS.map(m => (
              <option key={m.value ?? 0} value={m.value ?? 0}>{m.label}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum consistency % — how often this month has above-average delivery.">
              <span>Min Consistency</span>
            </Tooltip>
            <span className="text-green-400">{minConsistencyFilter}%</span>
          </div>
          <input
            type="range"
            min={55}
            max={95}
            step={5}
            value={minConsistencyFilter}
            onChange={e => setMinConsistencyFilter(Number(e.target.value))}
            className="w-full accent-green-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500/50"
            aria-label="Minimum consistency percentage"
          />
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum seasonal edge in percentage points (current - historical average).">
              <span>Min Edge</span>
            </Tooltip>
            <span className="text-green-400">{minEdgeFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={25}
            step={1}
            value={minEdgeFilter}
            onChange={e => setMinEdgeFilter(Number(e.target.value))}
            className="w-full accent-green-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500/50"
            aria-label="Minimum seasonal edge"
          />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Early Signal</div>
          <button
            onClick={() => setEarlyOnlyFilter(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500/50 ${
              earlyOnlyFilter
                ? 'bg-green-500/20 border-green-500/40 text-green-400'
                : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-green-400'
            }`}
            aria-pressed={earlyOnlyFilter}
          >
            Early Only
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono" id="sector-filter-label">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-green-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-green-500/50"
            aria-labelledby="sector-filter-label"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </section>

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Early Signals</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.early_signal).length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Consistency</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.consistency_pct, 0) / filteredData.length).toFixed(0) + '%'
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Score</div>
              <div className="text-2xl font-bold text-purple-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.seasonal_score, 0) / filteredData.length).toFixed(0)
                  : '—'}
              </div>
            </div>
          </div>

          {filteredData.filter(d => d.early_signal).length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[12px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Early Signals</span>
                <span className="text-[#888]">— seasonal delivery starting early, best entry window</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.early_signal)
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-green-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className="text-green-400 font-bold">EARLY</span>
                      <span className="text-yellow-400">+{d.seasonal_edge?.toFixed(1)}pp</span>
                    </div>
                  ))
                }
              </div>
            </div>
          )}

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Seasonal Delivery Harvester results"
                aria-rowcount={filteredData.length}
                aria-colcount={13}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('symbol')} scope="col">
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('sector')} scope="col">
                      Sector <SortIcon column="sector" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('market_cap_cr')} scope="col">
                      MCap (₹ Cr) <SortIcon column="market_cap_cr" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('current_month')} scope="col">
                      Month <SortIcon column="current_month" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('hist_avg_del')} scope="col">
                      <Tooltip content="Historical average delivery% for this month (excluding current year).">Hist Avg% <SortIcon column="hist_avg_del" /></Tooltip>
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('current_del')} scope="col">
                      <Tooltip content="This month's delivery% so far. Higher than hist avg = seasonal trigger.">This Month% <SortIcon column="current_del" /></Tooltip>
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('seasonal_edge')} scope="col">
                      <Tooltip content="(Current - Historical avg) in percentage points. >15pp = strong seasonal edge." good=">15: strong" bad="<8: weak">Edge(pp) <SortIcon column="seasonal_edge" /></Tooltip>
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('consistency_pct')} scope="col">
                      <Tooltip content="% of years this month had above-avg delivery. ≥80% = highly reliable pattern." good="≥80: reliable" bad="<60: inconsistent">Consistency% <SortIcon column="consistency_pct" /></Tooltip>
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('years_of_data')} scope="col">
                      Years <SortIcon column="years_of_data" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('early_signal')} scope="col">
                      Early <SortIcon column="early_signal" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('close')} scope="col">
                      Close <SortIcon column="close" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-green-500/50" onClick={() => handleSort('seasonal_score')} scope="col" aria-sort={sortCol === 'seasonal_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Score <SortIcon column="seasonal_score" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={12} className="px-4 py-8 text-center text-[#888]">No seasonal delivery candidates match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" scope="row">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-green-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-green-500/50"
                              aria-label={`Open chart for ${row.symbol}`}
                            >
                              {row.symbol}
                              <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" aria-hidden="true" />
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-3 text-[#888] text-[12px] max-w-[120px] truncate" title={row.sector ?? ''}>
                          {row.sector ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.market_cap_cr.toFixed(0)}</td>
                        <td className="px-3 py-3 text-center text-[#ccc] font-semibold">{row.current_month}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.hist_avg_del.toFixed(1)}%</td>
                        <td className="px-3 py-3 text-right">
                          {row.current_del != null
                            ? <span className={row.current_del > row.hist_avg_del ? 'text-green-400' : 'text-[#888]'}>{row.current_del.toFixed(1)}%</span>
                            : <span className="text-[#888]">—</span>
                          }
                        </td>
                        <td className="px-3 py-3 text-right">
                          {row.seasonal_edge != null
                            ? <span className={
                                row.seasonal_edge > 15 ? 'text-green-400' :
                                row.seasonal_edge > 8 ? 'text-yellow-400' :
                                'text-[#888]'
                              }>
                                +{row.seasonal_edge.toFixed(1)}pp
                              </span>
                            : <span className="text-[#888]">—</span>
                          }
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.consistency_pct >= 80 ? 'text-green-400' :
                            row.consistency_pct >= 60 ? 'text-yellow-400' :
                            'text-[#888]'
                          }>
                            {row.consistency_pct.toFixed(0)}%
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.years_of_data}y</td>
                        <td className="px-3 py-3 text-center">
                          {row.early_signal
                            ? <span className="px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 text-[12px] font-bold border border-green-500/30">EARLY</span>
                            : <span className="text-[#888]">—</span>
                          }
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.seasonal_score.toFixed(0)} · {row.grade || '?'}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
          <div className="flex justify-end">
            <button
              onClick={handleCSV}
              disabled={filteredData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500/50"
              aria-label="Export table as CSV"
            >
              <Download size={12} aria-hidden="true" />
              CSV
            </button>
          </div>
        </>
      )}

      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Box size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to harvest seasonal delivery patterns.</p>
            <p className="text-[12px]">Monthly institutional habits mapped across years — current vs historical delivery.</p>
          </div>
        </div>
      )}
    </main>
  );
}
