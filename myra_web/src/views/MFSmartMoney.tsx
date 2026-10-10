import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { formatScannerCsv } from '../lib/scannerCsv';
import {
  RefreshCw,
  Download,
  ChevronUp,
  ChevronDown,
  ArrowUpDown,
  Settings2,
  Info,
  Plus,
} from 'lucide-react';
import FundTractionButton from '../components/FundTractionButton';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { API_BASE } from '../config';
import ScrollableTable from '../components/ScrollableTable';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';

interface Row {
  symbol: string;
  company: string;
  name: string;
  sector?: string;
  fund_count: number;
  prev_fund_count: number;
  net_funds: number;
  funds_entered: number;
  funds_exited: number;
  funds_added: number;
  funds_trimmed: number;
  funds_steady: number;
  total_weight_pct: number;
  stake_change_pp: number;
  share_change_pct: number | null;
  aum_held_cr: number | null;
  market_cap_cr: number | null;
  cmp: number | null;
  dwap: number | null;
  vs_dwap_pct: number | null;
  range_position: number | null;
  direction: 'steady' | 'increase' | 'decrease' | 'mixed';
  has_price: boolean;
  traction_score: number | null;
  pct_vs_sma: number | null;
  month: string;
  prior_month: string | null;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Row[];
  scanned_date?: string | null;
}

interface Defaults {
  months: string[];
  modes: string[];
  sort_keys: string[];
}

const MODES: { value: string; label: string; hint: string }[] = [
  { value: 'all', label: 'All', hint: 'Every stock held by any watchlist fund' },
  { value: 'accumulating', label: 'Accumulating', hint: 'Funds are adding / entering' },
  { value: 'steady', label: 'Pure Hold', hint: 'Flat across all funds (no adds, no trims)' },
  { value: 'trimming', label: 'Trimming', hint: 'Funds are reducing / exiting' },
  { value: 'bargain', label: 'Bargain', hint: 'Below fund cost (DWAP) while still bought/held' },
];

const DIR_STYLES: Record<string, string> = {
  increase: 'bg-green-500/20 text-green-400 border-green-500/30',
  steady: 'bg-sky-500/20 text-sky-400 border-sky-500/30',
  decrease: 'bg-red-500/20 text-red-400 border-red-500/30',
  mixed: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
};

const DIR_LABEL: Record<string, string> = {
  increase: 'Adding',
  steady: 'Holding',
  decrease: 'Trimming',
  mixed: 'Mixed',
};

function relativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Never';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return 'Never';
    const mins = Math.floor((Date.now() - d.getTime()) / 60000);
    if (mins < 0) return 'Just now';
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  } catch {
    return dateStr || 'Never';
  }
}

function fmtNum(v: number | null | undefined, digits = 1): string {
  return v == null ? '-' : v.toFixed(digits);
}

