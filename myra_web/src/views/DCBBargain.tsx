import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ChevronRight, ArrowUpDown, Star, Info, Target, Settings2 } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import FundTractionButton from '../components/FundTractionButton';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';

const TIER_COLORS: Record<string, string> = {
  HIGH: 'bg-green-500/20 text-green-400 border-green-500/30',
  MOD: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  LOW: 'bg-[#ffffff0a] text-[#888] border-[#ffffff1a]',
};

const DEPTH_COLORS: Record<string, string> = {
  DEEP: 'bg-red-500/20 text-red-400 border-red-500/30',
  MID: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  SHALLOW: 'bg-[#ffffff0a] text-[#888] border-[#ffffff1a]',
};

interface Candidate {
  symbol: string;
  sector?: string;
  close: number;
  dcb: number;
  discount_pct: number;
  depth?: string;
  del_abs: number;
  adtv_cr: number;
  high_del_days: number;
  free_float_mcap_cr: number;
  spike_deep?: boolean;
  is_lower_circuit?: boolean;
  circuit_days_last_5?: number;
  dcb_disc_min?: number | null;
  dcb_disc_median?: number | null;
  dcb_disc_max?: number | null;
  score: number;
  tier: string;
  tier_rank?: number;
  timeframe?: string;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
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

/** Offline fallback values — used when /api/dcb-bargain/defaults is unreachable. */
const ADVANCED_DEFAULTS = {
  min_discount_pct: 15,
  max_discount_pct: 60,
  min_del_abs: -2,
  min_adtv_cr: 1.0,
  min_high_del_days: 10,
  sanity_mult: 5,
  min_ff_mcap: 600,
};

const DEFAULTS_FALLBACK = {
  min_discount_pct: 15.0,
  max_discount_pct: 60.0,
  min_del_abs: -2.0,
  min_adtv_cr: 1.0,
  min_high_del_days: 10,
  sanity_mult: 5.0,
  min_ff_mcap: 600.0,
  exclude_circuits: true,
};

export default function DCBBargainView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [tierFilter, setTierFilter] = useState<string>('All');
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [scanDate, setScanDate] = useState('');
  const [timeframe, setTimeframe] = useState<'daily' | 'weekly'>('daily');

  const [dcbWindow, setDcbWindow] = useState(120);
  const [minDiscountPct, setMinDiscountPct] = useState(ADVANCED_DEFAULTS.min_discount_pct);
  const [maxDiscountPct, setMaxDiscountPct] = useState(ADVANCED_DEFAULTS.max_discount_pct);
  const [minDelAbs, setMinDelAbs] = useState(ADVANCED_DEFAULTS.min_del_abs);
  const [minAdtvCr, setMinAdtvCr] = useState(ADVANCED_DEFAULTS.min_adtv_cr);
  const [minHighDelDays, setMinHighDelDays] = useState(ADVANCED_DEFAULTS.min_high_del_days);
  const [sanityMult, setSanityMult] = useState(ADVANCED_DEFAULTS.sanity_mult);
  const [minFfMcap, setMinFfMcap] = useState(ADVANCED_DEFAULTS.min_ff_mcap);
  const [excludeCircuits, setExcludeCircuits] = useState(true);
  const [caExcludeEnabled, setCaExcludeEnabled] = useState(true);
  const [caExcludeDays, setCaExcludeDays] = useState(60);

  const [tractionWindow, setTractionWindow] = useState(1);
  const [tractionAggregation, setTractionAggregation] = useState<string>('latest');

