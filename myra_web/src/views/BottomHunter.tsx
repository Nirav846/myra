import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ChevronRight, ArrowUpDown, Star, Info, Target, BookOpen, Building2 } from 'lucide-react';
import FundTractionButton from '../components/FundTractionButton';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
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

const SECTOR_MOM_COLORS: Record<string, string> = {
  TOP: 'bg-green-500/20 text-green-400 border-green-500/30',
  MID: 'bg-[#ffffff0a] text-amber-400 border-amber-500/30',
  BOTTOM: 'bg-red-500/20 text-red-400 border-red-500/30',
};

interface Candidate {
  symbol: string;
  sector?: string;
  sector_mom_tier?: string;
  quality_score?: number | null;
  close: number;
  market_cap_cr: number;
  delivery_absorption: number;
  pct_above_52w_low: number;
  adtv_cr: number;
  entry_signal: string;
  sl_price: number;
  sl_type: string;
  swing_low_20d: number;
  delivery_spike_conf?: boolean;
  timeframe?: string;
  score: number;
  tier: string;
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

export default function BottomHunterView({ lib }: { lib: Librarian }) {
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
  const [tierFilter, setTierFilter] = useState<string>('All');

  const [scanDate, setScanDate] = useState('');
  const [timeframe, setTimeframe] = useState<'daily' | 'weekly'>('daily');
  const [restrictToHoldings, setRestrictToHoldings] = useState(false);

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
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
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
      ? <ChevronUp size={10} className="inline ml-1 text-purple-400" />
      : <ChevronDown size={10} className="inline ml-1 text-purple-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/bottom-hunter/status`);
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
      const res = await fetch(`${API_BASE}/bottom-hunter/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_mcap: mcapRange?.min ?? 200,
          max_mcap: mcapRange?.max ?? 50000,
          timeframe,
          restrict_to_holdings: restrictToHoldings,
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
  }, [fetchScanStatus, clearPolling, mcapRange, scanDate, timeframe, restrictToHoldings]);
  startScanRef.current = startScan;

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
      'Symbol', 'Sector', 'Sector Mom', 'Quality Score', 'Close', 'Market Cap Cr', 'Delivery Absorption',
      '% Above 52W Low', 'Entry Signal', 'ADTV (₹ Cr)', 'SL Price', 'SL Type',
      'Swing Low 20d', 'Score', 'Tier', 'Spike Conf',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.sector_mom_tier ?? '', r.quality_score != null ? r.quality_score.toFixed(0) : '',
      r.close.toFixed(2), r.market_cap_cr.toFixed(0),
      r.delivery_absorption.toFixed(2), r.pct_above_52w_low.toFixed(2),
      `"${r.entry_signal ?? ''}"`, r.adtv_cr.toFixed(2),
      r.sl_price != null ? r.sl_price.toFixed(2) : '',
      `"${r.sl_type ?? ''}"`,
      r.swing_low_20d != null ? r.swing_low_20d.toFixed(2) : '',
      r.score.toFixed(0), r.tier,
      r.delivery_spike_conf === true ? 'YES' : '',
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `bottom_hunter_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Bottom Hunter">
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" aria-hidden="true" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="bg-blue-500/10 border border-blue-500/30 rounded px-4 py-3 flex items-start gap-2">
        <Info size={16} className="text-blue-400 shrink-0 mt-0.5" aria-hidden="true" />
        <div className="text-xs font-mono text-blue-300/90">
          <p className="mb-1">This screener identifies delivery-based accumulation near 52-week lows.</p>
          <p className="mb-1">Historical backtest: +57% net return over 6 months (173 observations, 2022-2024).</p>
          <p>The signal is strongest over a 3-9 month horizon. For multi-year investing, combine with fundamental analysis.</p>
        </div>
      </div>

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-blue-500/20 p-2 rounded" aria-hidden="true">
            <Target className="text-blue-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Bottom Hunter</h1>
            <p className="text-xs font-mono text-[#888]">Delivery Absorption Near 52-Week Lows</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
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
              fetch(`${API_BASE}/cache/bottom-hunter`, { method: 'DELETE' }).then(() => fetchScanStatus()).catch(() => {});
            }}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {/* How to read Bottom Hunter */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
        <button
          onClick={() => setGuideOpen(o => !o)}
          className="w-full flex items-center gap-2 px-4 py-2.5 text-xs font-mono text-[#888] hover:text-[#fafafa] transition-colors"
        >
          <BookOpen size={14} className="text-blue-400" />
          <span className="font-semibold text-[#fafafa]">How to use Bottom Hunter</span>
          <span className="text-[12px] text-[#888]">- A guide to delivery-absorption signals</span>
          <ChevronRight size={14} className={`ml-auto transition-transform ${guideOpen ? 'rotate-90' : ''}`} />
        </button>
        {guideOpen && (
          <div className="px-4 pb-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div>
              <h4 className="text-blue-400 font-semibold mb-1">How it works</h4>
              <p className="text-[#aaa] leading-relaxed">
                Bottom Hunter scans for <span className="text-[#fafafa]">delivery-based accumulation near 52-week lows</span> — the same "quiet institutional buying" theme as Invisible Hand, but the setup is a stock basing at or near its 52-week low while up-days absorb more delivery than down-days (delivery_absorption).
              </p>
              <ul className="space-y-1 text-[#aaa] mt-2">
                <li><span className="text-[#888] font-bold">Score / Tier</span> — percentile rank of delivery absorption; HIGH ≥80, MOD 50-80, LOW &lt;50.</li>
                <li><span className="text-[#888] font-bold">Entry Signal</span> — how close price is to the 52-week low (At &lt;5%, Near 5-10%, Above &gt;10%).</li>
                <li><span className="text-[#888] font-bold">SL Price</span> — stop-loss below the 20-day swing low minus 0.5×ATR (or the 52-week low if deeper).</li>
                <li><span className="text-[#888] font-bold">Spike Conf</span> (delivery_spike_conf) — TODAY's delivery ≥ 1.3× the 50-day average AND close in the upper 60% of the day's range. Confirmed entry-timing signal (backtest: +8.5% mean 40d @ 70% win rate).</li>
              </ul>
            </div>
            <div>
              <h4 className="text-cyan-400 font-semibold mb-1">Signals & context</h4>
              <ul className="space-y-1.5 text-[#aaa]">
                <li><span className="text-cyan-400 font-bold">Quality Score</span> — composite of net_margin (40%) + promoter holding (30%) + PE inverse (30%), percentile-ranked within the scan. ≥70 strong, 40-69 neutral, &lt;40 weak.</li>
                <li><span className="text-cyan-400 font-bold">Sector Mom</span> — 6-month sector momentum tier (TOP/MID/BOTTOM). Top-quintile-momentum sectors tend to outperform.</li>
                <li className="pt-1 text-[#888] border-t border-[#ffffff0a]">
                  The signal is strongest over a <span className="text-[#fafafa] font-semibold">3-9 month horizon</span>; combine with fundamental analysis for multi-year holds. Historical backtest: +57% net return over 6 months (173 observations, 2022-2024).
                </li>
              </ul>
            </div>
          </div>
        )}
      </div>

      {isScanning && (
        <div className="bg-blue-500/10 border border-blue-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-blue-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-blue-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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
        <div className="flex items-center gap-2 px-3 py-1.5 rounded text-[12px] font-mono text-blue-400 bg-blue-500/5 border border-blue-500/20">
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
          <div className="text-[12px] text-[#888] font-mono">Holdings</div>
          <button
            onClick={() => setRestrictToHoldings(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${
              restrictToHoldings
                ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400'
                : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-emerald-400'
            }`}
            aria-label={restrictToHoldings ? 'Show all symbols' : 'Restrict to stocks held by mutual funds'}
            aria-pressed={restrictToHoldings}
          >
            <Building2 size={11} aria-hidden="true" />
            MF-held only
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Tier</div>
          <select
            value={tierFilter}
            onChange={e => setTierFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-blue-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-blue-500/50"
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
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-blue-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-blue-500/50"
            aria-labelledby="sector-filter-label"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </section>

      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1">
          <button onClick={() => setTimeframe('daily')}
            className={`px-2 py-1 text-[12px] rounded ${timeframe === 'daily' ? 'bg-blue-600 text-white' : 'bg-[#ffffff0a] text-[#888]'}`}>
            Daily
          </button>
          <button onClick={() => setTimeframe('weekly')}
            className={`px-2 py-1 text-[12px] rounded ${timeframe === 'weekly' ? 'bg-blue-600 text-white' : 'bg-[#ffffff0a] text-[#888]'}`}>
            Weekly
          </button>
        </div>
        {timeframe === 'weekly' && (
          <span className="text-[12px] font-mono text-[#888]">
            Weekly mode — uses 8-week delivery absorption window. Experimental: based on limited backtest (8 trades). For long-term screening.
          </span>
        )}
      </div>

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="text-[12px] font-mono text-[#888]">
            Results: <span className="text-blue-400 capitalize">{timeframe}</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">High Tier</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.tier === 'HIGH').length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Absorption</div>
              <div className="text-2xl font-bold text-blue-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.delivery_absorption, 0) / filteredData.length).toFixed(2)
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Score</div>
              <div className="text-2xl font-bold text-purple-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.score, 0) / filteredData.length).toFixed(0)
                  : '—'}
              </div>
            </div>
          </div>

          {filteredData.filter(d => d.tier === 'HIGH').length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[12px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>High Tier Candidates</span>
                <span className="text-[#888]">— top 20% by delivery absorption percentile</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.tier === 'HIGH')
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-green-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className="text-green-400">{d.delivery_absorption.toFixed(2)}</span>
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
                aria-label="Bottom Hunter results"
                aria-rowcount={filteredData.length}
                aria-colcount={16}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50" onClick={() => handleSort('sector')} scope="col" aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Sector <SortIcon column="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50" onClick={() => handleSort('sector_mom_tier')} scope="col" aria-sort={sortCol === 'sector_mom_tier' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Sector Mom <SortIcon column="sector_mom_tier" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('quality_score')} scope="col">
                      <Tooltip content="Three-factor quality score (0-100): net margin (40%), promoter holding (30%), earnings yield 1/PE (30%). Cross-sectionally ranked within the scan universe.">Quality Score <SortIcon column="quality_score" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('close')} scope="col">
                      Close <SortIcon column="close" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50" onClick={() => handleSort('market_cap_cr')} scope="col" aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      MCap (₹ Cr) <SortIcon column="market_cap_cr" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50" onClick={() => handleSort('delivery_absorption')} scope="col" aria-sort={sortCol === 'delivery_absorption' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Delivery absorption: average delivery% on up days minus average delivery% on down days (last 20 days).">Delivery Absorption <SortIcon column="delivery_absorption" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('pct_above_52w_low')} scope="col">
                      <Tooltip content="Percentage above 52-week low.">% Above 52W Low <SortIcon column="pct_above_52w_low" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('entry_signal')} scope="col">
                      <Tooltip content="Entry proximity to 52-week low: at (<5%), near (5-10%), or above (>10%).">Entry Signal <SortIcon column="entry_signal" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('adtv_cr')} scope="col">
                      <Tooltip content="Average daily turnover in ₹ Cr (last 20 days).">ADTV (₹ Cr) <SortIcon column="adtv_cr" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('sl_price')} scope="col">
                      <Tooltip content="Stop-loss: below 20-day swing low minus ATR (or below 52-week low).">SL Price <SortIcon column="sl_price" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('sl_type')} scope="col">
                      <Tooltip content="Stop-loss basis: below 20-day swing low or below 52-week low.">SL Type <SortIcon column="sl_type" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('swing_low_20d')} scope="col">
                      <Tooltip content="20-day swing low: base level used for the stop-loss calculation.">Swing Low 20d <SortIcon column="swing_low_20d" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50" onClick={() => handleSort('score')} scope="col" aria-sort={sortCol === 'score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Percentile rank of delivery absorption among candidates (0-100).">Score <SortIcon column="score" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('tier')} scope="col">
                      Tier <SortIcon column="tier" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('delivery_spike_conf')} scope="col">
                      Spike Conf <SortIcon column="delivery_spike_conf" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={16} className="px-4 py-8 text-center text-[#888]">No candidates match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-blue-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/50"
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
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${SECTOR_MOM_COLORS[row.sector_mom_tier ?? ''] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.sector_mom_tier ?? '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          {row.quality_score != null ? (
                            <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${
                              row.quality_score >= 70 ? 'bg-green-500/20 text-green-400 border-green-500/30' :
                              row.quality_score >= 40 ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                              'bg-red-500/20 text-red-400 border-red-500/30'
                            }`}>
                              {row.quality_score.toFixed(0)}
                            </span>
                          ) : '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.market_cap_cr.toFixed(0)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.delivery_absorption > 10 ? 'text-green-400' :
                            row.delivery_absorption > 5 ? 'text-yellow-400' :
                            'text-[#888]'
                          }>
                            {row.delivery_absorption.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.pct_above_52w_low < 20 ? 'text-green-400' :
                            row.pct_above_52w_low < 50 ? 'text-yellow-400' :
                            'text-[#888]'
                          }>
                            {row.pct_above_52w_low.toFixed(2)}%
                          </span>
                        </td>
                        <td className="px-3 py-3 text-[#ccc] text-[12px]">{row.entry_signal ?? '—'}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.adtv_cr.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.sl_price != null ? row.sl_price.toFixed(2) : '—'}</td>
                        <td className="px-3 py-3 text-[#888] text-[12px]">{row.sl_type ?? '—'}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.swing_low_20d != null ? row.swing_low_20d.toFixed(2) : '—'}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.score >= 80 ? 'text-green-400' :
                            row.score >= 50 ? 'text-yellow-400' :
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
                        <td className="px-3 py-3 text-center">
                          {row.delivery_spike_conf === true ? (
                            <span className="px-2 py-0.5 rounded text-[12px] font-bold border bg-green-500/10 text-green-400 border-green-500/30">
                              YES
                            </span>
                          ) : (
                            <span className="text-[#888]">—</span>
                          )}
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
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/50"
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
            <p>Click Scan to find stocks with strong delivery absorption near 52-week lows.</p>
          </div>
        </div>
      )}
    </main>
  );
}