export default function MFSmartMoneyView() {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<Defaults | null>(null);

  const [month, setMonth] = useState<string>('');
  const [mode, setMode] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('fund_count');
  const [minFunds, setMinFunds] = useState(1);
  const [minAumHeldCr, setMinAumHeldCr] = useState(0);
  const [maxVsDwapPct, setMaxVsDwapPct] = useState<string>('');
  const [requirePrice, setRequirePrice] = useState(true);
  const [showParams, setShowParams] = useState(false);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());
  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [dirFilter, setDirFilter] = useState<string>('All');
  const [colSort, setColSort] = useState<{ key: string; asc: boolean } | null>(null);

  const mountedRef = useRef(true);
  const scanParamsRef = useRef<Record<string, unknown> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const candidates = scanStatus?.candidates ?? [];
  const completedScan = scanStatus?.scan_status === 'completed';

  useEffect(() => {
    fetchMarketCapMap().then((m) => (mcapMapRef.current = m));
    fetch(`${API_BASE}/mf-smart-money/defaults`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Defaults | null) => {
        if (!d || !mountedRef.current) return;
        setDefaults(d);
        setMonth((cur) => cur || (d.months?.[0] ?? ''));
      })
      .catch(() => undefined);
  }, []);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/mf-smart-money/status`);
      if (!mountedRef.current || !res.ok) return;
      const data: ScanStatus = await res.json();
      setScanStatus(data);
      setError(null);
      if (data.scan_status === 'completed' || data.scan_status === 'error') {
        clearPolling();
        setIsScanning(false);
      }
    } catch {
      if (mountedRef.current) setError('Failed to fetch status');
    }
  }, [clearPolling]);

  const buildScanBody = useCallback(
    (): Record<string, unknown> => ({
      month: month || undefined,
      mode,
      sort_by: sortBy,
      min_funds: minFunds,
      min_aum_held_cr: minAumHeldCr,
      max_vs_dwap_pct: maxVsDwapPct === '' ? undefined : Number(maxVsDwapPct),
      require_price: requirePrice,
    }),
    [month, mode, sortBy, minFunds, minAumHeldCr, maxVsDwapPct, requirePrice]
  );

  const startScan = useCallback(async () => {
    setIsScanning(true);
    setError(null);
    try {
      const body = buildScanBody();
      const res = await fetch(`${API_BASE}/mf-smart-money/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) scanParamsRef.current = { ...body };
      pollTimerRef.current = setInterval(fetchScanStatus, 2000);
      fetchScanStatus();
    } catch {
      setIsScanning(false);
      setError('Failed to start scan');
    }
  }, [buildScanBody, fetchScanStatus]);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchScanStatus, clearPolling]);

  const availableSectors = useMemo(() => {
    const sectors = new Set(candidates.map((c) => c.sector || 'Unknown'));
    return ['All', ...Array.from(sectors).filter((s) => s !== 'Unknown').sort(), 'Unknown'];
  }, [candidates]);

  const displayData = useMemo(() => {
    let data: Row[] = [...candidates];
    if (mcapRange) {
      const map = mcapMapRef.current;
      data = data.filter((d) => {
        const mcap = d.market_cap_cr ?? (d.symbol ? map.get(d.symbol) : undefined);
        return mcap !== undefined && mcap >= mcapRange.min && mcap <= mcapRange.max;
      });
    }
    if (watchlistOnly) data = data.filter((d) => d.symbol && isWatched(d.symbol));
    if (sectorFilter !== 'All') data = data.filter((d) => (d.sector || 'Unknown') === sectorFilter);
    if (dirFilter !== 'All') data = data.filter((d) => d.direction === dirFilter);
    if (colSort) {
      const { key, asc } = colSort;
      data.sort((a, b) => {
        const av = (a as unknown as Record<string, unknown>)[key] as number | string | null;
        const bv = (b as unknown as Record<string, unknown>)[key] as number | string | null;
        if (typeof av === 'number' && typeof bv === 'number') return asc ? av - bv : bv - av;
        const an = av == null ? '' : String(av);
        const bn = bv == null ? '' : String(bv);
        return an.localeCompare(bn) * (asc ? 1 : -1);
      });
    }
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, dirFilter, colSort, isWatched]);

  const handleSort = (key: string) => {
    setColSort((cur) =>
      cur && cur.key === key ? { key, asc: !cur.asc } : { key, asc: false }
    );
  };

  const SortIcon = ({ column }: { column: string }) => {
    if (!colSort || colSort.key !== column)
      return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return colSort.asc ? (
      <ChevronUp size={10} className="inline ml-1 text-emerald-400" />
    ) : (
      <ChevronDown size={10} className="inline ml-1 text-emerald-400" />
    );
  };

  const resolveSymbol = useCallback(
    async (row: Row) => {
      const ticker = window.prompt(
        `Enter the NSE ticker for:\n${row.company}\n\n(It will be cached for future scans.)`
      );
      if (!ticker || !ticker.trim()) return;
      try {
        const res = await fetch(`${API_BASE}/mf-smart-money/resolve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ company: row.company, symbol: ticker.trim() }),
        });
        const body = await res.json().catch(() => null);
        if (!res.ok) {
          setError(body?.detail || 'Could not save mapping');
          return;
        }
        if (body?.note) setError(body.note);
        else setError(null);
        startScan();
      } catch {
        setError('Could not save mapping');
      }
    },
    [startScan]
  );

  const exportCsv = () => {
    if (!displayData.length) return;
    const headers = [
      'Symbol', 'Company', 'Sector', 'Direction', 'Funds', 'PrevFunds', 'Net', 'Entered',
      'Exited', 'Added', 'Trimmed', 'Steady', 'TotalWeight%', 'StakeChangePP',
      'ShareChange%', 'AUMHeldCr', 'McapCr', 'CMP', 'DWAP', 'VsDWAP%', 'RangePos',
      'Traction', 'PctVsSMA', 'Month',
    ];
    const rows = displayData.map((r) => [
      r.symbol || '(unmapped)', r.company, r.sector ?? '', r.direction, r.fund_count,
      r.prev_fund_count, r.net_funds, r.funds_entered, r.funds_exited, r.funds_added,
      r.funds_trimmed, r.funds_steady, r.total_weight_pct, r.stake_change_pp,
      r.share_change_pct ?? '', r.aum_held_cr ?? '', r.market_cap_cr ?? '', r.cmp ?? '',
      r.dwap ?? '', r.vs_dwap_pct ?? '', r.range_position ?? '', r.traction_score ?? '',
      r.pct_vs_sma ?? '', r.month,
    ]);
    const csv = formatScannerCsv([headers, ...rows].map((r) => r.join(',')).join('\n'), {
      scanner: 'mf-smart-money',
      scanned_date: scanStatus?.scanned_date ?? scanStatus?.last_scan ?? '',
      params: scanParamsRef.current ?? buildScanBody(),
      exported_at: new Date().toISOString(),
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mf_smart_money.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const stats = useMemo(() => {
    const n = displayData.length;
    const aum = displayData.reduce((s, r) => s + (r.aum_held_cr ?? 0), 0);
    const adds = displayData.reduce((s, r) => s + (r.funds_added ?? 0) + (r.funds_entered ?? 0), 0);
    const trims = displayData.reduce((s, r) => s + (r.funds_trimmed ?? 0) + (r.funds_exited ?? 0), 0);
    const unpriced = displayData.filter((r) => !r.has_price).length;
    return { n, aum, adds, trims, unpriced };
  }, [displayData]);

  const columns: { key: string; label: string }[] = [
    { key: 'company', label: 'Company' },
    { key: 'symbol', label: 'Symbol' },
    { key: 'sector', label: 'Sector' },
    { key: 'direction', label: 'Signal' },
    { key: 'fund_count', label: 'Funds' },
    { key: 'net_funds', label: 'Net' },
    { key: 'total_weight_pct', label: 'Wt%' },
    { key: 'aum_held_cr', label: 'AUM ₹Cr' },
    { key: 'vs_dwap_pct', label: 'vs DWAP' },
    { key: 'range_position', label: 'Range' },
    { key: 'market_cap_cr', label: 'Mcap ₹Cr' },
    { key: 'traction_score', label: 'Traction' },
  ];

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">MF Smart Money</h1>
          <p className="text-xs text-[#888] mt-1">
            Every stock held by the watchlist funds — including pure holds and unmapped names
            {scanStatus?.scanned_date ? ` · as of ${scanStatus.scanned_date}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#888]">Last: {relativeTime(scanStatus?.last_scan)}</span>
          <button
            onClick={() => setShowParams((s) => !s)}
            className="p-1.5 rounded bg-[#ffffff0a] hover:bg-[#ffffff14] text-[#888]"
            title="Parameters"
          >
            <Settings2 size={14} />
          </button>
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-50"
          >
            {isScanning ? <RefreshCw size={14} className="animate-spin inline mr-1" /> : null}
            {isScanning ? 'Scanning...' : 'Scan'}
          </button>
        </div>
      </div>

      {error && <div className="text-red-400 text-sm bg-red-500/10 p-2 rounded">{error}</div>}

      {isScanning && scanStatus && (
        <div className="bg-[#ffffff0a] rounded p-3">
          <div className="flex justify-between text-xs text-[#888] mb-1">
            <span>{scanStatus.message}</span>
            <span>{scanStatus.progress}%</span>
          </div>
          <div className="w-full bg-[#ffffff0a] rounded-full h-1.5">
            <div
              className="bg-emerald-500 h-1.5 rounded-full transition-all"
              style={{ width: `${scanStatus.progress}%` }}
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[12px] text-[#888] font-mono mr-1">Mode:</span>
        {MODES.map((m) => (
          <button
            key={m.value}
            onClick={() => setMode(m.value)}
            title={m.hint}
            className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors ${
              mode === m.value
                ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400'
                : 'bg-[#ffffff0a] border-[#ffffff14] text-[#888] hover:text-white hover:border-[#ffffff30]'
            }`}
            aria-pressed={mode === m.value}
          >
            {m.label}
          </button>
        ))}
        <div className="ml-3 border-l border-[#ffffff14] pl-3 flex items-center gap-2">
          <label className="text-xs text-[#888]">Month</label>
          <select
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
          >
            <option value="">Auto (latest complete)</option>
            {(defaults?.months ?? []).map((mo) => (
              <option key={mo} value={mo}>
                {mo}
              </option>
            ))}
          </select>
        </div>
      </div>

      {showParams && (
        <div className="bg-[#ffffff06] border border-[#ffffff14] rounded p-3 grid grid-cols-2 md:grid-cols-4 gap-3">
          <label className="text-xs text-[#888]">
            Min Funds Holding
            <input
              type="number"
              min={1}
              value={minFunds}
              onChange={(e) => setMinFunds(Number(e.target.value))}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
            />
          </label>
          <label className="text-xs text-[#888]">
            Min AUM Held (₹ Cr)
            <input
              type="number"
              min={0}
              value={minAumHeldCr}
              onChange={(e) => setMinAumHeldCr(Number(e.target.value))}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
            />
          </label>
          <label className="text-xs text-[#888]">
            Max % vs DWAP
            <input
              type="number"
              value={maxVsDwapPct}
              placeholder="any"
              onChange={(e) => setMaxVsDwapPct(e.target.value)}
              title="Only stocks trading at/below this premium to the funds' cost (DWAP)."
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
            />
          </label>
          <label className="text-xs text-[#888]">
            Server Sort
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
            >
              {(defaults?.sort_keys ?? [
                'traction_score',
                'vs_dwap_pct',
                'fund_count',
                'aum_held_cr',
                'share_change_pct',
              ]).map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-[#888] flex items-end gap-2 pb-1">
            <input
              type="checkbox"
              checked={requirePrice}
              onChange={(e) => setRequirePrice(e.target.checked)}
              className="rounded accent-emerald-500"
            />
            Only priced (hide unmapped)
          </label>
        </div>
      )}

      {completedScan && !isScanning && (
        <div className="bg-[#ffffff06] border border-[#ffffff14] rounded p-3 text-xs font-mono text-[#888] flex items-start gap-2">
          <Info size={14} className="text-emerald-400 shrink-0 mt-0.5" />
          <div>
            <span className="text-white font-semibold">{MODES.find((m) => m.value === mode)?.label}</span>
            {' '}· {defaults?.months?.includes(month) ? month : 'latest complete month'}.
            {stats.n === 0 ? (
              <span> <span className="text-red-400">0 stocks.</span> Try mode “All”.</span>
            ) : (
              <span> <span className="text-emerald-400">{stats.n} stocks</span> held.</span>
            )}
            {stats.unpriced > 0 && (
              <span>
                {' '}· <span className="text-amber-400">{stats.unpriced} without a ticker</span> (shown by name).
              </span>
            )}
          </div>
        </div>
      )}

      {scanStatus?.scan_status !== 'scanning' && (
        <div className="flex flex-wrap gap-3 text-xs text-[#888]">
          <span className="bg-[#ffffff0a] px-2 py-1 rounded">{stats.n} stocks</span>
          <span className="bg-[#ffffff0a] px-2 py-1 rounded">AUM footprint: ₹{stats.aum.toFixed(0)} Cr</span>
          <span className="bg-emerald-500/10 text-emerald-400 px-2 py-1 rounded">Fund adds: {stats.adds}</span>
          <span className="bg-red-500/10 text-red-400 px-2 py-1 rounded">Fund trims: {stats.trims}</span>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="flex flex-wrap gap-2 items-center text-xs">
          <MarketCapRangeFilter onChange={setMcapRange} />
          <select
            value={dirFilter}
            onChange={(e) => setDirFilter(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white"
          >
            <option value="All">All Signals</option>
            <option value="increase">Adding</option>
            <option value="steady">Holding</option>
            <option value="decrease">Trimming</option>
            <option value="mixed">Mixed</option>
          </select>
          <select
            value={sectorFilter}
            onChange={(e) => setSectorFilter(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white"
          >
            {availableSectors.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-[#888]">
            <input
              type="checkbox"
              checked={watchlistOnly}
              onChange={(e) => setWatchlistOnly(e.target.checked)}
              className="rounded"
            />
            Watchlist
          </label>
          <button
            onClick={exportCsv}
            className="ml-auto px-2 py-1 rounded bg-[#ffffff0a] hover:bg-[#ffffff14] text-[#888]"
            title="Export CSV"
          >
            <Download size={12} />
          </button>
        </div>
      )}

      {displayData.length > 0 ? (
        <ScrollableTable className="text-xs">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff14]">
                {columns.map((col) => (
                  <th
                    key={col.key}
                    className="px-2 py-1.5 cursor-pointer hover:text-white whitespace-nowrap"
                    onClick={() => handleSort(col.key)}
                  >
                    {col.label}
                    <SortIcon column={col.key} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayData.map((r, i) => (
                <tr
                  key={`${r.company}-${r.symbol}-${i}`}
                  className="border-b border-[#ffffff08] hover:bg-[#ffffff08]"
                >
                  <td className="px-2 py-1.5 font-medium text-white">{r.company}</td>
                  <td className="px-2 py-1.5">
                    {r.symbol ? (
                      <span className="flex items-center gap-1">
                        <span className="text-white">{r.symbol}</span>
                        <FundTractionButton symbols={[r.symbol]} size="xs" />
                      </span>
                    ) : (
                      <button
                        onClick={() => resolveSymbol(r)}
                        className="flex items-center gap-1 text-amber-400 hover:text-amber-300"
                        title="Add a ticker for this company (cached for future scans)"
                      >
                        <Plus size={11} /> Add symbol
                      </button>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">{r.sector || '-'}</td>
                  <td className="px-2 py-1.5">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] border ${
                        DIR_STYLES[r.direction] ?? DIR_STYLES.mixed
                      }`}
                    >
                      {DIR_LABEL[r.direction] ?? r.direction}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-[#888]" title={`${r.funds_entered} entered, ${r.funds_exited} exited`}>
                    {r.fund_count}
                  </td>
                  <td className="px-2 py-1.5">
                    <span
                      className={
                        r.net_funds > 0 ? 'text-green-400' : r.net_funds < 0 ? 'text-red-400' : 'text-[#888]'
                      }
                    >
                      {r.net_funds > 0 ? '+' : ''}
                      {r.net_funds}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">{fmtNum(r.total_weight_pct)}</td>
                  <td className="px-2 py-1.5 text-[#888]">{fmtNum(r.aum_held_cr, 0)}</td>
                  <td className="px-2 py-1.5">
                    {r.vs_dwap_pct == null ? (
                      '-'
                    ) : (
                      <span className={r.vs_dwap_pct < 0 ? 'text-green-400' : 'text-[#888]'}>
                        {r.vs_dwap_pct > 0 ? '+' : ''}
                        {r.vs_dwap_pct.toFixed(1)}%
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">
                    {r.range_position == null ? '-' : `${(r.range_position * 100).toFixed(0)}%`}
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">{fmtNum(r.market_cap_cr, 0)}</td>
                  <td className="px-2 py-1.5">
                    {r.traction_score == null ? (
                      '-'
                    ) : (
                      <span
                        className={
                          r.traction_score >= 60
                            ? 'text-green-400'
                            : r.traction_score >= 40
                              ? 'text-amber-400'
                              : 'text-[#888]'
                        }
                      >
                        {fmtNum(r.traction_score)}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      ) : scanStatus?.scan_status !== 'scanning' ? (
        <div className="text-center text-[#888] py-12">
          {scanStatus?.scan_status === 'idle' && !scanStatus?.last_scan
            ? 'Click Scan to see every stock held by the watchlist funds'
            : 'No stocks match current filters'}
        </div>
      ) : null}
    </div>
  );
}
