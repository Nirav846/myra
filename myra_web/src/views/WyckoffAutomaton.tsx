import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star, BookOpen, ChevronRight, Info } from 'lucide-react';
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
  spring_score?: number;
  grade?: string;
  lower_wick_ratio?: number;
  close_location?: number;
  grab_depth_pct?: number;
  equal_low_zone?: boolean;
  two_candle_confirm?: boolean;
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

const EVENT_META: Record<string, { label: string; desc: string; color: string }> = {
  SC: {
    label: 'SC',
    desc: 'Supply Climax — a massive down-bar on huge volume. The last wave of selling from weak hands. The smart money starts buying here.',
    color: 'bg-red-500/20 text-red-400 border-red-500/30',
  },
  AR: {
    label: 'AR',
    desc: 'Automatic Rally — a bounce after SC as selling pressure briefly lifts. Low volume is good here (no real buying yet).',
    color: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  },
  ST: {
    label: 'ST',
    desc: 'Secondary Test — price revisits SC low to see if selling returns. LOW delivery confirms sellers are gone. The most important test.',
    color: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  },
  Spring: {
    label: 'Spring',
    desc: 'Spring — price briefly breaks below SC low then snaps back. A false breakdown that traps late sellers. Often marks Phase C / the final low.',
    color: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  },
  SOS: {
    label: 'SOS',
    desc: 'Sign of Strength — a strong up-bar on high volume above resistance. Confirms accumulation worked. Marks the start of Phase D / mark-up.',
    color: 'bg-green-500/20 text-green-400 border-green-500/30',
  },
};

