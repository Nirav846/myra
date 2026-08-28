import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star, Zap, BookOpen, ChevronRight, Info } from 'lucide-react';
import FundTractionButton from '../components/FundTractionButton';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';
import ScrollableTable from '../components/ScrollableTable';

interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  float_util_pct: number;
  gate1_score: number;
  avg_down_del: number;
  seller_slope: number;
  gate2_score: number;
  vol_ratio_5_20: number;
  price_range_5d_pct: number;
  gate3_score: number;
  smart_float_ratio: number;
  defense_bars: number;
  base_duration: number;
  breakout_prox: number;
  trigger_score: number;
  grade: string;
  close: number;
  wk52_pos: number;
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

const STATUS_FILTERS = ['All', 'A', 'B', 'C', 'D'];

export default function TriggerScannerView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);
  const [guideOpen, setGuideOpen] = useState(false);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [minTriggerScoreFilter, setMinTriggerScoreFilter] = useState(0);
  const [minDefenseBarsFilter, setMinDefenseBarsFilter] = useState(0);
  const [minBaseDurationFilter, setMinBaseDurationFilter] = useState(0);
  const [minFloatUtilPct, setMinFloatUtilPct] = useState(8.0);
  const [volPinchRatio, setVolPinchRatio] = useState(0.72);
  const [minSmartFloatRatio, setMinSmartFloatRatio] = useState(0.55);
  const [priceRangeMax, setPriceRangeMax] = useState(10.0);
  const [gradeFilter, setGradeFilter] = useState<string>('All');

  const [scanDate, setScanDate] = useState('');

  const [sortCol, setSortCol] = useState<string>('trigger_score');
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
    if (minTriggerScoreFilter > 0) data = data.filter(d => d.trigger_score >= minTriggerScoreFilter);
    if (minDefenseBarsFilter > 0) data = data.filter(d => d.defense_bars >= minDefenseBarsFilter);
    if (minBaseDurationFilter > 0) data = data.filter(d => d.base_duration >= minBaseDurationFilter);
    if (gradeFilter !== 'All') data = data.filter(d => d.grade === gradeFilter);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, minTriggerScoreFilter, minDefenseBarsFilter, minBaseDurationFilter, gradeFilter, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-orange-400" />
      : <ChevronDown size={10} className="inline ml-1 text-orange-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/trigger/status`);
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
      const res = await fetch(`${API_BASE}/trigger/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_mcap: mcapRange?.min ?? 300,
          max_mcap: mcapRange?.max ?? 50000,
          min_float_util_pct: minFloatUtilPct,
          vol_pinch_ratio: volPinchRatio,
          price_range_max_pct: priceRangeMax,
          min_smart_float_ratio: minSmartFloatRatio,
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
  }, [fetchScanStatus, clearPolling, mcapRange, minFloatUtilPct, volPinchRatio, minSmartFloatRatio, priceRangeMax, scanDate]);

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
      'Symbol', 'Sector', 'Market Cap Cr', 'Float Util%', 'Gate 1', 'Avg Down Del%', 'Seller Slope', 'Gate 2', 'Vol Ratio', 'Range 5d%', 'Gate 3', 'Defense', 'Base Days', 'Prox%', 'Score', 'Grade', 'Close', '52W Pos%',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.market_cap_cr, r.float_util_pct,
      r.gate1_score, r.avg_down_del, r.seller_slope, r.gate2_score,
      r.vol_ratio_5_20, r.price_range_5d_pct, r.gate3_score, r.defense_bars,
      r.base_duration, r.breakout_prox, r.trigger_score, r.grade,
      r.close, r.wk52_pos,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trigger_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Trigger Scanner">
      {isStale && staleBannerOpen && (
        <div className="bg-orange-500/10 border border-orange-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-orange-400 shrink-0" aria-hidden="true" />
          <span className="text-orange-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-orange-500/50 hover:text-orange-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-orange-500/20 p-2 rounded" aria-hidden="true">
            <Zap className="text-orange-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">The Trigger</h1>
            <p className="text-xs font-mono text-[#888]">Three-gate system for precise accumulation timing</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-orange-600 hover:bg-orange-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400/50"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
            ) : (
              <><Zap size={14} fill="currentColor" aria-hidden="true" /> Scan</>
            )}
          </button>
          <button
            onClick={() => fetch(`${API_BASE}/cache/trigger`, { method: 'DELETE' })}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {/* ── TRIGGER 101 GUIDE ── */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
        <button
          onClick={() => setGuideOpen(o => !o)}
          className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-[#ffffff05] transition-colors"
        >
          <div className="flex items-center gap-2">
            <BookOpen size={14} className="text-orange-400" />
            <span className="text-sm font-semibold text-[#fafafa]">How does The Trigger work?</span>
            <span className="text-[12px] text-orange-400 bg-orange-500/15 border border-orange-500/30 px-2 py-0.5 rounded font-mono">
              NEW? START HERE
            </span>
          </div>
          <ChevronRight
            size={14}
            className={`text-[#888] transition-transform duration-200 ${guideOpen ? 'rotate-90' : ''}`}
          />
        </button>

        {guideOpen && (
          <div className="px-4 pb-4 border-t border-[#ffffff0a]">
            <p className="text-xs text-[#888] mt-3 mb-4 leading-relaxed max-w-3xl">
              The Trigger finds stocks at the <strong className="text-[#fafafa]">exact moment before they break out</strong> —
              not weeks away, but days. It uses a <strong className="text-orange-400">three-gate system</strong>:
              all three gates must pass simultaneously. One gate passing is noise.
              Two gates is interesting. All three together is a setup.
              Every stock in this list has passed all three.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
              {[
                {
                  gate: 'Gate 1',
                  title: 'Supply is Gone',
                  subtitle: 'Float Absorption',
                  color: 'text-orange-400',
                  border: 'border-orange-500/30',
                  bg: 'bg-orange-500/10',
                  metric: 'Float Util%',
                  what: '20-day cumulative delivery ÷ free float shares. Measures what fraction of available supply has physically changed hands.',
                  good: '>12% = meaningful supply consumed. >25% = float critically short. Sellers are running out of shares to sell.',
                },
                {
                  gate: 'Gate 2',
                  title: 'Sellers Are Giving Up',
                  subtitle: 'Seller Extinction',
                  color: 'text-red-400',
                  border: 'border-red-500/30',
                  bg: 'bg-red-500/10',
                  metric: 'Avg Down Del% + Slope',
                  what: 'On days when the stock falls, how much is being delivered by sellers? Falling delivery on down-days = sellers exhausted.',
                  good: 'Avg Down Del% < 38% means sellers barely showed up even on bad days. Negative slope = getting worse for sellers each time.',
                },
                {
                  gate: 'Gate 3',
                  title: 'Coil is Loaded',
                  subtitle: 'Volume Pinch',
                  color: 'text-blue-400',
                  border: 'border-blue-500/30',
                  bg: 'bg-blue-500/10',
                  metric: 'Vol Ratio + Price Range',
                  what: '5-day volume vs 20-day average (should be <72%) AND 5-day high-low range should be tight (<2.8%). The market is compressing.',
                  good: 'Low volume + tight range = nobody is selling, nobody is chasing. The coil is wound. Something has to give.',
                },
              ].map(item => (
                <div key={item.gate} className={`rounded border ${item.border} ${item.bg} p-3`}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-xs font-bold px-2 py-0.5 rounded border ${item.border} ${item.color} ${item.bg}`}>
                      {item.gate}
                    </span>
                    <span className={`text-sm font-bold ${item.color}`}>{item.title}</span>
                  </div>
                  <div className="text-[12px] text-[#ccc] font-semibold mb-1">{item.subtitle} · {item.metric}</div>
                  <p className="text-[12px] text-[#aaa] leading-relaxed mb-2">{item.what}</p>
                  <p className="text-[12px] text-[#888]"><strong className="text-[#ccc]">Pass condition:</strong> {item.good}</p>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="p-3 bg-[#0e1117] border border-orange-500/20 rounded text-[12px] text-[#888] leading-relaxed">
                <strong className="text-orange-400">Defense Bars</strong> — days when the stock opened lower
                but recovered strongly on high delivery. Someone defended the price. ≥3 bars = very strong floor.
              </div>
              <div className="p-3 bg-[#0e1117] border border-green-500/20 rounded text-[12px] text-[#888] leading-relaxed">
                <strong className="text-green-400">Breakout Prox%</strong> — how close to the top of the
                20-session base the price is right now. 80%+ = price is near the breakout point.
                Combine with high score for best setups.
              </div>
            </div>
          </div>
        )}
      </div>

      {isScanning && (
        <div className="bg-orange-500/10 border border-orange-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-orange-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-orange-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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
          <div className="text-[12px] text-[#888] font-mono">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-orange-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-orange-500/50"
            aria-label="Sector filter"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-32">
          <div className="text-[12px] text-[#888] font-mono" id="trigger-score-filter-label">Min Trigger Score</div>
          <select
            value={minTriggerScoreFilter}
            onChange={e => setMinTriggerScoreFilter(Number(e.target.value))}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-orange-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-orange-500/50"
            aria-labelledby="trigger-score-filter-label"
          >
            {[0, 30, 55, 75, 85].map(v => (
              <option key={v} value={v}>{v === 0 ? 'Any' : `${v}+`}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="text-[12px] text-[#888] font-mono">Min Defense Bars</div>
          <div className="flex gap-1">
            {[0, 1, 2, 3].map(n => (
              <button
                key={n}
                onClick={() => setMinDefenseBarsFilter(n)}
                className={`px-2 py-1 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 ${
                  minDefenseBarsFilter === n
                    ? 'bg-orange-500/20 border-orange-500/40 text-orange-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-[#aaa]'
                }`}
                aria-pressed={minDefenseBarsFilter === n}
              >
                {n === 0 ? 'Any' : `${n}+`}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Min Base Days</div>
          <div className="flex gap-1">
            {[0, 5, 10, 15].map(n => (
              <button
                key={n}
                onClick={() => setMinBaseDurationFilter(n)}
                className={`px-2 py-1 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 ${
                  minBaseDurationFilter === n
                    ? 'bg-orange-500/20 border-orange-500/40 text-orange-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-[#aaa]'
                }`}
                aria-pressed={minBaseDurationFilter === n}
              >
                {n === 0 ? 'Any' : `${n}+`}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Grade</div>
          <div className="flex gap-1">
            {['All', 'A', 'B', 'C', 'D'].map(g => (
              <button
                key={g}
                onClick={() => setGradeFilter(g)}
                className={`px-2 py-1 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 ${
                  gradeFilter === g
                    ? 'bg-orange-500/20 border-orange-500/40 text-orange-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-[#aaa]'
                }`}
                aria-pressed={gradeFilter === g}
              >
                {g}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[12px] text-[#888] font-mono">
            <span>Min Float Util%</span>
            <span className="text-orange-400">{minFloatUtilPct.toFixed(1)}%</span>
          </div>
          <input type="range" min={5.0} max={30.0} step={0.5} value={minFloatUtilPct}
            onChange={e => setMinFloatUtilPct(Number(e.target.value))}
            className="w-full accent-orange-500" />
        </div>
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[12px] text-[#888] font-mono">
            <span>Vol Pinch Ratio</span>
            <span className="text-orange-400">{volPinchRatio.toFixed(2)}</span>
          </div>
          <input type="range" min={0.50} max={1.00} step={0.01} value={volPinchRatio}
            onChange={e => setVolPinchRatio(Number(e.target.value))}
            className="w-full accent-orange-500" />
        </div>
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[12px] text-[#888] font-mono">
            <span>Min Smart Float Ratio</span>
            <span className="text-orange-400">{minSmartFloatRatio.toFixed(2)}</span>
          </div>
          <input type="range" min={0.40} max={0.70} step={0.01} value={minSmartFloatRatio}
            onChange={e => setMinSmartFloatRatio(Number(e.target.value))}
            className="w-full accent-orange-500" />
        </div>
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[12px] text-[#888] font-mono">
            <span>Max Price Range%</span>
            <span className="text-orange-400">{priceRangeMax.toFixed(1)}</span>
          </div>
          <input type="range" min={1.0} max={5.0} step={0.1} value={priceRangeMax}
            onChange={e => setPriceRangeMax(Number(e.target.value))}
            className="w-full accent-orange-500" />
        </div>
      </section>

      {/* ── PERFORMANCE STATS ── */}
      <details className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden group">
        <summary className="flex items-center gap-2 px-4 py-2.5 text-xs font-mono text-[#888] cursor-pointer hover:bg-[#ffffff05] transition-colors select-none list-none [&::-webkit-details-marker]:hidden">
          <span className="text-[12px] text-cyan-400 bg-cyan-500/10 border border-cyan-500/30 px-2 py-0.5 rounded font-bold">BACKTEST</span>
          <span className="font-semibold">Performance Stats</span>
          <span className="ml-auto text-[12px] opacity-50 group-open:rotate-180 transition-transform">▼</span>
        </summary>
        <div className="px-4 pb-4 border-t border-[#ffffff0a]">
          <p className="text-xs text-[#888] mt-3 mb-3 leading-relaxed font-mono">
            Based on 1‑year backtest (2,674 symbols, 363 trades):
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-3 text-center">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Win Rate</div>
              <div className="text-xl font-bold text-green-400">34.7%</div>
            </div>
            <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-3 text-center">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg +60d Return</div>
              <div className="text-xl font-bold text-red-400">–7.25%</div>
            </div>
            <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-3 text-center">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Median Return</div>
              <div className="text-xl font-bold text-red-400">–6.07%</div>
            </div>
            <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-3 text-center">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Best / Worst</div>
              <div className="text-xl font-bold text-[#fafafa]">+45.2% / –89.8%</div>
            </div>
          </div>
          <p className="text-[12px] text-[#888] font-mono italic">
            This is a screening tool — combine with fundamental analysis.
          </p>
        </div>
      </details>

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Tooltip content="Total stocks where all 3 gates passed simultaneously right now">
              <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 cursor-help">
                <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Triggers Ready</div>
                <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
              </div>
            </Tooltip>
            <Tooltip content="Trigger Score ≥75 — all three gates strong, plus meaningful defense bars and breakout proximity. Act on these first.">
              <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 cursor-help">
                <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Grade A</div>
                <div className="text-2xl font-bold text-orange-400">{filteredData.filter(d => d.grade === 'A').length}</div>
              </div>
            </Tooltip>
            <Tooltip content="Average Float Utilisation% across current results. Higher = more supply consumed from the market.">
              <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 cursor-help">
                <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Float Util%</div>
                <div className="text-2xl font-bold text-red-400">
                  {filteredData.length > 0
                    ? (filteredData.reduce((s, d) => s + (d.float_util_pct ?? 0), 0) / filteredData.length).toFixed(1) + '%'
                    : '—'}
                </div>
              </div>
            </Tooltip>
            <Tooltip content="Average base duration in sessions. Longer base = more patient, deliberate accumulation. >10 sessions is strong.">
              <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 cursor-help">
                <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Base Days</div>
                <div className="text-2xl font-bold text-cyan-400">
                  {filteredData.length > 0
                    ? (filteredData.reduce((s, d) => s + (d.base_duration ?? 0), 0) / filteredData.length).toFixed(0)
                    : '—'}
                </div>
              </div>
            </Tooltip>
          </div>

          {filteredData.filter(d => d.grade === 'A').length > 0 && (
            <div className="bg-orange-500/5 border border-orange-500/20 rounded p-3">
              <div className="text-[12px] text-orange-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Triggers</span>
                <span className="text-[#888]">— highest conviction setups passing all three gates</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.grade === 'A')
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-orange-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className="text-orange-400">Float {d.float_util_pct.toFixed(1)}%</span>
                      <span className="text-green-400">Base {d.base_duration}</span>
                      <span className="text-amber-400">Prox {((d.breakout_prox ?? 0) * 100).toFixed(0)}%</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Trigger Scanner results"
                aria-rowcount={filteredData.length}
                aria-colcount={14}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('symbol'); } }} onClick={() => handleSort('symbol')} scope="col">
                      Symbol <SortIcon column="symbol" />
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('sector'); } }} onClick={() => handleSort('sector')} scope="col">
                      Sector <SortIcon column="sector" />
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('market_cap_cr'); } }} onClick={() => handleSort('market_cap_cr')} scope="col">
                      MCap (₹Cr) <SortIcon column="market_cap_cr" />
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'float_util_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('float_util_pct'); } }} onClick={() => handleSort('float_util_pct')} scope="col">
                      <Tooltip content="Gate 1 — Float Utilisation. % of free float absorbed in last 20 days. >25% = critically short supply. Higher = more of the available stock has changed hands.">
                        Float Util% <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="float_util_pct" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'gate1_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('gate1_score'); } }} onClick={() => handleSort('gate1_score')} scope="col">
                      <Tooltip content="Gate 1 Score (0–100). Scales with Float Util%: 30 at 12%, 62 at 25%, 100 at 40%.">
                        G1 Score <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="gate1_score" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'avg_down_del' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('avg_down_del'); } }} onClick={() => handleSort('avg_down_del')} scope="col">
                      <Tooltip content="Gate 2 — Average delivery% on sessions when the stock fell >0.15%. Low = sellers barely participated even on bad days. <35% is exceptional.">
                        Down Del% <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="avg_down_del" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'gate2_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('gate2_score'); } }} onClick={() => handleSort('gate2_score')} scope="col">
                      <Tooltip content="Gate 2 Score — Seller Extinction strength. Combines how low down-day delivery is AND how fast it's declining. Higher = sellers more exhausted.">
                        G2 Score <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="gate2_score" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'gate3_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('gate3_score'); } }} onClick={() => handleSort('gate3_score')} scope="col">
                      <Tooltip content="Gate 3 Score — Volume Pinch strength. Combines volume dry-up (5d vs 20d) and price range compression. Higher = coil more tightly wound.">
                        G3 Score <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="gate3_score" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'defense_bars' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('defense_bars'); } }} onClick={() => handleSort('defense_bars')} scope="col">
                      <Tooltip content="Defense Bars — sessions where the stock opened lower but recovered strongly on high delivery. Each bar = a buyer defending the price. ≥3 bars = strong institutional floor.">
                        Defense <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="defense_bars" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'base_duration' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('base_duration'); } }} onClick={() => handleSort('base_duration')} scope="col">
                      <Tooltip content="How many consecutive recent sessions the stock has been in a tight base (daily H-L range < 3.5%). Longer = more patient accumulation underway.">
                        Base Days <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="base_duration" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'breakout_prox' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('breakout_prox'); } }} onClick={() => handleSort('breakout_prox')} scope="col">
                      <Tooltip content="Breakout Proximity — where price is within the 20-session base. 0% = at the base low. 100% = at the base high (breakout level). >70% = price approaching launch.">
                        Prox% <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="breakout_prox" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'trigger_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('trigger_score'); } }} onClick={() => handleSort('trigger_score')} scope="col">
                      <Tooltip content="Trigger Score (0–100) = G1(30%) + G2(25%) + G3(25%) + defense bonus + proximity bonus + base bonus. Grade A = 75+.">
                        Score <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="trigger_score" />
                      </Tooltip>
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'close' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('close'); } }} onClick={() => handleSort('close')} scope="col">
                      Price (₹) <SortIcon column="close" />
                    </th>

                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white select-none"
                        aria-sort={sortCol === 'wk52_pos' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('wk52_pos'); } }} onClick={() => handleSort('wk52_pos')} scope="col">
                      <Tooltip content="52-week position — 0% = at 52w low, 100% = at 52w high. Triggers ideally sit below 85% — still room to run.">
                        52W Pos% <Info size={10} className="inline mb-0.5 opacity-40" /> <SortIcon column="wk52_pos" />
                      </Tooltip>
                    </th>

                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={14} className="px-4 py-8 text-center text-[#888]">No triggers ready — all three gates must pass simultaneously.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-orange-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50"
                              aria-label={`Open chart for ${row.symbol}`}
                            >
                              {row.symbol}
                              <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" aria-hidden="true" />
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-3 text-[#888] text-[12px] max-w-[120px] truncate" title={row.sector ?? ''}>{row.sector ?? '—'}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.market_cap_cr.toFixed(0)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.float_util_pct >= 25 ? 'text-red-400' : row.float_util_pct >= 15 ? 'text-orange-400' : row.float_util_pct >= 12 ? 'text-amber-400' : 'text-[#888]'}>{row.float_util_pct.toFixed(1)}%</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.gate1_score >= 80 ? 'text-green-400' : row.gate1_score >= 60 ? 'text-orange-400' : row.gate1_score >= 40 ? 'text-amber-400' : 'text-[#888]'}>{row.gate1_score.toFixed(1)}</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.avg_down_del < 35 ? 'text-green-400' : row.avg_down_del < 45 ? 'text-amber-400' : 'text-[#888]'}>{row.avg_down_del.toFixed(1)}%</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.gate2_score >= 80 ? 'text-green-400' : row.gate2_score >= 60 ? 'text-cyan-400' : 'text-[#888]'}>{row.gate2_score.toFixed(1)}</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.gate3_score >= 80 ? 'text-green-400' : row.gate3_score >= 60 ? 'text-blue-400' : 'text-[#888]'}>{row.gate3_score.toFixed(1)}</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={(row.breakout_prox ?? 0) > 0.70 ? 'text-green-400' : (row.breakout_prox ?? 0) > 0.50 ? 'text-amber-400' : 'text-[#888]'}>{((row.breakout_prox ?? 0) * 100).toFixed(0)}%</span>
                        </td>
                        <td className="px-3 py-3 text-right font-mono">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.trigger_score.toFixed(0)} · {row.grade}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.base_duration}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.wk52_pos < 75 ? 'text-green-400' : row.wk52_pos < 88 ? 'text-amber-400' : 'text-[#888]'}>{row.wk52_pos.toFixed(1)}%</span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
          <div className="flex justify-end gap-2">
            <FundTractionButton symbols={filteredData.map(c => c.symbol)} disabled={filteredData.length === 0} />
            <button
              onClick={handleCSV}
              disabled={filteredData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
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
            <Zap size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to detect stocks ready for breakout.</p>
            <p className="text-[12px]">All three gates must pass: supply absorbed, sellers exhausted, volume pinched.</p>
          </div>
        </div>
      )}
    </main>
  );
}