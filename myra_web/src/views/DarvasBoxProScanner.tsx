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
  tier: string;
  ceiling_price: number;
  floor_price: number;
  ceiling_date: string;
  floor_date: string;
  box_age_days: number;
  box_range_pct: number;
  touches_ceiling: number;
  touches_floor: number;
  dist_to_ceiling_pct: number;
  dar_box_median: number;
  sar: number;
  sar_z: number;
  ftc: number;
  breakout_dar: number;
  am: number;
  rs_mean: number;
  entry: number | null;
  sl: number | null;
  t1: number | null;
  t2: number | null;
  volume_ok: boolean;
  close: number;
  status: string;
  failure_reason: string;
  composite_score: number;
  grade: string;
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

const STATUS_COLORS: Record<string, string> = {
  'In Box': 'text-[#aaa]',
  'Breakout Pending': 'text-yellow-400',
  'Triggered': 'text-green-400',
  'Invalidated': 'text-red-400',
  'Failed Validation': 'text-orange-400',
  'Low Volume': 'text-red-300',
};

const TIER_COLORS: Record<string, string> = {
  small: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  mid: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
  large: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
};

const STATUS_FILTERS = [
  'Active',   // In Box + Breakout Pending (default — actionable setups only)
  'All',
  'In Box',
  'Breakout Pending',
  'Triggered',
  'Invalidated',
  'Failed Validation',
  'Low Volume',
];