const PHASE_META: Record<string, { label: string; desc: string; color: string }> = {
  'Phase A': {
    label: 'Phase A',
    desc: 'Supply — the prior downtrend ends with SC (selling climax). AR bounces, ST confirms supply is gone. Smart money begins accumulating.',
    color: 'bg-[#ffffff1a] text-[#888] border-[#ffffff1a]',
  },
  'Phase B': {
    label: 'Phase B',
    desc: 'Accumulation — price moves sideways in a range. Smart money accumulates patiently. Weak hands get shaken out. This is the longest phase.',
    color: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  },
  'Phase C': {
    label: 'Phase C',
    desc: 'Spring / Final Low — a false breakdown below the range that immediately reverses. Traps the last sellers. The final shakeout before mark-up.',
    color: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  },
  'Phase D': {
    label: 'Phase D',
    desc: 'Mark-up — price breaks out of the range on volume. SOS confirms accumulation. This is the explosive phase. The smart money now profits.',
    color: 'bg-green-500/20 text-green-400 border-green-500/30 shadow-[0_0_8px_rgba(34,197,94,0.3)]',
  },
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

function springScoreColor(score: number | undefined): string {
  if (score === undefined || score === null) return 'text-[#888]';
  if (score >= 65) return 'text-green-400';
  if (score >= 50) return 'text-amber-400';
  return 'text-[#888]';
}

function springGradeBadge(grade: string | undefined): { label: string; color: string } {
  if (grade === 'A+') return { label: 'A+', color: 'bg-green-500/20 text-green-400 border-green-500/30' };
  if (grade === 'B') return { label: 'B', color: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };
  if (grade === 'C') return { label: 'C', color: 'bg-[#ffffff1a] text-[#888] border-[#ffffff1a]' };
  return { label: '—', color: 'bg-[#ffffff0a] text-[#888] border-[#ffffff1a]' };
}

const EVENT_TYPES = ['All', 'SC', 'AR', 'ST', 'Spring', 'SOS'];
const PHASE_TYPES = ['All', 'Phase A', 'Phase B', 'Phase C', 'Phase D'];

export default function WyckoffAutomatonView({ lib }: { lib: Librarian }) {
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
  const [eventFilter, setEventFilter] = useState<string>('All');
  const [phaseFilter, setPhaseFilter] = useState<string>('All');
  const [minQualityFilter, setMinQualityFilter] = useState(0);
  const [maxDaysFilter, setMaxDaysFilter] = useState<number>(30);

  const [scanDate, setScanDate] = useState('');

  const [sortCol, setSortCol] = useState<string>('phase_complete_pct');
  const [sortAsc, setSortAsc] = useState(false);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/wyckoff/status`);
      if (!mountedRef.current) return;
      if (!res.ok) return;
      const data: ScanStatus = await res.json();
      if (!mountedRef.current) return;
      setScanStatus(data);
      setError(null);

      if (data.scan_status === 'completed' || data.scan_status === 'error') {
        clearPolling();
        setIsScanning(false);
      } else if (data.scan_status === 'scanning' && !pollTimerRef.current) {
        pollTimerRef.current = setInterval(fetchStatus, 2000);
        setIsScanning(true);
      }
    } catch {
      // ignore
    }
  }, [clearPolling]);

  useEffect(() => {
    mountedRef.current = true;
    fetchStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchStatus, clearPolling]);

  const triggerScan = useCallback(async () => {
    if (!mountedRef.current) return;
    setIsScanning(true);
    setError(null);
    clearPolling();

    try {
      const body: Record<string, unknown> = {};
      if (mcapRange) {
        body.min_mcap = mcapRange.min;
        body.max_mcap = mcapRange.max;
      }
      if (scanDate.trim()) {
        body.scan_date = scanDate;
      }
      const res = await fetch(`${API_BASE}/wyckoff/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!mountedRef.current) return;
      if (!res.ok) {
        const errData = await res.json().catch(() => ({ detail: 'Scan failed' }));
        setError(errData.detail || 'Scan failed');
        setIsScanning(false);
      } else {
        await fetchStatus();
        pollTimerRef.current = setInterval(fetchStatus, 2000);
      }
    } catch (e: unknown) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : 'Network error');
        setIsScanning(false);
      }
    }
  }, [mcapRange, fetchStatus, clearPolling, scanDate]);

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

  const candidates = scanStatus?.candidates ?? [];

  const sectors = useMemo(() => {
    const set = new Set<string>();
    candidates.forEach((c) => {
      if (c.sector) set.add(c.sector);
    });
    return ['All', ...Array.from(set).sort()];
  }, [candidates]);

  const filteredData = useMemo(() => {
    let data = candidates;
    if (mcapRange) {
      const map = mcapMapRef.current;
      data = data.filter(c => {
        const mcap = map.get(c.symbol);
        return mcap !== undefined && mcap >= mcapRange.min && mcap <= mcapRange.max;
      });
    }
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
  }, [candidates, mcapRange, watchlistOnly, isWatched, sectorFilter, eventFilter, phaseFilter, minQualityFilter, maxDaysFilter, sortCol, sortAsc]);

  const stats = useMemo(() => {
    return {
      total: candidates.length,
      avgQuality: candidates.length ? (candidates.reduce((s, c) => s + c.event_quality, 0) / candidates.length) : 0,
      phaseD: candidates.filter((c) => c.phase === 'Phase D').length,
      springCount: candidates.filter((c) => c.wyckoff_event === 'Spring').length,
      recent: candidates.filter((c) => c.days_since_event <= 5).length,
    };
  }, [candidates]);

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
    return sortAsc ? <ChevronUp size={12} className="inline ml-1 text-purple-400" /> : <ChevronDown size={12} className="inline ml-1 text-purple-400" />;
  }

  const exportCSV = () => {
    const data = filteredData;
    if (!data.length) return;
    const headers = ['Symbol','Sector','Spring Score','Grade','Close','Event','Phase','Lower Wick %','Close Location %','Grab Depth %','Equal Low','2-Candle','MCap Cr','Phase%','Event Date','Days Ago','Del%','Vol Ratio','Range Low','Range High','Quality'];
    const rows = data.map((c) => [
      c.symbol, c.sector ?? '', c.spring_score ?? '', c.grade ?? '',
      c.close, c.wyckoff_event, c.phase,
      c.lower_wick_ratio != null ? (c.lower_wick_ratio * 100).toFixed(0) : '',
      c.close_location != null ? (c.close_location * 100).toFixed(0) : '',
      c.grab_depth_pct ?? '',
      c.equal_low_zone ? 'YES' : '',
      c.two_candle_confirm ? 'YES' : '',
      c.market_cap_cr, c.phase_complete_pct, c.event_date, c.days_since_event,
      c.event_delivery_pct, c.vol_ratio, c.range_low_90, c.range_high_90,
      c.event_quality,
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

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  const gradeA = useMemo(() => filteredData.filter(d => d.event_quality >= 75), [filteredData]);

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Wyckoff Automaton">
      {/* Stale Banner */}
      {isStale && staleBannerOpen && scanStatus?.last_scan && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300" aria-label="Dismiss stale warning">
            <XCircle size={14} />
          </button>
        </div>
      )}

      {/* Spring Score Info Banner */}
      <div className="bg-blue-500/10 border border-blue-500/30 rounded px-4 py-3 flex items-start gap-2">
        <Info size={14} className="text-blue-400 shrink-0 mt-0.5" />
        <div className="text-xs font-mono text-blue-300/90 space-y-1">
          <p>Spring Score: Composite 0‑100 based on delivery absorption (30%), lower wick ratio (30%), close location (20%), grab depth (10%), and equal‑low bonus (10%). A+ ≥ 65, B ≥ 50, C ≥ 35.</p>
          <p>Spring events with delivery absorption &gt; 5% on the grab candle indicate institutional accumulation.</p>
        </div>
      </div>

      {/* Header */}
      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-purple-500/20 p-2 rounded">
            <Box className="text-purple-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Wyckoff Automaton</h1>
            <p className="text-xs font-mono text-[#888]">Smart Money — Accumulation Phase Detection</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          {isScanning && (
            <span className="flex items-center gap-1 text-xs text-yellow-400">
              <RefreshCw size={14} className="animate-spin" /> Scanning...
            </span>
          )}
          <button
            onClick={triggerScan}
            disabled={isScanning}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" /> Scanning...</>
            ) : (
              <><Box size={14} fill="currentColor" /> Scan</>
            )}
          </button>
          <button
            onClick={() => fetch(`${API_BASE}/cache/wyckoff`, { method: 'DELETE' })}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {/* Wyckoff 101 Guide */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
        <button
          onClick={() => setGuideOpen(o => !o)}
          className="w-full flex items-center gap-2 px-4 py-2.5 text-xs font-mono text-[#888] hover:text-[#fafafa] transition-colors"
        >
          <BookOpen size={14} className="text-purple-400" />
          <span className="font-semibold text-[#fafafa]">Wyckoff 101</span>
          <span className="text-[12px] text-[#888]">— A beginner's guide to Wyckoff Accumulation</span>
          <ChevronRight size={14} className={`ml-auto transition-transform ${guideOpen ? 'rotate-90' : ''}`} />
        </button>
        {guideOpen && (
          <div className="px-4 pb-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div>
              <h4 className="text-purple-400 font-semibold mb-1">The Wyckoff Method</h4>
              <p className="text-[#aaa] leading-relaxed">
                Wyckoff is a 100-year-old methodology that reads institutional accumulation through price, volume, and delivery.
                The core idea: <span className="text-[#fafafa]">smart money accumulates while retail sells in panic</span>, then marks up when accumulation is complete.
              </p>
              <h4 className="text-purple-400 font-semibold mt-3 mb-1">The Four Phases</h4>
              <ul className="space-y-1 text-[#aaa]">
                <li><span className="text-[#888] font-bold">Phase A:</span> Downtrend stops. Selling climax (SC) on massive volume. Automatic rally (AR) bounces. Secondary test (ST) confirms sellers are gone.</li>
                <li><span className="text-blue-400 font-bold">Phase B:</span> Accumulation range. Price moves sideways for weeks. Smart money buys patiently. Volume & delivery gradually increase at support.</li>
                <li><span className="text-amber-400 font-bold">Phase C:</span> Spring — a fake breakdown below the range that reverses fast. Traps the last remaining sellers. This is often the final low before the breakout.</li>
                <li><span className="text-green-400 font-bold">Phase D:</span> Mark-up! SOS (Sign of Strength) on volume confirms accumulation is done. Price breaks out of the range. The explosive move begins.</li>
              </ul>
            </div>
            <div>
              <h4 className="text-purple-400 font-semibold mb-1">Wyckoff Events (the building blocks)</h4>
              <div className="space-y-1.5">
                <p><span className="text-red-400 font-bold">SC</span> — <span className="text-[#aaa]">Supply Climax. Huge down-bar, massive volume. The last of the selling. Smart money starts buying.</span></p>
                <p><span className="text-yellow-400 font-bold">AR</span> — <span className="text-[#aaa]">Automatic Rally. Bounces after SC. Low volume = good (no real demand yet, just short-covering).</span></p>
                <p><span className="text-amber-400 font-bold">ST</span> — <span className="text-[#aaa]">Secondary Test. Revisits SC low. Low delivery on ST = sellers are truly gone. The most important confirmation.</span></p>
                <p><span className="text-purple-400 font-bold">Spring</span> — <span className="text-[#aaa]">False breakdown below SC low. Snaps back fast. Traps sellers. Often ends Phase C.</span></p>
                <p><span className="text-green-400 font-bold">SOS</span> — <span className="text-[#aaa]">Sign of Strength. Strong up-bar through resistance on high volume. Confirms accumulation worked. Begins Phase D.</span></p>
              </div>
              <h4 className="text-purple-400 font-semibold mt-3 mb-1">What the Quality Score means</h4>
              <p className="text-[#aaa] leading-relaxed">
                Each event gets a <span className="text-[#fafafa]">quality score (0–100)</span> based on delivery, volume, and price behavior.
                <span className="text-green-400"> Grade A (75+)</span> = textbook event. <span className="text-blue-400">B (55–74)</span> = good. <span className="text-amber-400">C (40–54)</span> = weak. <span className="text-red-400">D (&lt;40)</span> = unreliable.
              </p>
              <p className="text-[#aaa] leading-relaxed mt-1">
                The <span className="text-[#fafafa]">Spring Score</span> is separate — it specifically grades Spring (Phase C) setups on a 0–100 composite scale.
                <span className="text-green-400"> A+ (≥65)</span> = strong institutional absorption. <span className="text-amber-400">B (≥50)</span> = moderate. <span className="text-[#888]">C (≥35)</span> = marginal.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {error && !isScanning && (
        <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono text-red-300" role="alert">
          <AlertTriangle size={14} className="shrink-0" />
          <span>Error: {error}</span>
        </div>
      )}

      {/* Progress Bar */}
      {isScanning && (
        <div className="bg-purple-500/10 border border-purple-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-purple-300 mb-2">
            <RefreshCw size={14} className="animate-spin" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-purple-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
          </div>
        </div>
      )}

      {/* Status Banner */}
      {!isScanning && scanStatus && scanStatus.scan_status !== 'idle' && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded text-xs font-mono border ${
          scanStatus.scan_status === 'completed' ? 'bg-green-500/10 border-green-500/30 text-green-300' :
          scanStatus.scan_status === 'error' ? 'bg-red-500/10 border-red-500/30 text-red-300' :
          'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
        }`} role="status" aria-live="polite">
          {scanStatus.scan_status === 'completed' ? <CheckCircle size={14} className="text-green-400" /> :
           scanStatus.scan_status === 'error' ? <XCircle size={14} className="text-red-400" /> :
           <Clock size={14} />}
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

      {/* Filters */}
      <section className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end" aria-label="Filters">
        <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
          <Filter size={14} /> <span className="font-mono uppercase font-semibold">Filters</span>
        </div>
        <div className="max-w-[220px] flex-shrink-0">
          <MarketCapRangeFilter onChange={setMcapRange} />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Watchlist</div>
          <button
            onClick={() => setWatchlistOnly(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors ${
              watchlistOnly
                ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-yellow-400'
            }`}
            aria-label={watchlistOnly ? 'Show all symbols' : 'Filter to starred watchlist only'}
            aria-pressed={watchlistOnly}
          >
            <Star size={11} fill={watchlistOnly ? 'currentColor' : 'none'} />
            Only Starred
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Event</div>
          <select className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono" value={eventFilter} onChange={(e) => setEventFilter(e.target.value)}>
            {EVENT_TYPES.map((t) => <option key={t} value={t}>{t === 'All' ? 'All Events' : t + ' — ' + (EVENT_META[t]?.desc ?? '').slice(0, 50) + '...'}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Phase</div>
          <select className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono" value={phaseFilter} onChange={(e) => setPhaseFilter(e.target.value)}>
            {PHASE_TYPES.map((t) => <option key={t} value={t}>{t === 'All' ? 'All Phases' : t}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Sector</div>
          <select className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono" value={sectorFilter} onChange={(e) => setSectorFilter(e.target.value)}>
            {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-24">
          <div className="text-[12px] text-[#888] font-mono">Max Days</div>
          <select className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono" value={maxDaysFilter} onChange={(e) => setMaxDaysFilter(Number(e.target.value))}>
            <option value={999}>All Days</option>
            <option value={5}>≤ 5 Days</option>
            <option value={10}>≤ 10 Days</option>
            <option value={20}>≤ 20 Days</option>
            <option value={30}>≤ 30 Days</option>
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum event quality score. Higher = more textbook Wyckoff event. Grade A (75+) = near-perfect delivery/volume setup.">
              <span>Min Quality</span>
            </Tooltip>
            <span className="text-purple-400">{minQualityFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minQualityFilter}
            onChange={(e) => setMinQualityFilter(Number(e.target.value))}
            className="w-full accent-purple-500"
            aria-label="Minimum quality score"
          />
        </div>
        <button onClick={exportCSV} disabled={filteredData.length === 0} className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40">
          <Download size={12} /> CSV
        </button>
      </section>

      {/* Results */}
      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          {/* Stats */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Total Signals</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Quality</div>
              <div className="text-2xl font-bold text-purple-400">
                {filteredData.length > 0 ? filteredData.reduce((s, d) => s + d.event_quality, 0) / filteredData.length : 0}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Phase D (Breakout)</div>
              <div className="text-2xl font-bold text-green-400">{stats.phaseD}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Spring Detected</div>
              <div className="text-2xl font-bold text-purple-400">{stats.springCount}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Recent (≤5d)</div>
              <div className="text-2xl font-bold text-green-400">{stats.recent}</div>
            </div>
          </div>

          {/* Grade A Panel */}
          {gradeA.length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[12px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Signals</span>
                <span className="text-[#888]">— textbook Wyckoff events with quality ≥ 75</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {gradeA.slice(0, 12).map(d => {
                  const em = EVENT_META[d.wyckoff_event];
                  return (
                    <div key={d.symbol} className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-green-500/20 bg-[#1a1c24]">
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className={em?.color.split(' ')[1] ?? 'text-[#888]'}>{d.wyckoff_event}</span>
                      <span className="text-green-400">{d.event_quality}Q</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Table */}
          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap" role="grid" aria-label="Wyckoff Automaton results" aria-rowcount={filteredData.length} aria-colcount={20}>
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('symbol'); } }} onClick={() => handleSort('symbol')} scope="col">
                      Symbol <SortIcon col="symbol" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('sector'); } }} onClick={() => handleSort('sector')} scope="col">
                      Sector <SortIcon col="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'spring_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('spring_score'); } }} onClick={() => handleSort('spring_score')} scope="col">
                      <Tooltip content="Composite 0-100: delivery absorption 30%, lower wick 30%, close location 20%, grab depth 10%, equal-low bonus 10%.">Spring Score <SortIcon col="spring_score" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" aria-sort={sortCol === 'grade' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('grade'); } }} onClick={() => handleSort('grade')} scope="col">
                      <Tooltip content="Spring setup grade: A+ ≥ 65, B ≥ 50, C ≥ 35. Only C+ Springs are included.">Grade <SortIcon col="grade" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'close' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('close'); } }} onClick={() => handleSort('close')} scope="col">
                      Close <SortIcon col="close" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" aria-sort={sortCol === 'wyckoff_event' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('wyckoff_event'); } }} onClick={() => handleSort('wyckoff_event')} scope="col">
                      <Tooltip content="Wyckoff event type: SC (Selling Climax), AR (Automatic Rally), ST (Secondary Test), Spring (false breakdown), SOS (Sign of Strength). Each marks a different step in the accumulation process.">Event <SortIcon col="wyckoff_event" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" aria-sort={sortCol === 'phase' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('phase'); } }} onClick={() => handleSort('phase')} scope="col">
                      <Tooltip content="Wyckoff accumulation phase. Phase A (supply ending) → Phase B (accumulation) → Phase C (spring) → Phase D (mark-up / breakout).">Phase <SortIcon col="phase" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'lower_wick_ratio' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('lower_wick_ratio'); } }} onClick={() => handleSort('lower_wick_ratio')} scope="col">
                      <Tooltip content="Lower wick as % of total candle range. ≥60% = strong buyer absorption below the range. ≥40% = moderate.">Lower Wick <SortIcon col="lower_wick_ratio" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'close_location' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('close_location'); } }} onClick={() => handleSort('close_location')} scope="col">
                      <Tooltip content="Close position within the candle range (0 = low, 1 = high). >75% = closed near top (bullish). ≥50% = neutral.">Close Location <SortIcon col="close_location" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'grab_depth_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('grab_depth_pct'); } }} onClick={() => handleSort('grab_depth_pct')} scope="col">
                      <Tooltip content="How deep below the range the grab went (% of range). 0.5–1.5% = optimal institutional grab zone.">Grab Depth <SortIcon col="grab_depth_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" aria-sort={sortCol === 'equal_low_zone' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('equal_low_zone'); } }} onClick={() => handleSort('equal_low_zone')} scope="col">
                      <Tooltip content="Whether the Spring tested an equal-low support zone (multiple prior touches). Equal lows attract stop-losses — institutional grab target.">Equal Low <SortIcon col="equal_low_zone" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" aria-sort={sortCol === 'two_candle_confirm' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('two_candle_confirm'); } }} onClick={() => handleSort('two_candle_confirm')} scope="col">
                      <Tooltip content="Two-candle confirmation: a second candle closes back above the range low after the Spring candle. Stronger signal.">2-Candle <SortIcon col="two_candle_confirm" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('market_cap_cr'); } }} onClick={() => handleSort('market_cap_cr')} scope="col">
                      MCap (₹Cr) <SortIcon col="market_cap_cr" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'phase_complete_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('phase_complete_pct'); } }} onClick={() => handleSort('phase_complete_pct')} scope="col">
                      <Tooltip content="How far through the current phase the stock has progressed. Higher = closer to the next phase. 100% = phase complete.">Phase% <SortIcon col="phase_complete_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" aria-sort={sortCol === 'event_date' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('event_date'); } }} onClick={() => handleSort('event_date')} scope="col">
                      <Tooltip content="The date the Wyckoff event was detected. More recent = more actionable.">Event Date <SortIcon col="event_date" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'days_since_event' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('days_since_event'); } }} onClick={() => handleSort('days_since_event')} scope="col">
                      <Tooltip content="Days since this event occurred. ≤5 days = very fresh. ≤15 = still relevant. >30 = stale." good="≤5: fresh signal" bad=">30: stale">Days Ago <SortIcon col="days_since_event" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'event_delivery_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('event_delivery_pct'); } }} onClick={() => handleSort('event_delivery_pct')} scope="col">
                      <Tooltip content="Delivery percentage on the event day. For SC/Spring: high delivery = strong absorption. For ST: LOW delivery = sellers are gone (good).">Del% <SortIcon col="event_delivery_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'vol_ratio' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('vol_ratio'); } }} onClick={() => handleSort('vol_ratio')} scope="col">
                      <Tooltip content="Volume on event day relative to average. SC/Spring: >1.5x = climactic. SOS: >1.5x = conviction. ST: <1.0x = quiet test.">Vol Ratio <SortIcon col="vol_ratio" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right" scope="col">
                      <Tooltip content="90-session price range. Lower bound = support. Upper bound = resistance. Breakout above = Phase D begins.">Range (90d)</Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" aria-sort={sortCol === 'event_quality' ? (sortAsc ? 'ascending' : 'descending') : 'none'} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('event_quality'); } }} onClick={() => handleSort('event_quality')} scope="col">
                      <Tooltip content="Quality score (0–100) based on delivery, volume, and price action. A (75+) = textbook. B (55–74) = good. C (40–54) = marginal. D (<40) = unreliable." good="≥75: Grade A" bad="<40: Grade D">Quality <SortIcon col="event_quality" /></Tooltip>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={20} className="px-4 py-8 text-center text-[#888]">No signals match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((c, index) => {
                      const g = gradeBadge(c.event_quality);
                      const sg = springGradeBadge(c.grade);
                      const em = EVENT_META[c.wyckoff_event];
                      const pm = PHASE_META[c.phase];
                      return (
                        <tr key={c.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                          <td className="px-3 py-3 font-bold" role="rowheader">
                            <div className="flex items-center gap-1.5">
                              <StarButton symbol={c.symbol} size={11} />
                              <button
                                onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(c.symbol)}`, '_blank')}
                                className="text-[#fafafa] hover:text-purple-400 inline-flex items-center gap-1 transition-colors group"
                                aria-label={`Open chart for ${c.symbol}`}
                              >
                                {c.symbol}
                                <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" />
                              </button>
                            </div>
                          </td>
                          <td className="px-3 py-3 text-[#888] text-[12px] max-w-[120px] truncate" title={c.sector ?? ''}>{c.sector ?? '—'}</td>
                          <td className={`px-3 py-3 text-right ${springScoreColor(c.spring_score)}`}>{c.spring_score != null ? c.spring_score.toFixed(1) : '—'}</td>
                          <td className="px-3 py-3 text-center">
                            <Tooltip content={`Spring setup grade: A+ ≥ 65, B ≥ 50, C ≥ 35. Only C+ Springs are included.`}>
                              <span className={`inline-block px-1.5 py-0.5 rounded text-[12px] font-bold border ${sg.color}`}>{sg.label}</span>
                            </Tooltip>
                          </td>
                          <td className="px-3 py-3 text-right text-[#ccc]">{c.close?.toFixed(2) ?? '—'}</td>
                          <td className="px-3 py-3 text-center">
                            <Tooltip content={em?.desc ?? ''}>
                              <span className={`inline-block px-2 py-0.5 rounded text-[12px] font-bold border ${em?.color ?? 'bg-[#ffffff1a] text-[#888]'}`}>
                                {c.wyckoff_event}
                              </span>
                            </Tooltip>
                          </td>
                          <td className="px-3 py-3">
                            <Tooltip content={pm?.desc ?? ''}>
                              <span className={`inline-block px-2 py-0.5 rounded text-[12px] font-bold border ${pm?.color ?? 'bg-[#ffffff1a] text-[#888]'}`}>
                                {c.phase}
                              </span>
                            </Tooltip>
                          </td>
                          <td className={`px-3 py-3 text-right ${c.lower_wick_ratio != null ? (c.lower_wick_ratio >= 0.6 ? 'text-green-400' : c.lower_wick_ratio >= 0.4 ? 'text-amber-400' : 'text-[#888]') : 'text-[#888]'}`}>
                            {c.lower_wick_ratio != null ? `${(c.lower_wick_ratio * 100).toFixed(0)}%` : '—'}
                          </td>
                          <td className={`px-3 py-3 text-right ${c.close_location != null ? (c.close_location > 0.75 ? 'text-green-400' : c.close_location >= 0.5 ? 'text-amber-400' : 'text-[#888]') : 'text-[#888]'}`}>
                            {c.close_location != null ? `${(c.close_location * 100).toFixed(0)}%` : '—'}
                          </td>
                          <td className={`px-3 py-3 text-right ${c.grab_depth_pct != null ? (c.grab_depth_pct >= 0.5 && c.grab_depth_pct <= 1.5 ? 'text-green-400' : 'text-[#888]') : 'text-[#888]'}`}>
                            {c.grab_depth_pct != null ? `${c.grab_depth_pct.toFixed(2)}%` : '—'}
                          </td>
                          <td className="px-3 py-3 text-center">
                            {c.equal_low_zone ? (
                              <span className="inline-block px-1.5 py-0.5 rounded text-[12px] font-bold bg-green-500/10 text-green-400 border border-green-500/30">YES</span>
                            ) : (
                              <span className="text-[#888]">—</span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-center">
                            {c.two_candle_confirm ? (
                              <span className="inline-block px-1.5 py-0.5 rounded text-[12px] font-bold bg-green-500/10 text-green-400 border border-green-500/30">YES</span>
                            ) : (
                              <span className="text-[#888]">—</span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right text-[#ccc]">{c.market_cap_cr?.toFixed(0) ?? '—'}</td>
                          <td className="px-3 py-3">
                            <div className="flex items-center gap-2 justify-end">
                              <div className="w-16 h-1.5 bg-[#1a1a1a] rounded-full overflow-hidden">
                                <div className="h-full bg-green-500 rounded-full" style={{ width: `${c.phase_complete_pct}%` }} />
                              </div>
                              <span className="text-xs text-[#888]">{c.phase_complete_pct}%</span>
                            </div>
                          </td>
                          <td className="px-3 py-3 text-[#888] whitespace-nowrap">{c.event_date}</td>
                          <td className={`px-3 py-3 text-right ${daysColor(c.days_since_event)}`}>{c.days_since_event}d</td>
                          <td className="px-3 py-3 text-right text-[#ccc]">{c.event_delivery_pct?.toFixed(1) ?? '—'}%</td>
                          <td className="px-3 py-3 text-right text-[#ccc]">{c.vol_ratio?.toFixed(1) ?? '—'}x</td>
                          <td className="px-3 py-3 text-right text-[#888] whitespace-nowrap">
                            {c.range_low_90?.toFixed(1) ?? '—'} – {c.range_high_90?.toFixed(1) ?? '—'}
                          </td>
                          <td className="px-3 py-3 text-center">
                            <Tooltip content={`Quality score: ${c.event_quality}/100. ${g.label === 'A' ? 'Textbook event — high conviction.' : g.label === 'B' ? 'Good event — above average.' : g.label === 'C' ? 'Marginal — needs confirmation.' : 'Weak — unreliable event.'}`}>
                              <span className={`inline-block px-1.5 py-0.5 rounded text-[12px] font-bold ${g.color}`}>{g.label}</span>
                            </Tooltip>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
          <div className="flex justify-end">
            <button
              onClick={exportCSV}
              disabled={filteredData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40"
              aria-label="Export table as CSV"
            >
              <Download size={12} /> CSV
            </button>
          </div>
        </>
      )}

      {/* Idle / Empty State */}
      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2 max-w-md">
            <Box size={40} className="opacity-20 mb-2" />
            <p className="text-sm text-[#888]">Click <span className="text-purple-400 font-semibold">Scan</span> to find Wyckoff accumulation patterns.</p>
            <p className="text-[12px] leading-relaxed">
              The Automaton detects institutional accumulation by identifying Wyckoff events
              (SC, AR, ST, Spring, SOS) and classifying them into four phases.
              Stocks with multiple high-quality events in Phase C or D are closest to breakout.
            </p>
          </div>
        </div>
      )}
    </main>
  );
}
