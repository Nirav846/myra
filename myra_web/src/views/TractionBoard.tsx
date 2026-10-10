import { useCallback, useEffect, useMemo, useState } from 'react';
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
      {!loading && !error && data && (
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
    </div>
  );
}

