import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';
import { Librarian } from '../lib/Librarian';
import { SymbolSearch } from '../components/SymbolSearch';
import { API_BASE } from '../config';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import {
  Loader2,
  AlertTriangle,
  RefreshCw,
  LineChart,
  Activity,
  Coins,
  Percent,
  Scale,
  Wallet,
  Building2,
  ShieldCheck,
  Tag,
  TrendingUp,
  TrendingDown,
  CircleDollarSign,
  Gauge,
  Users,
  Award,
} from 'lucide-react';

const Plot = lazy(async () => {
  const mod: any = await import('react-plotly.js');
  return { default: (mod.default ?? mod) as React.ComponentType<any> };
});

// --------------------------------------------------------------------------- //
// Types
// --------------------------------------------------------------------------- //

interface Shareholding {
  promoters?: number | null;
  fii?: number | null;
  dii?: number | null;
  public?: number | null;
  government?: number | null;
}

interface Snapshot {
  company_id?: string | null;
  market_cap_crore?: number | null;
  dividend_yield?: number | null;
  face_value?: number | null;
  roe?: number | null;
  roce?: number | null;
  pe?: number | null;
  current_price?: number | null;
  book_value?: number | null;
  shareholding?: Shareholding;
}

interface SeriesPoint {
  date: string;
  value: number | null;
}

interface YfinanceData {
  long_name?: string | null;
  sector?: string | null;
  industry?: string | null;
  market_cap?: number | null;
  pe?: number | null;
  price_to_book?: number | null;
  forward_pe?: number | null;
  roe?: number | null;
  roce?: number | null;
  dividend_yield?: number | null;
  debt_to_equity?: number | null;
  beta?: number | null;
  revenue_growth?: number | null;
  earnings_growth?: number | null;
  recommendation_key?: string | null;
  recommendation_mean?: number | null;
  analyst_count?: number | null;
  target_mean_price?: number | null;
  current_price?: number | null;
  total_debt?: number | null;
  total_cash?: number | null;
}

interface FullData {
  symbol: string;
  timestamp?: string | null;
  sources: string[];
  company_id?: string | null;
  snapshot: Snapshot;
  ratios: Record<string, Record<string, number>>;
  timeseries: Record<string, SeriesPoint[]>;
  yfinance: YfinanceData;
  warning?: string | null;
}

interface Insight {
  key: string;
  title: string;
  detail: string;
  severity: 'green' | 'yellow' | 'red';
}

interface FullResponse {
  symbol: string;
  data: FullData;
  insights: Insight[];
  cached: boolean;
  last_updated: string | null;
}

// --------------------------------------------------------------------------- //
// Formatting helpers
// --------------------------------------------------------------------------- //

const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: digits });

const fmtPrice = (v: number | null | undefined): string =>
  v == null || Number.isNaN(v) ? '—' : `₹${fmtNum(v)}`;

const fmtGrowth = (v: number | null | undefined, digits = 1): string => {
  if (v == null || Number.isNaN(v)) return '—';
  const pct = Math.abs(v) <= 1.5 ? v * 100 : v;
  return `${fmtNum(pct, digits)}%`;
};

const fmtMcapCr = (v: number | null | undefined): string => {
  if (v == null || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1e5) {
    return `₹${(v / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 })} L Cr`;
  }
  if (Math.abs(v) >= 1e3) {
    return `₹${(v / 1e3).toLocaleString('en-IN', { maximumFractionDigits: 1 })}K Cr`;
  }
  return `₹${fmtNum(v, 1)} Cr`;
};

const fmtDE = (v: number | null | undefined): string => {
  if (v == null || Number.isNaN(v)) return '—';
  return fmtNum(v > 5 ? v / 100 : v);
};

const fmtDY = (v: number | null | undefined): string => {
  if (v == null || Number.isNaN(v)) return '—';
  return `${fmtNum(v > 1 ? v : v * 100, 2)}%`;
};

