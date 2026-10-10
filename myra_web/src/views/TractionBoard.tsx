import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpDown, ChevronUp, ChevronDown, LayoutGrid, Table2 } from 'lucide-react';
import { API_ROOT } from '../config';

// Traction Board — Pages-parity view of cross-fund-holdings-traction, served
// from our own DB via /api/fund-traction/board*. Monthly cadence only: data is
// refreshed when the fund traction sync runs (5th of each month), so this view
// fetches on mount and on user interaction — no polling, no websockets.

interface FundLine {
  fund_slug: string | null;
  fund_name: string;
  activity: string | null;
  share_change_pct: number | null;
  share_change_abs: number | null;
  weight_delta_pp: number | null;
  current_weight_pct: number | null;
  is_new: boolean;
  history_url: string | null;
}

interface Persistence {
  status: string;
  prior_month_id: string;
}

interface BoardRow {
  symbol: string;
  stock_key: string;
  name: string;
  nse: string;
  bse: string;
  sector: string;
  direction: string;
  score: number | null;
  fund_count: number;
  add_count: number;
  reduce_count: number;
  breadth_active: number;
  breadth_hold: number;
  median_share_change_pct: number | null;
  median_weight_delta_pp: number | null;
  pct_vs_sma: number | null;
  month_end_close: number | null;
  close_latest: number | null;
  new_entry_count: number;
   price: number | null;
   prev_month_close: number | null;
   pct_vs_prev: number | null;
   market_cap_cr: number | null;
   mcap_bucket: string;
  adds: FundLine[];
  reduces: FundLine[];
  holds: FundLine[];
  funds: FundLine[];
  persistence: Persistence;
}

interface BoardStats {
  total: number;
  adding: number;
  reducing: number;
  mixed: number;
  new_this_month: number;
  still_adding: number;
  reversed: number;
  watchlisted: number;
  large: number;
  mid: number;
  small: number;
  mcap_unknown: number;
}

interface BoardResponse {
  success: boolean;
  month: string | null;
  months: string[];
  prior_month: string | null;
  rows: BoardRow[];
  stats: BoardStats;
  count: number;
  filter: string;
  sort: string;
  error: string | null;
}

interface InsightItem {
  id: string;
  section: string;
  headline: string;
  action: string;
  stock_keys: string[];
  body: string;
  citations: unknown[];
  source: string;
  score: number | null;
  fund_count: number | null;
}

interface InsightsResponse {
  success: boolean;
  month: string | null;
  source: string;
  insights: InsightItem[];
  top_traction: InsightItem[];
  count: number;
  error: string | null;
}

const FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'added', label: 'Added' },
  { key: 'reduced', label: 'Reduced' },
  { key: 'new', label: 'New' },
  { key: 'mixed', label: 'Mixed' },
  { key: 'still_adding', label: 'Still Adding' },
  { key: 'reversed', label: 'Reversed' },
  { key: 'new_this_month', label: 'New This Month' },
  { key: 'watchlist', label: 'Watchlist' },
];

const SORTS: { key: string; label: string }[] = [
  { key: 'score', label: 'Score' },
  { key: 'funds', label: 'Funds' },
  { key: 'share_change', label: 'Share Δ%' },
  { key: 'weight_delta', label: 'Weight Δpp' },
  { key: 'pct_vs_sma', label: '% vs SMA' },
  { key: 'name', label: 'Name' },
];

const MCAP_FILTERS: { key: string; label: string }[] = [
  { key: '', label: 'All Caps' },
  { key: 'large', label: 'Large (≥20k Cr)' },
  { key: 'mid', label: 'Mid (5k–20k Cr)' },
  { key: 'small', label: 'Small (<5k Cr)' },
  { key: 'unknown', label: 'Unknown' },
];

