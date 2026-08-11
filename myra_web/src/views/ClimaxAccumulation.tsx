import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, AlertTriangle, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Info } from 'lucide-react';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';

interface Candidate {
  symbol: string;
  sector?: string;
  climax_date: string;
  base_days: number;
  trigger_price: number;
  last_close: number;
  dist_pct: number;
  del_start: number;
  del_end: number;
  del_delta: number;
  sl_price: number;
  second_chance: boolean;
  days_to_lowest: number | null;
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

export default function ClimaxAccumulationView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const [scanDate, setScanDate] = useState('');
  const [minAdtvCr, setMinAdtvCr] = useState(1.0);

  const [sortCol, setSortCol] = useState<string>('dist_pct');
  const [sortAsc, setSortAsc] = useState(false);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startScanRef = useRef<(() => void) | null>(null);

  const candidates = scanStatus?.candidates ?? [];

  const filteredData = useMemo(() => {
    let data = [...candidates];
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, sortCol, sortAsc]);

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
      const res = await fetch(`${API_BASE}/climax-accumulation/status`);
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
      const res = await fetch(`${API_BASE}/climax-accumulation/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_adtv_cr: Number(minAdtvCr) || 1.0,
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
  }, [fetchScanStatus, clearPolling, scanDate, minAdtvCr]);
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
      'Symbol', 'Sector', 'Climax Date', 'Base Days', 'Trigger Price', 'Last Close',
      'Dist%', 'SL Price', 'Del Start%', 'Del End%', 'Del Delta', 'Second Chance', 'Days to Lowest',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.climax_date, r.base_days, r.trigger_price, r.last_close,
      r.dist_pct, r.sl_price, r.del_start, r.del_end, r.del_delta,
      r.second_chance ? 'YES' : '',
      r.days_to_lowest ?? '',
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `climax_accumulation_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Climax Accumulation Scanner">
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
          <div className="bg-purple-500/20 p-2 rounded" aria-hidden="true">
            <Box className="text-purple-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Climax Accumulation</h1>
            <p className="text-xs font-mono text-[#888]">High-volume climax → tight consolidation → rising delivery</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <div className="flex flex-col gap-1">
            <label htmlFor="min-adtv-input" className="text-[12px] text-[#888] font-mono">Min ADTV (₹ Cr)</label>
            <input
              id="min-adtv-input"
              type="number"
              min="0"
              step="0.5"
              value={minAdtvCr}
              onChange={e => setMinAdtvCr(Number(e.target.value))}
              className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 w-24 text-xs text-[#fafafa] font-mono focus:border-purple-500 outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
              title="Minimum average daily traded value (₹ Cr) over the last 20 trading days — split-adjusted liquidity filter"
              aria-label="Minimum ADTV in crore rupees"
            />
          </div>
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
            onClick={() => {
              fetch(`${API_BASE}/cache/climax-accumulation`, { method: 'DELETE' }).then(() => fetchScanStatus()).catch(() => {});
            }}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {isScanning && (
        <div className="bg-purple-500/10 border border-purple-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-purple-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-purple-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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
        <div className="flex items-center gap-2 px-3 py-1.5 rounded text-[12px] font-mono text-purple-400 bg-purple-500/5 border border-purple-500/20">
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

      {/* Info Banner */}
      <div className="bg-purple-500/5 border border-purple-500/20 rounded p-3">
        <div className="flex items-start gap-2 text-[12px] font-mono text-purple-300">
          <Info size={14} className="shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <span className="font-semibold text-purple-400">Climax Accumulation Scanner:</span>{' '}
            Identifies stocks where a high-volume distribution climax was followed by consolidation with rising delivery.{' '}
            The climax low acts as a structural reference level.
            <br />
            Liquidity filter: minimum {minAdtvCr} ₹ Cr average daily traded value (ADTV, 20-day) — split-adjusted, unlike a raw share-volume cutoff.
            <br />
            2-year backtest (219 signals, entry at day 15): –0.6% gross / –2.0% net 40-day return, 42% win rate overall.{' '}
            Test set (Mar 2026+): +4.2% gross, 54% win rate.
            <br />
            <span className="text-amber-400">⚠</span> This is NOT a standalone entry signal.{' '}
            Use it as an overlay on your existing watchlist or scanner results.
            <br />
            <span className="text-green-400 font-semibold">Second-Chance signals</span> (low broke, then recovered): 9 events.{' '}
            Entry at the LOWEST point after the break produced +35.5% avg 40-day return, 67% win rate.{' '}
            These are rare but powerful averaging opportunities.
          </div>
        </div>
      </div>

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Second Chance</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.second_chance).length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Dist%</div>
              <div className="text-2xl font-bold text-purple-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.dist_pct, 0) / filteredData.length).toFixed(1) + '%'
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Base Days</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.base_days, 0) / filteredData.length).toFixed(0)
                  : '—'}
              </div>
            </div>
          </div>

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Climax Accumulation results"
                aria-rowcount={filteredData.length}
                aria-colcount={13}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('sector')} scope="col">
                      Sector <SortIcon column="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('climax_date')} scope="col">
                      <Tooltip content="Date of the high-volume climax event." good="Recent: actionable" bad="Old: may be stale">Climax Date <SortIcon column="climax_date" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('base_days')} scope="col">
                      <Tooltip content="Number of trading days in the post-climax consolidation window. 5-12 is ideal." good="5-12: tight base" bad="<3 or >15: incomplete">Base Days <SortIcon column="base_days" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('trigger_price')} scope="col">
                      <Tooltip content="Climax day high — breakout trigger level. Buy above this.">Trigger ₹ <SortIcon column="trigger_price" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('last_close')} scope="col">
                      Close <SortIcon column="last_close" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('dist_pct')} scope="col">
                      <Tooltip content="Distance from current close to trigger price. Negative = already above trigger." good="<1%: coiled spring" bad="≥3%: extended">Dist% <SortIcon column="dist_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('sl_price')} scope="col">
                      <Tooltip content="Reference level: climax week low. A break below this that does NOT recover invalidates the thesis. A break that recovers is a potential averaging opportunity.">SL ₹ <SortIcon column="sl_price" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('del_start')} scope="col">
                      <Tooltip content="Average delivery% in first 30% of post-climax days. Lower = initial distribution.">Del Start% <SortIcon column="del_start" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('del_end')} scope="col">
                      <Tooltip content="Average delivery% in last 30% of post-climax days. Higher = accumulation in progress.">Del End% <SortIcon column="del_end" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('del_delta')} scope="col">
                      <Tooltip content="Del End% minus Del Start%. Positive = delivery rising (good).">Del Δ <SortIcon column="del_delta" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('second_chance')} scope="col">
                      <Tooltip content="Low briefly broke below climax low then recovered. 9 events backtested. Entry at the lowest point after break: +35.5% avg, 67% win rate.">2nd Chance <SortIcon column="second_chance" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('days_to_lowest')} scope="col">
                      <Tooltip content="For second-chance signals: days from the break to the lowest low. Lower = faster bottoming. Higher = still finding support.">Days to Low <SortIcon column="days_to_lowest" /></Tooltip>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={13} className="px-4 py-8 text-center text-[#888]">No climax accumulation setups match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <button
                            onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                            className="text-[#fafafa] hover:text-purple-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50"
                            aria-label={`Open chart for ${row.symbol}`}
                          >
                            {row.symbol}
                          </button>
                        </td>
                        <td className="px-3 py-3 text-[#888] text-[12px] max-w-[120px] truncate" title={row.sector ?? ''}>
                          {row.sector ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-[#ccc]">{row.climax_date}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.base_days}</td>
                        <td className="px-3 py-3 text-right text-purple-400 font-bold">₹{row.trigger_price.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">₹{row.last_close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.dist_pct < 1 ? 'text-green-400' :
                            row.dist_pct < 3 ? 'text-amber-400' :
                            'text-[#888]'
                          }>
                            {row.dist_pct.toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <Tooltip content="Reference level: climax week low. A break that recovers is an averaging opportunity.">
                            <span className="text-red-400">₹{row.sl_price.toFixed(2)}</span>
                          </Tooltip>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.del_start.toFixed(1)}%</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.del_end.toFixed(1)}%</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.del_delta > 0 ? 'text-green-400' : 'text-red-400'}>
                            +{row.del_delta.toFixed(1)}pp
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          {row.second_chance ? (
                            <span className="px-2 py-0.5 rounded text-[12px] font-bold border bg-green-500/20 text-green-400 border-green-500/30">
                              YES — Shakeout Recovered
                            </span>
                          ) : null}
                        </td>
                        <td className="px-3 py-3 text-center text-[#ccc]">
                          {row.days_to_lowest != null ? row.days_to_lowest : '—'}
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

      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Box size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to detect climax accumulation setups.</p>
            <p className="text-[12px]">High-volume distribution events followed by tight consolidation with rising delivery.</p>
          </div>
        </div>
      )}
    </main>
  );
}