const fmtDate = (v: string | null | undefined): string => {
  if (!v) return '—';
  try {
    const d = new Date(v);
    if (isNaN(d.getTime())) return v;
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch {
    return v;
  }
};

const REC_LABEL: Record<string, string> = {
  strong_buy: 'Strong Buy',
  buy: 'Buy',
  hold: 'Hold',
  sell: 'Sell',
  strong_sell: 'Strong Sell',
};

const latestOf = (series: SeriesPoint[] | undefined): number | null =>
  series && series.length ? (series[series.length - 1].value ?? null) : null;

// --------------------------------------------------------------------------- //
// Small components
// --------------------------------------------------------------------------- //

function MetricCard({ icon, label, value, sub, accent = 'text-cyan-400' }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-[12px] text-[#888] font-mono uppercase tracking-wider">
        <span className={accent}>{icon}</span>
        {label}
      </div>
      <div className="text-lg font-bold text-[#fafafa] leading-tight">{value}</div>
      {sub && <div className="text-[11px] font-mono text-[#888]">{sub}</div>}
    </div>
  );
}

const SEVERITY_STYLES: Record<string, { card: string; dot: string; text: string }> = {
  green: { card: 'bg-emerald-500/10 border-emerald-500/40', dot: 'bg-emerald-400', text: 'text-emerald-300' },
  yellow: { card: 'bg-amber-500/10 border-amber-500/40', dot: 'bg-amber-400', text: 'text-amber-300' },
  red: { card: 'bg-red-500/10 border-red-500/40', dot: 'bg-red-400', text: 'text-red-300' },
};

function InsightCard({ insight }: { insight: Insight }) {
  const s = SEVERITY_STYLES[insight.severity] ?? SEVERITY_STYLES.yellow;
  return (
    <div className={`rounded border px-3 py-2 flex items-start gap-3 ${s.card}`}>
      <span className={`mt-1 h-2 w-2 rounded-full shrink-0 ${s.dot}`} aria-hidden="true" />
      <div className="min-w-0">
        <div className={`text-[12px] font-semibold uppercase tracking-wider ${s.text}`}>{insight.title}</div>
        <div className="text-[12px] font-mono text-[#ccc] leading-snug">{insight.detail}</div>
      </div>
    </div>
  );
}

const PIE_COLORS: Record<string, string> = {
  promoters: '#22d3ee',
  fii: '#34d399',
  dii: '#fbbf24',
  public: '#a78bfa',
  government: '#fb7185',
};

function TrendChart({ title, series }: { title: string; series: SeriesPoint[] }) {
  if (!series || series.length === 0) return null;
  const data = [
    {
      x: series.map(p => p.date),
      y: series.map(p => p.value),
      type: 'scatter',
      mode: 'lines+markers',
      name: title,
      line: { color: '#22d3ee', width: 2 },
      marker: { size: 3, color: '#22d3ee' },
    },
  ];
  const layout = {
    title: { text: title, font: { color: '#fafafa', size: 12 }, x: 0.02 },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#888', family: 'monospace', size: 10 },
    margin: { l: 44, r: 12, t: 34, b: 28 },
    xaxis: { showgrid: false, zeroline: false, color: '#888' },
    yaxis: { showgrid: true, gridcolor: 'rgba(255,255,255,0.06)', zeroline: false, color: '#888' },
    autosize: true,
  };
  const config = { displayModeBar: false, responsive: true };
  return (
    <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
      <Suspense fallback={<div className="h-[260px] flex items-center justify-center text-[#888] font-mono text-xs">Loading chart…</div>}>
        <Plot data={data} layout={layout} config={config} style={{ width: '100%', height: 260 }} />
      </Suspense>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Main view
// --------------------------------------------------------------------------- //

export default function FullFundamentalsView({ lib }: { lib: Librarian }) {
  const location = useLocation();
  const urlSymbol = new URLSearchParams(location.search).get('symbol') || undefined;

  const [selectedSymbol, setSelectedSymbol] = useState<string>(urlSymbol ?? '');
  const [response, setResponse] = useState<FullResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestSeq = useRef(0);

  const fetchData = useCallback(async (symbol: string, refresh = false) => {
    if (!symbol) return;
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const qs = refresh ? '?refresh=true' : '';
      const res = await fetch(`${API_BASE}/full-fundamentals/${encodeURIComponent(symbol)}${qs}`);
      if (seq !== requestSeq.current) return;
      if (res.ok) {
        const payload: FullResponse = await res.json();
        if (seq !== requestSeq.current) return;
        setResponse(payload);
      } else if (res.status === 404) {
        setError(`No data found for symbol "${symbol}"`);
        setResponse(null);
      } else {
        const err = await res.json().catch(() => ({ detail: `Failed to fetch (HTTP ${res.status})` }));
        setError(err.detail || `Failed to fetch deep fundamentals (HTTP ${res.status})`);
        setResponse(null);
      }
    } catch (e: any) {
      if (seq !== requestSeq.current) return;
      setError(e.message || 'Error connecting to backend');
      setResponse(null);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (urlSymbol) {
      setSelectedSymbol(urlSymbol);
      fetchData(urlSymbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSymbolSelect = (symbol: string) => {
    setSelectedSymbol(symbol);
    setResponse(null);
    fetchData(symbol);
  };

  const onRefresh = () => {
    if (selectedSymbol) fetchData(selectedSymbol, true);
  };

  const data = response?.data;
  const snapshot = data?.snapshot ?? ({} as Snapshot);
  const yf = data?.yfinance ?? ({} as YfinanceData);

  const shareholdingRows = Object.entries(snapshot.shareholding ?? {})
    .filter(([, v]) => v != null && v > 0)
    .map(([key, value]) => ({ name: key.charAt(0).toUpperCase() + key.slice(1), value: value as number, key }));

  const trendSeries: { key: string; label: string }[] = [
    { key: 'price_to_book', label: 'Price to Book' },
    { key: 'pe', label: 'P/E' },
    { key: 'roce', label: 'ROCE %' },
    { key: 'market_cap_to_sales', label: 'Market Cap / Sales' },
  ];

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Deep Fundamentals">
      <header className="flex items-center justify-between bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-cyan-500/20 p-2 rounded" aria-hidden="true">
            <LineChart className="text-cyan-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Deep Fundamentals</h1>
            <p className="text-xs font-mono text-[#888]">
              Screener.in (Scrapling) · Screener Chart API · yfinance
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <button
              onClick={onRefresh}
              disabled={loading}
              className="flex items-center gap-1.5 text-xs font-mono px-3 py-1.5 rounded border border-[#ffffff1a] bg-[#1a1c24] text-[#fafafa] hover:bg-[#ffffff0a] disabled:opacity-50 transition-colors"
              title="Re-fetch from sources (bypass cache)"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
              Refresh
            </button>
          )}
          <div className="w-64">
            <SymbolSearch
              lib={lib}
              onSymbolSelect={onSymbolSelect}
              placeholder="Search symbol..."
              initialValue={selectedSymbol}
            />
          </div>
        </div>
      </header>

      {loading && (
        <div className="flex-1 flex items-center justify-center" role="status" aria-live="polite">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Loader2 size={32} className="animate-spin text-cyan-400" aria-hidden="true" />
            <p>Fetching deep fundamentals for {selectedSymbol || 'symbol'}…</p>
            <p className="text-[11px]">Headless browser + chart API + analyst data</p>
          </div>
        </div>
      )}

      {error && !loading && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2" role="alert">
            <AlertTriangle size={32} className="opacity-30" aria-hidden="true" />
            <p className="text-red-400">{error}</p>
            <p className="text-[12px]">Try another symbol using the search box above.</p>
          </div>
        </div>
      )}

      {!loading && !error && !data && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Tag size={32} className="opacity-30" aria-hidden="true" />
            <p>Select a symbol to load its deep fundamentals.</p>
          </div>
        </div>
      )}

      {!loading && !error && data && (
        <>
          {data.warning && (
            <div className="flex items-center gap-2 text-[12px] font-mono text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-3 py-2" role="status">
              <AlertTriangle size={14} aria-hidden="true" />
              <span>{data.warning}</span>
            </div>
          )}

          <div className="flex items-center justify-between text-[12px] font-mono text-[#888]">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-cyan-400 font-bold">{yf.long_name || data.symbol}</span>
              {yf.sector && <span>· {yf.sector}</span>}
              {yf.industry && <span className="text-[#666]">· {yf.industry}</span>}
              {data.company_id && <span className="text-[#666]">· SC ID {data.company_id}</span>}
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <span className="flex items-center gap-1">
                {response?.cached ? <CircleDollarSign size={12} aria-hidden="true" /> : <Activity size={12} aria-hidden="true" />}
                {response?.cached ? 'Cached' : 'Fresh'}
              </span>
              {response?.last_updated && <span>{fmtDate(response.last_updated)}</span>}
            </div>
          </div>

          <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3" aria-label="Deep fundamental metrics">
            <MetricCard
              icon={<Activity size={14} aria-hidden="true" />}
              label="Current Price"
              value={fmtPrice(snapshot.current_price ?? yf.current_price)}
              sub="Latest price"
            />
            <MetricCard
              icon={<Wallet size={14} aria-hidden="true" />}
              label="Market Cap"
              value={fmtMcapCr(snapshot.market_cap_crore)}
              sub={snapshot.market_cap_crore != null ? 'Crore (Screener.in)' : undefined}
            />
            <MetricCard
              icon={<Scale size={14} aria-hidden="true" />}
              label="P/E"
              value={fmtNum(snapshot.pe ?? yf.pe)}
              sub="Price to earnings"
            />
            <MetricCard
              icon={<Coins size={14} aria-hidden="true" />}
              label="P/BV"
              value={fmtNum(latestOf(data.timeseries.price_to_book) ?? yf.price_to_book)}
              sub="Price to book value"
            />
            <MetricCard
              icon={<Percent size={14} aria-hidden="true" />}
              label="ROE"
              value={fmtGrowth(snapshot.roe ?? yf.roe)}
              sub="Return on equity"
              accent="text-emerald-400"
            />
            <MetricCard
              icon={<TrendingUp size={14} aria-hidden="true" />}
              label="ROCE"
              value={fmtGrowth(snapshot.roce ?? latestOf(data.timeseries.roce) ?? yf.roce)}
              sub="Return on capital employed"
              accent="text-emerald-400"
            />
            <MetricCard
              icon={<CircleDollarSign size={14} aria-hidden="true" />}
              label="Dividend Yield"
              value={fmtDY(snapshot.dividend_yield ?? yf.dividend_yield)}
              sub="Trailing dividend yield"
            />
            <MetricCard
              icon={<Tag size={14} aria-hidden="true" />}
              label="Book Value"
              value={fmtPrice(snapshot.book_value)}
              sub="Per share book value"
            />
            <MetricCard
              icon={<Coins size={14} aria-hidden="true" />}
              label="Face Value"
              value={fmtNum(snapshot.face_value)}
              sub="Face value per share"
            />
            <MetricCard
              icon={<Scale size={14} aria-hidden="true" />}
              label="Debt / Equity"
              value={fmtDE(yf.debt_to_equity)}
              sub="From yfinance"
            />
            <MetricCard
              icon={<Gauge size={14} aria-hidden="true" />}
              label="Beta"
              value={fmtNum(yf.beta)}
              sub="Volatility vs market"
            />
            <MetricCard
              icon={<TrendingUp size={14} aria-hidden="true" />}
              label="Revenue Growth"
              value={fmtGrowth(yf.revenue_growth)}
              sub="YoY revenue growth"
              accent={yf.revenue_growth != null && yf.revenue_growth < 0 ? 'text-red-400' : 'text-emerald-400'}
            />
            <MetricCard
              icon={<TrendingDown size={14} aria-hidden="true" />}
              label="Earnings Growth"
              value={fmtGrowth(yf.earnings_growth)}
              sub="YoY earnings growth"
              accent={yf.earnings_growth != null && yf.earnings_growth < 0 ? 'text-red-400' : 'text-emerald-400'}
            />
            <MetricCard
              icon={<Award size={14} aria-hidden="true" />}
              label="Analyst Rating"
              value={REC_LABEL[(yf.recommendation_key || '').toLowerCase()] || yf.recommendation_key || '—'}
              sub={
                yf.analyst_count != null || yf.recommendation_mean != null
                  ? `${yf.analyst_count ?? '—'} analysts · mean ${fmtNum(yf.recommendation_mean)}`
                  : undefined
              }
              accent="text-emerald-400"
            />
            <MetricCard
              icon={<Building2 size={14} aria-hidden="true" />}
              label="Target Price"
              value={fmtPrice(yf.target_mean_price)}
              sub="Mean analyst target"
            />
          </section>

          {response?.insights && response.insights.length > 0 && (
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-2" aria-label="Fundamental insights">
              {response.insights.map((ins, i) => (
                <InsightCard key={`${ins.key}-${i}`} insight={ins} />
              ))}
            </section>
          )}

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4" aria-label="Shareholding and trends">
            {shareholdingRows.length > 0 && (
              <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
                <div className="flex items-center gap-2 text-xs font-mono text-[#888] mb-2">
                  <Users size={14} className="text-cyan-400" aria-hidden="true" />
                  <span className="font-semibold uppercase tracking-wider text-[#fafafa]">Shareholding</span>
                  <span>— latest quarter</span>
                </div>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={shareholdingRows}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={55}
                        outerRadius={90}
                        paddingAngle={2}
                        stroke="#1a1c24"
                      >
                        {shareholdingRows.map(entry => (
                          <Cell key={entry.key} fill={PIE_COLORS[entry.key] ?? '#22d3ee'} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{ background: '#1a1c24', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 8, fontSize: 12, fontFamily: 'monospace' }}
                        formatter={(value: any) => `${Number(value).toFixed(2)}%`}
                      />
                      <Legend formatter={(value: string) => <span style={{ color: '#888', fontSize: 11 }}>{value}</span>} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {trendSeries.some(t => (data.timeseries[t.key]?.length ?? 0) > 0) && (
              <div className="grid grid-cols-1 gap-3">
                {trendSeries.map(t => (
                  <TrendChart key={t.key} title={t.label} series={data.timeseries[t.key] ?? []} />
                ))}
              </div>
            )}
          </section>

          <section className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden" aria-label="Annual ratios table">
            <div className="px-4 py-3 flex items-center gap-2 text-xs font-mono text-[#888] border-b border-[#ffffff0a]">
              <ShieldCheck size={14} className="text-cyan-400" aria-hidden="true" />
              <span className="font-semibold uppercase tracking-wider text-[#fafafa]">Annual Ratios</span>
              <span>— Screener.in</span>
            </div>
            {Object.keys(data.ratios).length === 0 ? (
              <div className="px-4 py-6 text-center text-xs font-mono text-[#888]">
                No annual ratios table available for this company.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono whitespace-nowrap" role="grid">
                  <thead className="text-[#888]">
                    <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08)' }}>
                      <th scope="col" className="px-4 py-2 font-semibold uppercase tracking-wider">Ratio</th>
                      {Object.keys(data.ratios).length > 0 &&
                        Object.keys(data.ratios[Object.keys(data.ratios)[0]] || {}).map(d => (
                          <th key={d} scope="col" className="px-4 py-2 font-semibold uppercase tracking-wider">{d.slice(0, 7)}</th>
                        ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#ffffff0a]">
                    {Object.entries(data.ratios).map(([label, series]) => (
                      <tr key={label} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-4 py-2 text-[#fafafa]">{label}</td>
                        {Object.entries(series).map(([d, v]) => (
                          <td key={d} className="px-4 py-2 text-[#ccc]">{fmtNum(v)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