const PERSISTENCE_BADGES: Record<string, { label: string; cls: string }> = {
  new_this_month: { label: 'New', cls: 'bg-emerald-500/15 text-emerald-300' },
  still_adding: { label: 'Still Adding', cls: 'bg-green-500/15 text-green-300' },
  still_reducing: { label: 'Still Reducing', cls: 'bg-red-500/15 text-red-300' },
  reversed: { label: 'Reversed', cls: 'bg-amber-500/15 text-amber-300' },
  continued_mixed: { label: 'Mixed', cls: 'bg-purple-500/15 text-purple-300' },
  unknown: { label: '—', cls: 'bg-white/5 text-gray-400' },
};

const ACTION_BADGES: Record<string, string> = {
  research: 'bg-emerald-500/15 text-emerald-300',
  monitor: 'bg-sky-500/15 text-sky-300',
  caution: 'bg-amber-500/15 text-amber-300',
};

interface TableColumn {
  key: string;
  label: string;
  numeric: boolean;
}

const TABLE_COLUMNS: TableColumn[] = [
  { key: 'name', label: 'Stock', numeric: false },
  { key: 'sector', label: 'Sector', numeric: false },
  { key: 'direction', label: 'Direction', numeric: false },
  { key: 'persistence', label: 'Persistence', numeric: false },
  { key: 'score', label: 'Score', numeric: true },
  { key: 'fund_count', label: 'Funds', numeric: true },
  { key: 'add_count', label: 'Adds', numeric: true },
  { key: 'reduce_count', label: 'Reduces', numeric: true },
  { key: 'new_entry_count', label: 'New', numeric: true },
  { key: 'median_share_change_pct', label: 'Share Δ%', numeric: true },
  { key: 'median_weight_delta_pp', label: 'Weight Δpp', numeric: true },
  { key: 'pct_vs_sma', label: '% vs SMA', numeric: true },
  { key: 'price', label: 'Price', numeric: true },
  { key: 'pct_vs_prev', label: 'Δ Prev%', numeric: true },
  { key: 'market_cap_cr', label: 'MCap Cr', numeric: true },
  { key: 'mcap_bucket', label: 'Cap', numeric: false },
];

/** Resolve a sortable value for a column (strings for text, numbers for numeric). */
function sortValue(row: BoardRow, key: string): number | string {
  if (key === 'persistence') return row.persistence?.status ?? 'unknown';
  if (key === 'name') return (row.name || row.nse || row.symbol || '').toLowerCase();
  if (key === 'sector') return (row.sector || '').toLowerCase();
  if (key === 'direction') return (row.direction || '').toLowerCase();
  if (key === 'mcap_bucket') return row.mcap_bucket || 'unknown';
  const v = (row as unknown as Record<string, unknown>)[key];
  return typeof v === 'number' ? v : Number.NEGATIVE_INFINITY;
}

function fmtMonth(ym: string): string {
  if (!ym || ym.length !== 7) return ym;
  const [y, m] = ym.split('-');
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const idx = parseInt(m, 10) - 1;
  return `${names[idx] ?? m} ${y}`;
}