  const [sortCol, setSortCol] = useState<string>('score');
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startScanRef = useRef<(() => void) | null>(null);

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
    if (tierFilter !== 'All') data = data.filter(d => d.tier === tierFilter);
    data.sort((a, b) => {
      const av = sortCol === 'tier' ? (a as any).tier_rank ?? 2 : (a as any)[sortCol] ?? 0;
      const bv = sortCol === 'tier' ? (b as any).tier_rank ?? 2 : (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, tierFilter, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-emerald-400" />
      : <ChevronDown size={10} className="inline ml-1 text-emerald-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/dcb-bargain/status`);
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
      const res = await fetch(`${API_BASE}/dcb-bargain/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_mcap: mcapRange?.min ?? 200,
          max_mcap: mcapRange?.max ?? 50000,
          dcb_window: dcbWindow,
          min_discount_pct: minDiscountPct,
          max_discount_pct: maxDiscountPct,
          min_del_abs: minDelAbs,
          min_adtv_cr: minAdtvCr,
          min_high_del_days: minHighDelDays,
          sanity_mult: sanityMult,
          timeframe,
          min_ff_mcap: minFfMcap,
          exclude_circuits: excludeCircuits,
          corporate_actions_exclude_days: caExcludeEnabled ? caExcludeDays : 0,
          min_traction_score: 30,
          traction_window: tractionWindow,
          traction_aggregation: tractionAggregation,
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
  }, [fetchScanStatus, clearPolling, mcapRange, scanDate, dcbWindow, minDiscountPct, maxDiscountPct, minDelAbs, minAdtvCr, minHighDelDays, sanityMult, timeframe, minFfMcap, excludeCircuits, caExcludeEnabled, caExcludeDays, tractionWindow, tractionAggregation]);
  startScanRef.current = startScan;

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    // Fetch backend defaults on first mount
    fetch(`${API_BASE}/dcb-bargain/defaults`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!mountedRef.current || !d) return;
        setMinDiscountPct(d.min_discount_pct ?? DEFAULTS_FALLBACK.min_discount_pct);
        setMaxDiscountPct(d.max_discount_pct ?? DEFAULTS_FALLBACK.max_discount_pct);
        setMinDelAbs(d.min_del_abs ?? DEFAULTS_FALLBACK.min_del_abs);
        setMinAdtvCr(d.min_adtv_cr ?? DEFAULTS_FALLBACK.min_adtv_cr);
        setMinHighDelDays(d.min_high_del_days ?? DEFAULTS_FALLBACK.min_high_del_days);
        setSanityMult(d.sanity_mult ?? DEFAULTS_FALLBACK.sanity_mult);
        setMinFfMcap(d.min_ff_mcap ?? DEFAULTS_FALLBACK.min_ff_mcap);
        setExcludeCircuits(d.exclude_circuits ?? DEFAULTS_FALLBACK.exclude_circuits);
        setCaExcludeDays(d.corporate_actions_exclude_days ?? 60);
        setTractionWindow(d.traction_window ?? 1);
        setTractionAggregation(d.traction_aggregation ?? 'latest');
      })
      .catch(() => {}); // keep current defaults on failure
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchScanStatus, clearPolling]);

  const isStale = scanStatus?.last_scan && (Date.now() - new Date(scanStatus.last_scan).getTime() > 30 * 60 * 1000);

  const clearCacheOnParamChange = useCallback(() => {
    if (scanStatus?.scan_status === 'completed') {
      fetch(`${API_BASE}/cache/dcb-bargain`, { method: 'DELETE' }).catch(() => {});
    }
  }, [scanStatus?.scan_status]);

  const resetAdvanced = () => {
    setMinDiscountPct(ADVANCED_DEFAULTS.min_discount_pct);
    setMaxDiscountPct(ADVANCED_DEFAULTS.max_discount_pct);
    setMinDelAbs(ADVANCED_DEFAULTS.min_del_abs);
    setMinAdtvCr(ADVANCED_DEFAULTS.min_adtv_cr);
    setMinHighDelDays(ADVANCED_DEFAULTS.min_high_del_days);
    setSanityMult(ADVANCED_DEFAULTS.sanity_mult);
    setMinFfMcap(ADVANCED_DEFAULTS.min_ff_mcap);
    setExcludeCircuits(true);
    setCaExcludeEnabled(true);
    setCaExcludeDays(60);
    setTractionWindow(1);
    setTractionAggregation('latest');
  };

  const handleCSV = () => {
    if (filteredData.length === 0) return;
    const headers = [
      'Symbol', 'Sector', 'Discount%', 'Depth', 'SpikeDeep', 'Circuit', 'CktDays', 'Close', 'DCB',
      'DelAbs', 'ADTV', 'HiDelDays', 'FF MCap Cr', 'DCBRange', 'Score', 'Tier', 'Timeframe',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '',
      r.discount_pct.toFixed(2), r.depth ?? '',
      r.spike_deep ? 'YES' : '',
      r.is_lower_circuit ? 'LOWER CKT' : '',
      r.circuit_days_last_5 ?? 0,
      r.close.toFixed(2), r.dcb.toFixed(2),
      r.del_abs.toFixed(2), r.adtv_cr.toFixed(2),
      r.high_del_days, r.free_float_mcap_cr.toFixed(2),
      r.dcb_disc_min != null && r.dcb_disc_median != null && r.dcb_disc_max != null
        ? `${r.dcb_disc_min.toFixed(1)}-${r.dcb_disc_median.toFixed(1)}-${r.dcb_disc_max.toFixed(1)}`
        : '',
      r.score.toFixed(0), r.tier, r.timeframe ?? 'daily',
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dcb_bargain_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="DCB Bargain">
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" aria-hidden="true" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="bg-emerald-500/10 border border-emerald-500/30 rounded px-4 py-3 flex items-start gap-2">
        <Info size={16} className="text-emerald-400 shrink-0 mt-0.5" aria-hidden="true" />
        <div className="text-xs font-mono text-emerald-300/90">
          <p className="mb-1">DCB Bargain Scanner: Finds stocks trading below their institutional Delivery Cost Basis — the weighted average price at which high‑delivery buyers accumulated over the last 6 months.</p>
          <p className="mb-1">Backtested: 1,101 signals across 15 dates (2022‑2024). TP=10%, SL=8%: 14 trades, 50% win rate, +6.6% net per trade, +₹9,264 total P&L. Deep discounts (&gt;20%) in large‑cap free‑float stocks averaged +17.6% (100% win rate).</p>
          <p>How to use: Prefer DEEP discounts (&gt;20%) with positive Delivery Absorption. The Discount% is your margin of safety. The DCB Range shows whether today's discount is historically deep or shallow for this specific stock. Spike+Deep is the highest‑conviction signal.</p>
          <p>⚠ Lower‑circuit stocks are excluded by default — their delivery data and DCB discount may be distorted by near‑zero volume. Toggle the filter in Advanced settings to include them.</p>
        </div>
      </div>

      {/* Color-coding legend */}
      <div className="flex flex-wrap gap-4 text-[12px] font-mono text-[#888] px-1" aria-label="Color legend: deep discount shown in green, moderate in yellow, shallow in white. Delivery absorption: green positive, yellow neutral, red negative. Score: green high, yellow medium.">
        <span aria-hidden="true">🟢</span> Deep discount (≥20%) <span aria-hidden="true">| 🟡</span> Moderate (10-20%) <span aria-hidden="true">| ⚪</span> Shallow (&lt;10%)
        <span aria-hidden="true">| DelAbs:</span> <span aria-hidden="true">🟢</span> ≥3% <span aria-hidden="true">| 🟡</span> ≥0% <span aria-hidden="true">| 🔴</span> &lt;0%
        <span aria-hidden="true">| Score:</span> <span aria-hidden="true">🟢</span> ≥20 <span aria-hidden="true">| 🟡</span> ≥10
      </div>

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-emerald-500/20 p-2 rounded" aria-hidden="true">
            <Target className="text-emerald-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">DCB Bargain Scanner</h1>
            <p className="text-xs font-mono text-[#888]">Delivery Cost Basis — Institutional Accumulation Discount</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/50"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
            ) : (
              <><Target size={14} fill="currentColor" aria-hidden="true" /> Scan</>
            )}
          </button>
          <button
            onClick={() => {
              fetch(`${API_BASE}/cache/dcb-bargain`, { method: 'DELETE' }).then(() => fetchScanStatus()).catch(() => {});
            }}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {/* Timeframe toggle */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1">
          <button onClick={() => { setTimeframe('daily'); clearCacheOnParamChange(); }}
            className={`px-2 py-1 text-[12px] rounded ${timeframe === 'daily' ? 'bg-blue-600 text-white' : 'bg-[#ffffff0a] text-[#888]'}`}>
            Daily
          </button>
          <button onClick={() => { setTimeframe('weekly'); clearCacheOnParamChange(); }}
            className={`px-2 py-1 text-[12px] rounded ${timeframe === 'weekly' ? 'bg-blue-600 text-white' : 'bg-[#ffffff0a] text-[#888]'}`}>
            Weekly
          </button>
        </div>
        {timeframe === 'weekly' && (
          <span className="text-[12px] font-mono text-[#888]">
            Weekly mode — aggregates daily candles to weekly. Delivery absorption still computed on daily price action.
          </span>
        )}
      </div>

      {/* DCB Parameters */}
      <section className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-col gap-4" aria-label="DCB Parameters">
        <div className="flex items-center gap-2 text-xs text-[#888]">
          <Filter size={14} aria-hidden="true" /> <span className="font-mono uppercase font-semibold">Parameters</span>
        </div>
        <div className="flex flex-wrap gap-4 items-end">
          <div className="max-w-[220px] flex-shrink-0">
            <MarketCapRangeFilter onChange={setMcapRange} />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="dcb-window" className="text-[12px] text-[#888] font-mono">DCB Window (days)</label>
            <input
              id="dcb-window"
              type="number"
              min={20}
              step={5}
              value={dcbWindow}
              onChange={e => { setDcbWindow(Number(e.target.value)); clearCacheOnParamChange(); }}
              className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 w-24"
              aria-label="DCB Window in days"
            />
          </div>
          <div className="flex items-center gap-1 mb-1 text-xs text-[#888]">
            <Star size={11} aria-hidden="true" />
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
            <div className="text-[12px] text-[#888] font-mono">Tier</div>
            <select
              value={tierFilter}
              onChange={e => setTierFilter(e.target.value)}
              className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-emerald-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-emerald-500/50"
            >
              {['All', 'HIGH', 'MOD', 'LOW'].map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[12px] text-[#888] font-mono" id="sector-filter-label">Sector</div>
            <select
              value={sectorFilter}
              onChange={e => setSectorFilter(e.target.value)}
              className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-emerald-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-emerald-500/50"
              aria-labelledby="sector-filter-label"
            >
              {availableSectors.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Advanced collapsible */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
          <button
            onClick={() => setAdvancedOpen(o => !o)}
            className="w-full flex items-center gap-2 px-4 py-2.5 text-xs font-mono text-[#888] hover:text-[#fafafa] transition-colors"
          >
            <Settings2 size={14} className="text-emerald-400" />
            <span className="font-semibold text-[#fafafa]">Advanced Parameters</span>
            <span className="text-[12px] text-[#888]">- discount range, delivery thresholds, free-float</span>
            <ChevronRight size={14} className={`ml-auto transition-transform ${advancedOpen ? 'rotate-90' : ''}`} />
          </button>
          {advancedOpen && (
            <div className="px-4 pb-4 grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
              <div className="flex flex-col gap-1">
                <label htmlFor="min-discount" className="text-[12px] text-[#888] font-mono">Min Discount %</label>
                <input
                  id="min-discount"
                  type="number"
                  min={0}
                  step={0.5}
                  value={minDiscountPct}
                  onChange={e => { setMinDiscountPct(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Minimum discount percentage"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="max-discount" className="text-[12px] text-[#888] font-mono">Max Discount %</label>
                <input
                  id="max-discount"
                  type="number"
                  min={10}
                  step={1}
                  value={maxDiscountPct}
                  onChange={e => { setMaxDiscountPct(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Maximum discount percentage"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="min-del-abs" className="text-[12px] text-[#888] font-mono">Min Delivery Absorption</label>
                <input
                  id="min-del-abs"
                  type="number"
                  min={-10}
                  step={0.5}
                  value={minDelAbs}
                  onChange={e => { setMinDelAbs(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Minimum delivery absorption"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="min-adtv" className="text-[12px] text-[#888] font-mono">Min ADTV (₹ Cr)</label>
                <input
                  id="min-adtv"
                  type="number"
                  min={0}
                  step={0.5}
                  value={minAdtvCr}
                  onChange={e => { setMinAdtvCr(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Minimum average daily traded value in crore rupees"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="min-hi-del-days" className="text-[12px] text-[#888] font-mono">Min High-Delivery Days</label>
                <input
                  id="min-hi-del-days"
                  type="number"
                  min={1}
                  step={1}
                  value={minHighDelDays}
                  onChange={e => { setMinHighDelDays(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Minimum high-delivery days"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="sanity-mult" className="text-[12px] text-[#888] font-mono">Sanity Multiplier</label>
                <input
                  id="sanity-mult"
                  type="number"
                  min={1}
                  step={0.5}
                  value={sanityMult}
                  onChange={e => { setSanityMult(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Sanity multiplier"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="min-ff-mcap" className="text-[12px] text-[#888] font-mono">Min FF MCap (₹ Cr)</label>
                <input
                  id="min-ff-mcap"
                  type="number"
                  min={0}
                  step={50}
                  value={minFfMcap}
                  onChange={e => { setMinFfMcap(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Minimum free-float market cap in crore rupees"
                />
              </div>
               <div className="flex flex-col gap-1 justify-end">
                <label className="flex items-center gap-2 text-[12px] text-[#888]">
                  <input
                    type="checkbox"
                    checked={excludeCircuits}
                    onChange={e => { setExcludeCircuits(e.target.checked); clearCacheOnParamChange(); }}
                    className="accent-emerald-500"
                  />
                  Exclude circuit‑locked stocks
                </label>
              </div>
              <div className="flex flex-col gap-1 justify-end">
                <label className="flex items-center gap-2 text-[12px] text-[#888]">
                  <input
                    type="checkbox"
                    checked={caExcludeEnabled}
                    onChange={e => { setCaExcludeEnabled(e.target.checked); clearCacheOnParamChange(); }}
                    className="accent-emerald-500"
                  />
                  Exclude recent corp. actions
                </label>
                {caExcludeEnabled && (
                  <input
                    type="number"
                    min={0}
                    max={365}
                    value={caExcludeDays}
                    onChange={e => { setCaExcludeDays(Number(e.target.value) || 60); clearCacheOnParamChange(); }}
                    className="w-20 px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-[12px] text-[#fafafa] font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                    title="Days to look back for bonus/split/rights"
                  />
                )}
                {caExcludeEnabled && <span className="text-[10px] text-[#666]">days</span>}
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="traction-window" className="text-[12px] text-[#888] font-mono">
                  <Tooltip content="Number of months of traction data to aggregate. Higher = smoother but delayed signal.">Traction Window</Tooltip>
                </label>
                <select
                  id="traction-window"
                  value={tractionWindow}
                  onChange={e => { setTractionWindow(Number(e.target.value)); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                >
                  {[1, 2, 3, 4, 6].map(n => (
                    <option key={n} value={n}>{n} month{n > 1 ? 's' : ''}</option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="traction-agg" className="text-[12px] text-[#888] font-mono">
                  <Tooltip content="How to combine traction scores across months. Max catches spikes, average smooths, momentum captures direction.">Traction Aggregation</Tooltip>
                </label>
                <select
                  id="traction-agg"
                  value={tractionAggregation}
                  onChange={e => { setTractionAggregation(e.target.value); clearCacheOnParamChange(); }}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-emerald-500 outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                >
                  <option value="latest">Latest</option>
                  <option value="max">Max</option>
                  <option value="average">Average</option>
                  <option value="momentum">Momentum</option>
                </select>
              </div>
              <div className="flex flex-col gap-1 justify-end">
                <button
                  onClick={resetAdvanced}
                  className="px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-[12px] text-[#888] hover:text-[#fafafa] transition-colors font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                  aria-label="Reset all advanced parameters to defaults"
                >
                  Reset defaults
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      {isScanning && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-emerald-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-emerald-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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
        <div className="flex items-center gap-2 px-3 py-1.5 rounded text-[12px] font-mono text-emerald-400 bg-emerald-500/5 border border-emerald-500/20">
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

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="text-[12px] font-mono text-[#888]">
            Results: <span className="text-emerald-400">{filteredData.length} candidates</span>
            <span className="ml-2 text-blue-400 capitalize">{timeframe}</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Discount%</div>
              <div className="text-2xl font-bold text-emerald-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.discount_pct, 0) / filteredData.length).toFixed(2) + '%'
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg DelAbs</div>
              <div className="text-2xl font-bold text-blue-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.del_abs, 0) / filteredData.length).toFixed(2)
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Spike+Deep</div>
              <div className="text-2xl font-bold text-green-400">
                {filteredData.filter(d => d.spike_deep).length}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Deep Count</div>
              <div className="text-2xl font-bold text-red-400">
                {filteredData.filter(d => d.depth === 'DEEP').length}
              </div>
            </div>
          </div>

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="DCB Bargain results"
                aria-rowcount={filteredData.length}
                aria-colcount={15}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-500/50" onClick={() => handleSort('sector')} scope="col" aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Sector <SortIcon column="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('discount_pct')} scope="col">
                      <Tooltip content="Discount % — how far current price is below the DCB. Higher = more margin of safety.">Discount% <SortIcon column="discount_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('depth')} scope="col">
                      <Tooltip content="Depth — DEEP (&gt;20%), MID (10-20%), SHALLOW (&lt;10%).">Depth <SortIcon column="depth" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('spike_deep')} scope="col">
                      <Tooltip content="Spike+Deep — today's delivery spike with deep discount. Highest-conviction signal.">Spike+Deep <SortIcon column="spike_deep" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('is_lower_circuit')} scope="col">
                      <Tooltip content="Lower circuit — close pinned at the low with a 5%+ drop. Delivery data may be distorted.">Circuit <SortIcon column="is_lower_circuit" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('circuit_days_last_5')} scope="col">
                      <Tooltip content="Circuit days in the last 5 sessions. ≥3 = sustained circuit lock.">Ckt Days <SortIcon column="circuit_days_last_5" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('close')} scope="col">
                      Close <SortIcon column="close" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('dcb')} scope="col">
                      <Tooltip content="Delivery Cost Basis — weighted average price at which high-delivery buyers accumulated.">DCB <SortIcon column="dcb" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('del_abs')} scope="col">
                      <Tooltip content="Delivery Absorption — net institutional buying pressure. Positive = institutions still accumulating.">DelAbs <SortIcon column="del_abs" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('adtv_cr')} scope="col">
                      <Tooltip content="Average daily traded value in ₹ Cr (last 20 days).">ADTV <SortIcon column="adtv_cr" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('free_float_mcap_cr')} scope="col">
                      <Tooltip content="Free-float market capitalization in ₹ Cr.">FF MCap <SortIcon column="free_float_mcap_cr" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('dcb_disc_median')} scope="col">
                      <Tooltip content="DCB Range (1Y) — historical min, median, max discount% over the past year.">DCB Range <SortIcon column="dcb_disc_median" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('score')} scope="col" aria-sort={sortCol === 'score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Composite score combining discount%, delivery absorption, and accumulation strength.">Score <SortIcon column="score" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('tier')} scope="col">
                      Tier <SortIcon column="tier" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={15} className="px-4 py-8 text-center text-[#888]">No candidates match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-emerald-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-500/50"
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
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.discount_pct >= 20 ? 'text-green-400' :
                            row.discount_pct >= 10 ? 'text-amber-400' :
                            'text-[#888]'
                          }>
                            {row.discount_pct.toFixed(2)}%
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${DEPTH_COLORS[row.depth ?? 'SHALLOW'] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.depth ?? '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          {row.spike_deep ? (
                            <span className="px-2 py-0.5 rounded text-[12px] font-bold border bg-green-500/20 text-green-400 border-green-500/30">YES</span>
                          ) : (
                            <span className="text-[#888]">—</span>
                          )}
                        </td>
                        <td className="px-3 py-3 text-center">
                          {row.is_lower_circuit ? (
                            <span className="px-2 py-0.5 rounded text-[12px] font-bold border bg-red-500/20 text-red-400 border-red-500/30">🔴 LOWER CKT</span>
                          ) : (
                            <span className="text-[#888]">—</span>
                          )}
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={row.circuit_days_last_5 >= 3 ? 'text-red-400' : (row.circuit_days_last_5 ?? 0) >= 1 ? 'text-amber-400' : 'text-[#888]'}>
                            {row.circuit_days_last_5 ?? 0}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{'\u20B9'}{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{'\u20B9'}{row.dcb.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.del_abs >= 3 ? 'text-green-400' :
                            row.del_abs >= 0 ? 'text-amber-400' :
                            'text-red-400'
                          }>
                            {row.del_abs.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.adtv_cr.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.free_float_mcap_cr.toFixed(2)}</td>
                        <td className="px-3 py-3 text-center">
                          {row.dcb_disc_min != null && row.dcb_disc_median != null && row.dcb_disc_max != null ? (() => {
                            const range = row.dcb_disc_max - row.dcb_disc_min;
                            const isNearMax = row.discount_pct >= row.dcb_disc_max - range * 0.25;
                            const isNearMedian = Math.abs(row.discount_pct - row.dcb_disc_median) < range * 0.25;
                            const cls = isNearMax ? 'text-green-400' : isNearMedian ? 'text-amber-400' : 'text-[#888]';
                            return (
                              <span className={cls} title={`Min: ${row.dcb_disc_min.toFixed(1)}% | Median: ${row.dcb_disc_median.toFixed(1)}% | Max: ${row.dcb_disc_max.toFixed(1)}%`}>
                                {row.dcb_disc_min.toFixed(1)}% – {row.dcb_disc_median.toFixed(1)}% – {row.dcb_disc_max.toFixed(1)}%
                              </span>
                            );
                          })() : (
                            <span className="text-[#888]">—</span>
                          )}
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.score >= 20 ? 'text-green-400' :
                            row.score >= 10 ? 'text-amber-400' :
                            'text-[#888]'
                          }>
                            {row.score.toFixed(0)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${TIER_COLORS[row.tier] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.tier}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
          <div className="flex justify-end gap-2">
            <FundTractionButton
              symbols={filteredData.map(c => c.symbol)}
              disabled={filteredData.length === 0}
            />
            <button
              onClick={handleCSV}
              disabled={filteredData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
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
            <Target size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to find stocks trading below their institutional Delivery Cost Basis.</p>
          </div>
        </div>
      )}
    </main>
  );
}