export default function DarvasBoxProScannerView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);
  const [bearMarket, setBearMarket] = useState(false);

  const [scanDate, setScanDate] = useState('');

  const [baseDays, setBaseDays] = useState(120);
  const [minDar, setMinDar] = useState(0.2);
  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [statusFilter, setStatusFilter] = useState<string>('Active');
  const [minScoreFilter, setMinScoreFilter] = useState(0);
  const [minAmFilter, setMinAmFilter] = useState(0);
  const [minSarFilter, setMinSarFilter] = useState(0);

  const [sortCol, setSortCol] = useState<string>('composite_score');
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
    if (statusFilter === 'Active') {
      data = data.filter(d => d.status === 'In Box' || d.status === 'Breakout Pending');
    } else if (statusFilter !== 'All') {
      data = data.filter(d => d.status === statusFilter);
    }
    if (minScoreFilter > 0) data = data.filter(d => d.composite_score >= minScoreFilter);
    if (minAmFilter > 0) data = data.filter(d => d.am >= minAmFilter);
    if (minSarFilter > 0) data = data.filter(d => d.sar >= minSarFilter);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, statusFilter, minScoreFilter, minAmFilter, minSarFilter, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-purple-400" />
      : <ChevronDown size={10} className="inline ml-1 text-purple-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/darvas/status`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: ScanStatus = await res.json();
        if (!mountedRef.current) return;
        setScanStatus(data);
        setBearMarket(data.bear_market ?? false);
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
      const res = await fetch(`${API_BASE}/darvas/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_days: baseDays,
          min_dar: minDar,
          min_mcap: mcapRange?.min ?? 100,
          max_mcap: mcapRange?.max ?? 50000,
          ...(scanDate.trim() && { scan_date: scanDate }),
        }),
      });
      if (!mountedRef.current) return;
      if (res.ok) {
        await fetchScanStatus();
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
  }, [fetchScanStatus, clearPolling, baseDays, minDar, mcapRange, scanDate]);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchScanStatus, clearPolling]);

  const isStale = !scanDate && scanStatus?.last_scan && (Date.now() - new Date(scanStatus.last_scan).getTime() > 30 * 60 * 1000);

  const handleCSV = () => {
    if (filteredData.length === 0) return;
    const escapeCSV = (val: string) => val.includes(',') ? `"${val}"` : val;
    const headers = [
      'Symbol', 'Sector', 'Market Cap Cr', 'Tier', 'Box Age', 'Box Range %',
      'Ceiling', 'Floor', 'Dist to Ceiling %', 'Touches Ceiling', 'Touches Floor',
      'DAR (Box)', 'SAR', 'SAR_z', 'FTC', 'RS', 'Breakout DAR', 'AM',
      'Entry', 'SL', 'T1', 'T2',
      'Status', 'Failure Reason', 'Composite Score', 'Grade',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.market_cap_cr, r.tier, r.box_age_days, r.box_range_pct,
      r.ceiling_price?.toFixed(2) ?? '', r.floor_price?.toFixed(2) ?? '',
      r.dist_to_ceiling_pct?.toFixed(1) ?? '', r.touches_ceiling, r.touches_floor,
      r.dar_box_median, r.sar, r.sar_z, r.ftc, r.rs_mean,
      r.breakout_dar, r.am,
      r.entry ?? '', r.sl ?? '', r.t1 ?? '', r.t2 ?? '',
      r.status, escapeCSV(r.failure_reason ?? ''),
      r.composite_score, r.grade,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `darvas_box_pro_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Darvas Box Pro Scanner">
      {/* Staleness Warning */}
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" aria-hidden="true" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Header */}
      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-purple-500/20 p-2 rounded" aria-hidden="true">
            <Box className="text-purple-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Darvas Box Pro</h1>
            <p className="text-xs font-mono text-[#888]">Box Breakouts Validated by Delivery Absorption</p>
          </div>
        </div>
        <div className="flex items-center gap-2 ml-auto mr-3">
          <span className="text-[12px] text-[#888] font-mono" aria-hidden="true">Lookback:</span>
          {[
            { label: 'Quick 60d', days: 60 },
            { label: 'Standard 120d', days: 120 },
            { label: 'Deep 180d', days: 180 },
          ].map(p => (
            <button
              key={p.days}
              onClick={() => setBaseDays(p.days)}
              className={`px-2 py-1 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 ${
                baseDays === p.days
                  ? 'bg-purple-500/20 border-purple-500/40 text-purple-400'
                  : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-[#aaa]'
              }`}
              aria-pressed={baseDays === p.days}
              aria-label={`Set lookback to ${p.days} days`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
        <button
          onClick={startScan}
          disabled={isScanning}
          className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/50"
          aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
        >
          {isScanning ? (
            <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
          ) : (
            <><Box size={14} fill="currentColor" aria-hidden="true" /> Scan</>
          )}
        </button>
        <button
          onClick={() => fetch(`${API_BASE}/cache/darvas`, { method: 'DELETE' })}
          className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
          title="Clear cached scan results"
        >
          Clear cache
        </button>
      </header>

      {/* Progress / Status Bar */}
      {isScanning && (
        <div className="bg-cyan-500/10 border border-cyan-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-cyan-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-cyan-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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

      {/* Filters */}
      <section className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end" aria-label="Filters">
        <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
          <Filter size={14} aria-hidden="true" /> <span className="font-mono uppercase font-semibold">Filters</span>
        </div>
        <div className="flex flex-col gap-1 w-24">
          <label className="text-[12px] text-[#888] font-mono" id="lookback-label">Lookback Days</label>
          <input
            type="number"
            min={30}
            max={365}
            value={baseDays}
            onChange={e => setBaseDays(Number(e.target.value))}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="lookback-label"
          />
        </div>
        <div className="flex flex-col gap-1 w-24">
          <label className="text-[12px] text-[#888] font-mono" id="min-dar-label">Min DAR %</label>
          <input
            type="number"
            min={0}
            max={10}
            step={0.1}
            value={minDar}
            onChange={e => setMinDar(Number(e.target.value))}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="min-dar-label"
          />
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
          <div className="text-[12px] text-[#888] font-mono">Status</div>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
          >
            {STATUS_FILTERS.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip
              content="Minimum composite score. Default 0 shows all candidates. 55+ = quality setups, 75+ = Grade A."
            >
              <span>Min Score</span>
            </Tooltip>
            <span className="text-purple-400">{minScoreFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minScoreFilter}
            onChange={e => setMinScoreFilter(Number(e.target.value))}
            className="w-full accent-purple-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-label="Minimum composite score"
          />
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum Absorption Multiple (breakout DAR / box median DAR). Mid-cap threshold = 2.2, Large-cap = 1.5.">
              <span>Min AM</span>
            </Tooltip>
            <span className="text-purple-400">{minAmFilter.toFixed(1)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={10}
            step={0.1}
            value={minAmFilter}
            onChange={e => setMinAmFilter(Number(e.target.value))}
            className="w-full accent-purple-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-label="Minimum absorption multiple"
          />
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum Squeeze Acceleration Ratio (last-3-day DAR / box median DAR). Mid-cap threshold = 1.10, Large-cap = 1.15.">
              <span>Min SAR</span>
            </Tooltip>
            <span className="text-purple-400">{minSarFilter.toFixed(2)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={2}
            step={0.05}
            value={minSarFilter}
            onChange={e => setMinSarFilter(Number(e.target.value))}
            className="w-full accent-purple-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-label="Minimum squeeze acceleration ratio"
          />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono" id="sector-filter-label">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="sector-filter-label"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </section>

      {/* Results */}
      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          {/* Stats Summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Triggered</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.status === 'Triggered').length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg AM</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.am, 0) / filteredData.length).toFixed(2)
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Box Range</div>
              <div className="text-2xl font-bold text-purple-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.box_range_pct, 0) / filteredData.length).toFixed(1) + '%'
                  : '—'}
              </div>
            </div>
          </div>

          {/* Grade A Spotlight */}
          {filteredData.filter(d => d.grade === 'A').length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[12px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Boxes</span>
                <span className="text-[#888]">— institutional absorption confirmed</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.grade === 'A' && (d.status === 'In Box' || d.status === 'Breakout Pending' || d.status === 'Triggered'))
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-green-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className={`px-1 rounded text-[12px] border ${TIER_COLORS[d.tier] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                        {d.tier.toUpperCase()}
                      </span>
                      <span className="text-green-400">AM {d.am.toFixed(2)}</span>
                      {d.dist_to_ceiling_pct > 0 && (
                        <span className="text-yellow-400 text-[12px]">
                          {d.dist_to_ceiling_pct.toFixed(1)}% to BO
                        </span>
                      )}
                      <span className="text-red-400 text-[12px]">SL {d.sl?.toFixed(2) ?? '—'}</span>
                    </div>
                  ))
                }
              </div>
            </div>
          )}

          {/* Table */}
          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Darvas Box Pro Scanner results"
                aria-rowcount={filteredData.length}
                aria-colcount={22}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="NSE ticker. Click to open the chart in a new tab." showIcon={false}>Symbol <SortIcon column="symbol" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('sector')} scope="col" aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Sector <SortIcon column="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('market_cap_cr')} scope="col" aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      MCap (₹ Cr) <SortIcon column="market_cap_cr" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('box_age_days')} scope="col" aria-sort={sortCol === 'box_age_days' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Box Days <SortIcon column="box_age_days" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('box_range_pct')} scope="col" aria-sort={sortCol === 'box_range_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Box Range % <SortIcon column="box_range_pct" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('ceiling_price')} scope="col">
                      <Tooltip content="Box ceiling price — the resistance level being tested. Entry is 0.5% above this on a confirmed breakout close.">Ceiling <SortIcon column="ceiling_price" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('floor_price')} scope="col">
                      <Tooltip content="Box floor price — the support level. Stop loss is 0.5% below this.">Floor <SortIcon column="floor_price" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('dist_to_ceiling_pct')} scope="col">
                      <Tooltip content="Distance from current price to the box ceiling. Below 2% = breakout imminent. 0% = already broken out." good="Below 2%: watch closely" bad="0%: check status — may already be triggered">→ Ceiling <SortIcon column="dist_to_ceiling_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('touches_ceiling')} scope="col">
                      <Tooltip content="Number of times price touched the ceiling within 1%. More touches = stronger resistance = more significant breakout when it comes." good="3+ touches: very strong box" bad="Exactly 2: minimum — treat with less conviction">Touches <SortIcon column="touches_ceiling" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('dar_box_median')} scope="col" aria-sort={sortCol === 'dar_box_median' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Median Delivery Absorption Ratio inside the box. Higher = more institutional accumulation." showIcon={false}>
                        DAR (Box) <SortIcon column="dar_box_median" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('sar')} scope="col" aria-sort={sortCol === 'sar' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Squeeze Acceleration Ratio: last-3-day mean DAR / box median DAR. >1.0 means DAR is accelerating into the breakout." showIcon={false}>
                        SAR <SortIcon column="sar" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('sar_z')} scope="col" aria-sort={sortCol === 'sar_z' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Statistical significance of recent delivery acceleration: (last-3 mean − box mean) / box stddev. |z| > 1.0 is significant." showIcon={false}>
                        SAR_z <SortIcon column="sar_z" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('ftc')} scope="col" aria-sort={sortCol === 'ftc' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Float Turnover Compression: median(volume, last 5) / median(volume, box). <0.7 = quiet accumulation (good). >1.2 = noise." showIcon={false}>
                        FTC <SortIcon column="ftc" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('rs_mean')} scope="col" aria-sort={sortCol === 'rs_mean' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Relative Strength: mean daily stock-outperformance vs Nifty over the box period. Positive = outperforming." showIcon={false}>
                        RS <SortIcon column="rs_mean" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('breakout_dar')} scope="col" aria-sort={sortCol === 'breakout_dar' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Breakout DAR <SortIcon column="breakout_dar" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('am')} scope="col" aria-sort={sortCol === 'am' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Absorption Multiple: breakout day DAR / box median DAR. ≥ tier threshold = institutional conviction." showIcon={false}>
                        AM <SortIcon column="am" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('entry')} scope="col" aria-sort={sortCol === 'entry' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Entry price: 0.5% above the box ceiling on a confirmed breakout close above ceiling." good="Wait for a close above ceiling — do not chase intraday spikes." bad="Entry = null when box is not yet ready to trigger.">Entry <SortIcon column="entry" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('sl')} scope="col" aria-sort={sortCol === 'sl' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Stop Loss: 0.5% below the box floor. Exit immediately if price closes below this level." good="Tight SL = low risk. Large box = wider SL, position size accordingly." bad="SL = null when box is not yet ready to trigger.">SL <SortIcon column="sl" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('t1')} scope="col" aria-sort={sortCol === 't1' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Target 1: conservative profit target, typically 1:1 risk-reward from entry. Book partial position here." good="T1 hit = take 50% off the table, move SL to breakeven." bad="T1 = null when not enough data to project.">T1 <SortIcon column="t1" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('t2')} scope="col" aria-sort={sortCol === 't2' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Target 2: extended profit target, typically 2:1 risk-reward. Trail remaining position." good="T2 hit = trail with ATR or moving average." bad="T2 = null when not enough data to project.">T2 <SortIcon column="t2" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('status')} scope="col" aria-sort={sortCol === 'status' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Box lifecycle status. Only actionable statuses trigger entry signals."
                        good="In Box = forming, Breakout Pending = near ceiling, Triggered = entered"
                        bad="Failed Validation = DAR criteria not met, Invalidated = box broken, Low Volume = insufficient liquidity"
                      >
                        Status <SortIcon column="status" />
                      </Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('composite_score')} scope="col" aria-sort={sortCol === 'composite_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Score <SortIcon column="composite_score" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={22} className="px-4 py-8 text-center text-[#888]">No Darvas boxes match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-purple-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50"
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
                        <td className="px-3 py-3 text-right text-[#ccc]">
                          <span className={row.box_age_days < 7 ? 'text-yellow-400' : 'text-[#ccc]'}>
                            {row.box_age_days}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.box_range_pct.toFixed(1)}%</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">
                          {row.ceiling_price?.toFixed(2) ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">
                          {row.floor_price?.toFixed(2) ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.dist_to_ceiling_pct <= 0 ? 'text-green-400' :
                            row.dist_to_ceiling_pct <= 2 ? 'text-yellow-400' :
                            'text-[#888]'
                          }>
                            {row.dist_to_ceiling_pct > 0 ? `${row.dist_to_ceiling_pct.toFixed(1)}%` : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.touches_ceiling >= 3 ? 'text-green-400' : 'text-[#ccc]'
                          }>
                            {row.touches_ceiling}C / {row.touches_floor}F
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.dar_box_median.toFixed(2)}%</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.sar >= 1.15 ? 'text-green-400' : row.sar >= 1.0 ? 'text-yellow-400' : 'text-[#888]'}>
                            {row.sar.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.sar_z != null && row.sar_z > 1.0 ? 'text-green-400' : row.sar_z != null && row.sar_z < -1.0 ? 'text-red-400' : 'text-[#888]'}>
                            {row.sar_z != null ? row.sar_z.toFixed(2) : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.ftc != null && row.ftc < 0.7 ? 'text-green-400' : row.ftc != null && row.ftc > 1.2 ? 'text-red-400' : 'text-[#888]'}>
                            {row.ftc != null ? row.ftc.toFixed(2) : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.rs_mean != null && row.rs_mean > 0 ? 'text-green-400' : row.rs_mean != null && row.rs_mean < 0 ? 'text-red-400' : 'text-[#888]'}>
                            {row.rs_mean != null ? `${row.rs_mean.toFixed(2)}%` : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.breakout_dar.toFixed(2)}%</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.am >= 4 ? 'text-green-400 font-bold' : row.am >= 2 ? 'text-cyan-400' : 'text-[#aaa]'}>
                            {row.am.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-green-400 font-semibold">
                          {row.entry !== null ? row.entry.toFixed(2) : '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-red-400">
                          {row.sl !== null ? row.sl.toFixed(2) : '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-[#888]">
                          {row.t1 !== null ? row.t1.toFixed(2) : '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">
                          {row.t2 !== null ? row.t2.toFixed(2) : '—'}
                        </td>
                        <td className="px-3 py-3 text-center">
                          <div className="flex flex-col items-center gap-0.5">
                            <span className={`text-[12px] font-semibold ${STATUS_COLORS[row.status] || 'text-[#aaa]'}`}>
                              {row.status}
                            </span>
                            {row.status === 'Failed Validation' && row.failure_reason && (
                              <span className="text-[12px] text-[#888] font-mono" title={row.failure_reason}>
                                {row.failure_reason.length > 30 ? row.failure_reason.slice(0, 30) + '…' : row.failure_reason}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.composite_score.toFixed(0)} · {row.grade}
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
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
              aria-label="Export table as CSV"
            >
              <Download size={12} aria-hidden="true" />
              CSV
            </button>
          </div>
        </>
      )}

      {/* Empty state */}
      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Box size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to detect Darvas boxes with delivery absorption.</p>
            <p className="text-[12px]">Box breakouts validated by DAR with tiered thresholds per market-cap bucket.</p>
          </div>
        </div>
      )}
    </main>
  );
}
