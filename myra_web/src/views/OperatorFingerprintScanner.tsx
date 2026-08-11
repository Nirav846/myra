import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star, Check, X, Info } from 'lucide-react';
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
  compression_ratio: number;
  delivery_drift: number;
  quiet_accum_days: number;
  volume_staircase: boolean;
  coil_tension_score: number;
  grade: string;
  close: number;
  atr_old_pct: number;
  atr_new_pct: number;
  base_duration_days: number;
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

const STATUS_FILTERS = ['All', 'In Box', 'Breakout Pending', 'Triggered', 'Invalidated'];

export default function OperatorFingerprintScannerView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [minTensionFilter, setMinTensionFilter] = useState(0);
  const [minQuietFilter, setMinQuietFilter] = useState(0);
  const [staircaseOnly, setStaircaseOnly] = useState(false);

  const [scanDate, setScanDate] = useState('');

  const [sortCol, setSortCol] = useState<string>('coil_tension_score');
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
    if (minTensionFilter > 0) data = data.filter(d => d.coil_tension_score >= minTensionFilter);
    if (minQuietFilter > 0) data = data.filter(d => d.quiet_accum_days >= minQuietFilter);
    if (staircaseOnly) data = data.filter(d => d.volume_staircase);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, minTensionFilter, minQuietFilter, staircaseOnly, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-fuchsia-400" />
      : <ChevronDown size={10} className="inline ml-1 text-fuchsia-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/operator-fingerprint/status`);
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
      const res = await fetch(`${API_BASE}/operator-fingerprint/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_mcap: mcapRange?.min ?? 200,
          max_mcap: mcapRange?.max ?? 50000,
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
  }, [fetchScanStatus, clearPolling, mcapRange, scanDate]);

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
      'Symbol', 'Sector', 'Market Cap Cr', 'Compression Ratio', 'Del Drift',
      'Quiet Days', 'Staircase', 'Base Duration', 'Tension Score', 'Close',
      'ATR Old%', 'ATR New%', 'Grade',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.market_cap_cr, r.compression_ratio, r.delivery_drift,
      r.quiet_accum_days, r.volume_staircase, r.base_duration_days, r.coil_tension_score,
      r.close, r.atr_old_pct, r.atr_new_pct, r.grade,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `operator_fingerprint_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Operator Fingerprint Scanner">
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
          <div className="bg-fuchsia-500/20 p-2 rounded" aria-hidden="true">
            <Box className="text-fuchsia-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Operator Fingerprint</h1>
            <p className="text-xs font-mono text-[#888]">Coil Detection via ATR Compression + Delivery Drift</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-fuchsia-600 hover:bg-fuchsia-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-400/50"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
            ) : (
              <><Box size={14} fill="currentColor" aria-hidden="true" /> Scan</>
            )}
          </button>
          <button
            onClick={() => fetch(`${API_BASE}/cache/operator-fingerprint`, { method: 'DELETE' })}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {isScanning && (
        <div className="bg-fuchsia-500/10 border border-fuchsia-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-fuchsia-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-fuchsia-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum Coil Tension Score. 0 = all candidates, 20+ = minimal coil, 50+ = quality.">
              <span>Min Tension</span>
            </Tooltip>
            <span className="text-fuchsia-400">{minTensionFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minTensionFilter}
            onChange={e => setMinTensionFilter(Number(e.target.value))}
            className="w-full accent-fuchsia-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/50"
            aria-label="Minimum coil tension score"
          />
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum quiet accumulation days (out of last 20). Higher = quieter operator accumulation.">
              <span>Min Quiet Days</span>
            </Tooltip>
            <span className="text-fuchsia-400">{minQuietFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={15}
            step={1}
            value={minQuietFilter}
            onChange={e => setMinQuietFilter(Number(e.target.value))}
            className="w-full accent-fuchsia-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/50"
            aria-label="Minimum quiet accumulation days"
          />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Staircase</div>
          <button
            onClick={() => setStaircaseOnly(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/50 ${
              staircaseOnly
                ? 'bg-fuchsia-500/20 border-fuchsia-500/40 text-fuchsia-400'
                : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-fuchsia-400'
            }`}
            aria-pressed={staircaseOnly}
          >
            <Check size={11} aria-hidden="true" />
            Staircase Only
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono" id="sector-filter-label">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-fuchsia-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-fuchsia-500/50"
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
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Tension</div>
              <div className="text-2xl font-bold text-fuchsia-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.coil_tension_score, 0) / filteredData.length).toFixed(0)
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Base Duration</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.base_duration_days, 0) / filteredData.length).toFixed(0) + 'd'
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">With Staircase</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.volume_staircase).length}</div>
            </div>
          </div>

          {filteredData.filter(d => d.grade === 'A').length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[12px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Coils</span>
                <span className="text-[#888]">— tightest springs ready to release</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.grade === 'A')
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-green-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className="text-fuchsia-400">{d.coil_tension_score.toFixed(0)}</span>
                      {d.volume_staircase && <Check size={10} className="text-green-400" />}
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
                aria-label="Operator Fingerprint Scanner results"
                aria-rowcount={filteredData.length}
                aria-colcount={13}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('symbol')} scope="col">
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('sector')} scope="col">
                      Sector <SortIcon column="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('market_cap_cr')} scope="col">
                      MCap (₹ Cr) <SortIcon column="market_cap_cr" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('compression_ratio')} scope="col">
                      <Tooltip content="ATR compression ratio (recent / older). <0.65 = highly compressed (good), <0.80 = compressing." good="<0.65: tight coil" bad="≥0.80: not compressing">Comp Ratio <SortIcon column="compression_ratio" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('delivery_drift')} scope="col">
                      <Tooltip content="Linear slope of delivery% over last 20 sessions. Positive = delivery drifting up (accumulation)." good=">0.3: strong drift" bad="≤0: declining">Del Drift <SortIcon column="delivery_drift" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('quiet_accum_days')} scope="col">
                      <Tooltip content="Quiet accumulation days (last 20). Days where delivery > average AND price change < 1.5% — operators accumulating stealthily.">Quiet Days <SortIcon column="quiet_accum_days" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('volume_staircase')} scope="col">
                      Staircase <SortIcon column="volume_staircase" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('base_duration_days')} scope="col">
                      <Tooltip content="Number of sessions where recent ATR < older ATR (how long the coil has been forming).">Base Dur <SortIcon column="base_duration_days" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('close')} scope="col">
                      Close <SortIcon column="close" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-fuchsia-500/50" onClick={() => handleSort('coil_tension_score')} scope="col" aria-sort={sortCol === 'coil_tension_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Tension Score <SortIcon column="coil_tension_score" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={11} className="px-4 py-8 text-center text-[#888]">No operator fingerprints match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-fuchsia-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-fuchsia-500/50"
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
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.compression_ratio < 0.65 ? 'text-green-400' :
                            row.compression_ratio < 0.80 ? 'text-yellow-400' :
                            'text-[#888]'
                          }>
                            {row.compression_ratio.toFixed(3)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.delivery_drift > 0.3 ? 'text-green-400' :
                            row.delivery_drift > 0 ? 'text-yellow-400' :
                            'text-red-400'
                          }>
                            {row.delivery_drift.toFixed(4)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.quiet_accum_days}</td>
                        <td className="px-3 py-3 text-center">
                          {row.volume_staircase
                            ? <span className="text-green-400"><Check size={14} className="inline" /></span>
                            : <span className="text-[#888]"><X size={14} className="inline" /></span>
                          }
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.base_duration_days}d</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.coil_tension_score.toFixed(0)} · {row.grade}
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
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/50"
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
            <p>Click Scan to detect operator accumulation fingerprints.</p>
            <p className="text-[12px]">ATR compression + delivery drift + quiet accumulation = coiled spring.</p>
          </div>
        </div>
      )}
    </main>
  );
}