function fmtNum(v: number | null | undefined, digits = 2, suffix = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)}${suffix}`;
}

export default function TractionBoard() {
  const [data, setData] = useState<BoardResponse | null>(null);
  const [insights, setInsights] = useState<InsightsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [month, setMonth] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('score');
  const [search, setSearch] = useState('');
   const [mcapBucket, setMcapBucket] = useState('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [pins, setPins] = useState<Set<string>>(new Set());
  const [view, setView] = useState<'cards' | 'table'>(() => {
    try {
      return localStorage.getItem('traction_view') === 'table' ? 'table' : 'cards';
    } catch {
      return 'cards';
    }
  });
  const [tableSort, setTableSort] = useState<{ key: string; asc: boolean } | null>(null);

  const selectView = useCallback((next: 'cards' | 'table') => {
    setView(next);
    try {
      localStorage.setItem('traction_view', next);
    } catch {
      // localStorage unavailable — view just won't persist.
    }
  }, []);

  const handleTableSort = useCallback((key: string) => {
    setTableSort(cur => (cur && cur.key === key ? { key, asc: !cur.asc } : { key, asc: false }));
  }, []);

  const loadBoard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ filter, sort });
      if (month) params.set('month', month);
      if (search.trim()) params.set('search', search.trim());
       if (mcapBucket) params.set('mcap_bucket', mcapBucket);
      const res = await fetch(`${API_ROOT}/api/fund-traction/board?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: BoardResponse = await res.json();
      if (!json.success) {
        setError(json.error || 'Board load failed');
        setData(null);
      } else {
        setData(json);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [month, filter, sort, search, mcapBucket]);

  const loadInsights = useCallback(async () => {
    try {
      const params = month ? `?month=${month}` : '';
      const res = await fetch(`${API_ROOT}/api/fund-traction/board/insights${params}`);
      if (!res.ok) return;
      const json: InsightsResponse = await res.json();
      if (json.success) setInsights(json);
    } catch {
      // Insights panel is non-fatal; board still renders without it.
    }
  }, [month]);

  const loadWatchlist = useCallback(async () => {
    try {
      const res = await fetch(`${API_ROOT}/api/fund-traction/board/watchlist`);
      if (!res.ok) return;
      const json = await res.json();
      if (json.success) setPins(new Set<string>(json.watchlist || []));
    } catch {
      // Watchlist miss is non-fatal; pins just start empty.
    }
  }, []);

  useEffect(() => {
    loadBoard();
  }, [loadBoard]);

  useEffect(() => {
    loadInsights();
  }, [loadInsights]);

  useEffect(() => {
    loadWatchlist();
  }, [loadWatchlist]);

  const togglePin = useCallback(
    async (row: BoardRow) => {
      const key = row.stock_key || row.symbol;
      const pinned = pins.has(key);
      try {
        const res = await fetch(
          `${API_ROOT}/api/fund-traction/board/watchlist/${pinned ? 'unpin' : 'pin'}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_key: key, name: row.name, nse: row.nse }),
          },
        );
        if (!res.ok) return;
        setPins(prev => {
          const next = new Set(prev);
          if (pinned) next.delete(key);
          else next.add(key);
          return next;
        });
        if (filter === 'watchlist') loadBoard();
      } catch {
        // Pin failure is non-fatal.
      }
    },
    [pins, filter, loadBoard],
  );

  const toggleExpand = useCallback((key: string) => {
    setExpanded(prev => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const chips = useMemo(() => {
    const s = data?.stats;
    if (!s) return [];
    return [
      { key: 'all', label: 'Total', value: s.total },
      { key: 'added', label: 'Adding', value: s.adding },
      { key: 'reduced', label: 'Reducing', value: s.reducing },
      { key: 'mixed', label: 'Mixed', value: s.mixed },
      { key: 'new_this_month', label: 'New', value: s.new_this_month },
      { key: 'still_adding', label: 'Still Adding', value: s.still_adding },
      { key: 'reversed', label: 'Reversed', value: s.reversed },
      { key: 'watchlist', label: 'Watchlist', value: s.watchlisted },
      { key: 'large', label: 'Large', value: s.large },
      { key: 'mid', label: 'Mid', value: s.mid },
      { key: 'small', label: 'Small', value: s.small },
      { key: 'mcap_unknown', label: 'Unknown', value: s.mcap_unknown },
    ];
  }, [data]);

  // Client-side sort for the table view; falls back to the server order until a
  // column header is clicked.
  const tableRows = useMemo(() => {
    const rows = data?.rows ?? [];
    if (!tableSort) return rows;
    const { key, asc } = tableSort;
    return [...rows].sort((a, b) => {
      const av = sortValue(a, key);
      const bv = sortValue(b, key);
      if (typeof av === 'number' && typeof bv === 'number') return asc ? av - bv : bv - av;
      return String(av).localeCompare(String(bv)) * (asc ? 1 : -1);
    });
  }, [data, tableSort]);

  const SortHead = ({ column }: { column: TableColumn }) => {
    if (!tableSort || tableSort.key !== column.key) {
      return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    }
    return tableSort.asc ? (
      <ChevronUp size={10} className="inline ml-1 text-indigo-300" />
    ) : (
      <ChevronDown size={10} className="inline ml-1 text-indigo-300" />
    );
  };

  return (
    <div className="p-4 space-y-4 text-gray-100">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">Traction Board</h1>
          <p className="text-xs text-gray-400">
            Cross-fund holdings traction — monthly MF portfolio filings, DB-backed.
            Daily-only app: refreshes on the monthly fund traction sync.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg bg-white/5 p-0.5" role="group" aria-label="View mode">
            <button
              onClick={() => selectView('cards')}
              aria-pressed={view === 'cards'}
              title="Card view"
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs transition-colors ${
                view === 'cards' ? 'bg-indigo-600 text-white' : 'text-gray-300 hover:bg-white/10'
              }`}
            >
              <LayoutGrid size={12} /> Cards
            </button>
            <button
              onClick={() => selectView('table')}
              aria-pressed={view === 'table'}
              title="Sortable table view"
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs transition-colors ${
                view === 'table' ? 'bg-indigo-600 text-white' : 'text-gray-300 hover:bg-white/10'
              }`}
            >
              <Table2 size={12} /> Table
            </button>
          </div>
          <button
            onClick={() => {
              loadBoard();
              loadInsights();
              loadWatchlist();
            }}
            className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-sm transition-colors"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Month tabs */}
      {data && data.months.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {data.months.map(m => (
            <button
              key={m}
              onClick={() => setMonth(m === data.month ? null : m)}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                (month ?? data.month) === m
                  ? 'bg-indigo-600 text-white'
                  : 'bg-white/5 text-gray-300 hover:bg-white/10'
              }`}
            >
              {fmtMonth(m)}
            </button>
          ))}
        </div>
      )}

      {/* Stats chips */}
      {chips.length > 0 && (
        <div className="grid grid-cols-4 sm:grid-cols-8 gap-2">
          {chips.map(c => (
            <button
              key={c.key}
              onClick={() => setFilter(c.key)}
              className={`rounded-lg p-2 text-center transition-colors ${
                filter === c.key
                  ? 'bg-indigo-600/25 ring-1 ring-indigo-500/50'
                  : 'bg-white/5 hover:bg-white/10'
              }`}
            >
              <div className="text-lg font-bold">{c.value}</div>
              <div className="text-[10px] uppercase tracking-wide text-gray-400">{c.label}</div>
            </button>
          ))}
        </div>
      )}

      {/* Filter + sort + search */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1">
          {FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
                filter === f.key
                  ? 'bg-indigo-600 text-white'
                  : 'bg-white/5 text-gray-300 hover:bg-white/10'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          {MCAP_FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setMcapBucket(f.key)}
              className={`px-2.5 py-1 rounded-md text-xs transition-colors ${mcapBucket === f.key ? 'bg-indigo-600 text-white' : 'bg-white/5 text-gray-300 hover:bg-white/10'}`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <select
          value={sort}
          onChange={e => setSort(e.target.value)}
          className="bg-white/5 rounded-md px-2 py-1 text-xs text-gray-200 outline-none"
        >
          {SORTS.map(s => (
            <option key={s.key} value={s.key}>
              Sort: {s.label}
            </option>
          ))}
        </select>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search name / symbol / sector…"
          className="bg-white/5 rounded-md px-3 py-1 text-xs w-56 outline-none focus:ring-1 focus:ring-indigo-500/50"
        />
        {data && (
          <span className="text-xs text-gray-500 ml-auto">
            {data.count} stock{data.count === 1 ? '' : 's'}
            {data.prior_month ? ` · vs ${fmtMonth(data.prior_month)}` : ''}
          </span>
        )}
      </div>

      {/* Status */}
      {loading && (
        <div className="text-sm text-gray-400 py-8 text-center">Loading traction data…</div>
      )}
      {error && (
        <div className="rounded-lg bg-red-500/10 ring-1 ring-red-500/30 p-4 text-sm text-red-200">
          {error}
        </div>
      )}

      {/* Insights + top traction panel */}
      {insights && (insights.insights.length > 0 || insights.top_traction.length > 0) && (
        <div className="grid md:grid-cols-2 gap-3">
          {insights.insights.length > 0 && (
            <div className="rounded-xl bg-white/5 p-3 space-y-2">
              <h2 className="text-sm font-semibold text-gray-200">Insights</h2>
              {insights.insights.map(ins => (
                <div key={ins.id} className="rounded-lg bg-white/5 p-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${
                        ACTION_BADGES[ins.action] ?? 'bg-white/10 text-gray-300'
                      }`}
                    >
                      {ins.action || ins.section}
                    </span>
                    <span className="text-xs font-medium text-gray-100">{ins.headline}</span>
                  </div>
                  {ins.body && <p className="text-[11px] text-gray-400 mt-1">{ins.body}</p>}
                </div>
              ))}
            </div>
          )}
          {insights.top_traction.length > 0 && (
            <div className="rounded-xl bg-white/5 p-3 space-y-2">
              <h2 className="text-sm font-semibold text-gray-200">Top Traction</h2>
              {insights.top_traction.map((tt, i) => (
                <div key={tt.id || i} className="flex items-center justify-between text-xs">
                  <span className="text-gray-200 truncate">{tt.headline}</span>
                  {tt.fund_count != null && (
                    <span className="text-gray-400 ml-2 shrink-0">{tt.fund_count} funds</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Stock cards */}
      {!loading && !error && data && view === 'cards' && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.rows.map(row => {
            const key = row.stock_key || row.symbol;
            const badge =
              PERSISTENCE_BADGES[row.persistence?.status] ?? PERSISTENCE_BADGES.unknown;
            const isOpen = !!expanded[key];
            return (
              <div key={key} className="rounded-xl bg-white/5 ring-1 ring-white/10 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm truncate">{row.name || row.symbol}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </div>
                    <div className="text-[11px] text-gray-400">
                      {row.nse || row.symbol}
                      {row.sector ? ` · ${row.sector}` : ''}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <div className="text-right">
                      <div className="text-sm font-bold text-indigo-300">{fmtNum(row.score, 1)}</div>
                      <div className="text-[10px] text-gray-500">score</div>
                    </div>
                    <button
                      onClick={() => togglePin(row)}
                      title={pins.has(key) ? 'Unpin' : 'Pin to watchlist'}
                      className={`text-lg leading-none transition-colors ${
                        pins.has(key) ? 'text-amber-400' : 'text-gray-600 hover:text-gray-400'
                      }`}
                    >
                      {pins.has(key) ? '★' : '☆'}
                    </button>
                  </div>
                </div>

                {/* Metrics row */}
                <div className="grid grid-cols-4 gap-1 mt-2 text-center">
                  <div className="rounded bg-white/5 py-1">
                    <div className="text-xs font-semibold">{row.fund_count}</div>
                    <div className="text-[9px] text-gray-500">Funds</div>
                  </div>
                  <div className="rounded bg-green-500/10 py-1">
                    <div className="text-xs font-semibold text-green-300">+{row.add_count}</div>
                    <div className="text-[9px] text-gray-500">Adds</div>
                  </div>
                  <div className="rounded bg-red-500/10 py-1">
                    <div className="text-xs font-semibold text-red-300">-{row.reduce_count}</div>
                    <div className="text-[9px] text-gray-500">Reduces</div>
                  </div>
                  <div className="rounded bg-white/5 py-1">
                    <div className="text-xs font-semibold">{row.new_entry_count}</div>
                    <div className="text-[9px] text-gray-500">New</div>
                  </div>
                </div>

                <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-2 text-[11px] text-gray-400">
                  <span>
                    Share Δ med{' '}
                    <span
                      className={
                        (row.median_share_change_pct ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'
                      }
                    >
                      {fmtNum(row.median_share_change_pct, 2, '%')}
                    </span>
                  </span>
                  <span>
                    Weight Δ{' '}
                    <span
                      className={
                        (row.median_weight_delta_pp ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'
                      }
                    >
                      {fmtNum(row.median_weight_delta_pp, 2, 'pp')}
                    </span>
                  </span>
                  <span>
                    vs SMA{' '}
                    <span
                      className={(row.pct_vs_sma ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}
                    >
                      {fmtNum(row.pct_vs_sma, 2, '%')}
                    </span>
                  </span>
                  <span>
                    Price{' '}
                    <span
                      className={(row.price ?? 0) >= 0 ? 'text-green-300' : 'text-red-300' }
                    >
                      {fmtNum(row.price, 2)}
                    </span>
                  </span>
                  <span>
                    Prev Close{' '}
                    <span
                      className={(row.prev_month_close ?? 0) >= 0 ? 'text-green-300' : 'text-red-300' }
                    >
                      {fmtNum(row.prev_month_close, 2)}
                    </span>
                  </span>
                  <span>
                    Δ Prev{' '}
                    <span
                      className={(row.pct_vs_prev ?? 0) >= 0 ? 'text-green-300' : 'text-red-300' }
                    >
                      {fmtNum(row.pct_vs_prev, 2, '%')}
                    </span>
                  </span>
                  <span>
                    Market Cap{' '}
                    <span className="text-gray-300">
                      {fmtNum(row.market_cap_cr, 0)} Cr
                    </span>
                  </span>
                  <span>
                    MCAP{' '}
                    <span className="text-gray-300">
                      {row.mcap_bucket === 'large' ? 'Large' : row.mcap_bucket === 'mid' ? 'Mid' : row.mcap_bucket === 'small' ? 'Small' : 'Unknown'}
                    </span>
                  </span>
                </div>

                {/* Per-fund breakdown (expandable) */}
                {(row.adds.length > 0 || row.reduces.length > 0 || row.holds.length > 0) && (
                  <>
                    <button
                      onClick={() => toggleExpand(key)}
                      className="mt-2 text-[11px] text-indigo-300 hover:text-indigo-200"
                    >
                      {isOpen ? '▾ Hide funds' : `▸ Show funds (${row.funds.length})`}
                    </button>
                    {isOpen && (
                      <div className="mt-2 space-y-1 max-h-56 overflow-y-auto pr-1">
                        {row.funds.map((f, i) => (
                          <div
                            key={`${f.fund_name}-${i}`}
                            className="flex items-center justify-between text-[11px] bg-white/5 rounded px-2 py-1"
                          >
                            <span className="text-gray-200 truncate mr-2">{f.fund_name}</span>
                            <span className="flex items-center gap-2 shrink-0">
                              {f.share_change_pct != null && (
                                <span
                                  className={
                                    f.share_change_pct >= 0 ? 'text-green-300' : 'text-red-300'
                                  }
                                >
                                  {f.share_change_pct >= 0 ? '+' : ''}
                                  {fmtNum(f.share_change_pct, 1, '%')}
                                </span>
                              )}
                              {f.weight_delta_pp != null && (
                                <span
                                  className={
                                    f.weight_delta_pp >= 0 ? 'text-green-300' : 'text-red-300'
                                  }
                                >
                                  {fmtNum(f.weight_delta_pp, 2, 'pp')}
                                </span>
                              )}
                              {f.is_new && (
                                <span className="px-1 rounded bg-emerald-500/15 text-emerald-300 text-[9px]">
                                  NEW
                                </span>
                              )}
                              <span className="text-gray-500 w-14 text-right">
                                {fmtNum(f.current_weight_pct, 2, '%')}
                              </span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })}
          {data.rows.length === 0 && (
            <div className="col-span-full text-sm text-gray-400 py-8 text-center rounded-lg bg-white/5">
              No stocks match the current filter.
            </div>
          )}
        </div>
      )}

      {/* Sortable table view */}
      {!loading && !error && data && view === 'table' && (
        <div className="rounded-xl bg-white/5 ring-1 ring-white/10 overflow-hidden">
          <div className="overflow-x-auto overflow-y-auto max-h-[70vh]">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 z-10 bg-[#12141b]">
                <tr className="text-gray-400">
                  {TABLE_COLUMNS.map(col => (
                    <th
                      key={col.key}
                      className={`px-2.5 py-2 font-medium border-b border-white/10 whitespace-nowrap ${
                        col.numeric ? 'text-right' : 'text-left'
                      }`}
                      aria-sort={
                        tableSort?.key === col.key
                          ? tableSort.asc
                            ? 'ascending'
                            : 'descending'
                          : 'none'
                      }
                    >
                      <button
                        type="button"
                        onClick={() => handleTableSort(col.key)}
                        title={`Sort by ${col.label}`}
                        className={`inline-flex items-center gap-0.5 cursor-pointer hover:text-white ${
                          col.numeric ? 'flex-row-reverse' : ''
                        }`}
                      >
                        {col.label}
                        <SortHead column={col} />
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableRows.map(row => {
                  const key = row.stock_key || row.symbol;
                  const badge =
                    PERSISTENCE_BADGES[row.persistence?.status] ?? PERSISTENCE_BADGES.unknown;
                  const isOpen = !!expanded[key];
                  const hasFunds =
                    row.adds.length > 0 || row.reduces.length > 0 || row.holds.length > 0;
                  return (
                    <Fragment key={key}>
                      <tr className="border-b border-white/5 hover:bg-white/5">
                        {TABLE_COLUMNS.map(col => {
                          switch (col.key) {
                            case 'name':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5">
                                  <div className="flex items-center gap-1.5">
                                    <button
                                      onClick={() => hasFunds && toggleExpand(key)}
                                      disabled={!hasFunds}
                                      className={`shrink-0 w-3 text-gray-500 ${
                                        hasFunds ? 'hover:text-gray-200' : 'opacity-30 cursor-default'
                                      }`}
                                      title={hasFunds ? 'Show funds' : 'No per-fund breakdown'}
                                    >
                                      {isOpen ? '▾' : '▸'}
                                    </button>
                                    <span className="font-medium text-gray-100 truncate max-w-[16rem]">
                                      {row.name || row.symbol}
                                    </span>
                                    <span className="text-gray-500 font-mono">
                                      {row.nse || row.symbol}
                                    </span>
                                    <button
                                      onClick={() => togglePin(row)}
                                      title={pins.has(key) ? 'Unpin' : 'Pin to watchlist'}
                                      className={`shrink-0 text-sm leading-none transition-colors ${
                                        pins.has(key)
                                          ? 'text-amber-400'
                                          : 'text-gray-600 hover:text-gray-400'
                                      }`}
                                    >
                                      {pins.has(key) ? '★' : '☆'}
                                    </button>
                                  </div>
                                </td>
                              );
                            case 'persistence':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5">
                                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${badge.cls}`}>
                                    {badge.label}
                                  </span>
                                </td>
                              );
                            case 'mcap_bucket':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5 capitalize text-gray-400">
                                  {row.mcap_bucket || 'unknown'}
                                </td>
                              );
                            case 'sector':
                            case 'direction':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5 text-gray-400 capitalize">
                                  {row[col.key as 'sector' | 'direction'] || '—'}
                                </td>
                              );
                            case 'score':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5 text-right font-bold text-indigo-300">
                                  {fmtNum(row.score, 1)}
                                </td>
                              );
                            case 'median_share_change_pct':
                            case 'median_weight_delta_pp':
                            case 'pct_vs_sma':
                            case 'pct_vs_prev': {
                              const v = row[col.key as keyof BoardRow] as number | null;
                              const suffix =
                                col.key === 'median_weight_delta_pp' ? 'pp' : '%';
                              return (
                                <td
                                  key={col.key}
                                  className={`px-2.5 py-1.5 text-right ${
                                    (v ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'
                                  }`}
                                >
                                  {fmtNum(v, 2, suffix)}
                                </td>
                              );
                            }
                            case 'market_cap_cr':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5 text-right text-gray-300">
                                  {fmtNum(row.market_cap_cr, 0)}
                                </td>
                              );
                            case 'add_count':
                            case 'reduce_count':
                            case 'new_entry_count': {
                              const v = row[col.key as 'add_count' | 'reduce_count' | 'new_entry_count'];
                              const cls =
                                col.key === 'add_count'
                                  ? 'text-green-300'
                                  : col.key === 'reduce_count'
                                    ? 'text-red-300'
                                    : 'text-gray-300';
                              return (
                                <td key={col.key} className={`px-2.5 py-1.5 text-right ${cls}`}>
                                  {col.key === 'new_entry_count' ? v : v > 0 ? `+${v}` : v}
                                </td>
                              );
                            }
                            case 'fund_count':
                            case 'price':
                              return (
                                <td key={col.key} className="px-2.5 py-1.5 text-right text-gray-200">
                                  {fmtNum(row[col.key as 'fund_count' | 'price'], col.key === 'fund_count' ? 0 : 2)}
                                </td>
                              );
                            default:
                              return (
                                <td key={col.key} className="px-2.5 py-1.5 text-right text-gray-300">
                                  {fmtNum(
                                    row[col.key as keyof BoardRow] as number | null,
                                    2,
                                  )}
                                </td>
                              );
                          }
                        })}
                      </tr>
                      {isOpen && hasFunds && (
                        <tr className="bg-black/20">
                          <td colSpan={TABLE_COLUMNS.length} className="px-3 py-2">
                            <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
                              {row.funds.map((f, i) => (
                                <div
                                  key={`${f.fund_name}-${i}`}
                                  className="flex items-center justify-between text-[11px] bg-white/5 rounded px-2 py-1"
                                >
                                  <span className="text-gray-200 truncate mr-2">{f.fund_name}</span>
                                  <span className="flex items-center gap-2 shrink-0">
                                    {f.share_change_pct != null && (
                                      <span
                                        className={
                                          f.share_change_pct >= 0 ? 'text-green-300' : 'text-red-300'
                                        }
                                      >
                                        {f.share_change_pct >= 0 ? '+' : ''}
                                        {fmtNum(f.share_change_pct, 1, '%')}
                                      </span>
                                    )}
                                    {f.weight_delta_pp != null && (
                                      <span
                                        className={
                                          f.weight_delta_pp >= 0 ? 'text-green-300' : 'text-red-300'
                                        }
                                      >
                                        {fmtNum(f.weight_delta_pp, 2, 'pp')}
                                      </span>
                                    )}
                                    {f.is_new && (
                                      <span className="px-1 rounded bg-emerald-500/15 text-emerald-300 text-[9px]">
                                        NEW
                                      </span>
                                    )}
                                    <span className="text-gray-500 w-14 text-right">
                                      {fmtNum(f.current_weight_pct, 2, '%')}
                                    </span>
                                  </span>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {tableRows.length === 0 && (
                  <tr>
                    <td
                      colSpan={TABLE_COLUMNS.length}
                      className="text-sm text-gray-400 py-8 text-center"
                    >
                      No stocks match the current filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

