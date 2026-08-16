import { useState, useEffect, useCallback, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { Librarian } from '../lib/Librarian';
import { SymbolSearch } from '../components/SymbolSearch';
import { API_BASE } from '../config';
import {
  Loader2,
  AlertTriangle,
  Activity,
  TrendingUp,
  Percent,
  Coins,
  Building2,
  ShieldCheck,
  Wallet,
  CalendarClock,
  Scale,
  LineChart,
  Tag,
} from 'lucide-react';

interface CorporateAction {
  action_type: string;
  ex_date: string | null;
  date: string | null;
}

interface FundamentalsData {
  symbol: string;
  market_cap: number | null;
  pe: number | null;
  net_margin: number | null;
  promoter_holding_pct: number | null;
  sector: string | null;
  free_float_mcap: number | null;
  free_float_pct: number | null;
  pbv: number | null;
  roce: number | null;
  last_updated: string | null;
  close: number | null;
  '52w_high': number | null;
  '52w_low': number | null;
  corporate_actions: CorporateAction[];
}

const fmtPrice = (v: number | null): string =>
  v == null ? '—' : `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

const fmtNum = (v: number | null, digits = 2): string =>
  v == null ? '—' : v.toLocaleString('en-IN', { maximumFractionDigits: digits });

const fmtPct = (v: number | null): string => {
  if (v == null) return '—';
  // Stored values are fractions (e.g. 0.0665 = 6.65%); guard against
  // already-percentage outliers.
  const pct = Math.abs(v) <= 1.5 ? v * 100 : v;
  return `${pct.toLocaleString('en-IN', { maximumFractionDigits: 2 })}%`;
};

const fmtMcap = (v: number | null): string => {
  if (v == null) return '—';
  const cr = v / 1e7;
  if (Math.abs(cr) >= 1e5) {
    return `₹${(cr / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 })} L Cr`;
  }
  if (Math.abs(cr) >= 1e3) {
    return `₹${(cr / 1e3).toLocaleString('en-IN', { maximumFractionDigits: 1 })}K Cr`;
  }
  return `₹${cr.toLocaleString('en-IN', { maximumFractionDigits: 1 })} Cr`;
};

const fmtDate = (v: string | null): string => {
  if (!v) return '—';
  try {
    const d = new Date(v);
    if (isNaN(d.getTime())) return v;
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch {
    return v;
  }
};

interface MetricCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}

function MetricCard({ icon, label, value, sub, accent = 'text-cyan-400' }: MetricCardProps) {
  return (
    <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-[12px] text-[#888] font-mono uppercase tracking-wider">
        <span className={accent}>{icon}</span>
        {label}
      </div>
      <div className="text-lg font-bold text-[#fafafa] leading-tight" data-testid={`metric-${label}`}>
        {value}
      </div>
      {sub && <div className="text-[11px] font-mono text-[#888]">{sub}</div>}
    </div>
  );
}

export default function FundamentalsView({ lib }: { lib: Librarian }) {
  const location = useLocation();
  const urlSymbol = new URLSearchParams(location.search).get('symbol') || undefined;

  const [selectedSymbol, setSelectedSymbol] = useState<string>(urlSymbol ?? '');
  const [data, setData] = useState<FundamentalsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedSymbol, setLoadedSymbol] = useState<string>('');

  const requestSeq = useRef(0);

  const fetchFundamentals = useCallback(async (symbol: string) => {
    if (!symbol) return;
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const res = await fetch(`${API_BASE}/fundamentals/${encodeURIComponent(symbol)}`);
      if (seq !== requestSeq.current) return;
      if (res.ok) {
        const payload: FundamentalsData = await res.json();
        if (seq !== requestSeq.current) return;
        setData(payload);
        setLoadedSymbol(payload.symbol);
      } else if (res.status === 404) {
        setError(`No data found for symbol "${symbol}"`);
        setLoadedSymbol(symbol);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Failed to fetch fundamentals' }));
        setError(err.detail || `Failed to fetch fundamentals (HTTP ${res.status})`);
      }
    } catch (e: any) {
      if (seq !== requestSeq.current) return;
      setError(e.message || 'Error connecting to backend');
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, []);

  // Pre-select symbol from URL (?symbol=) on mount.
  useEffect(() => {
    if (urlSymbol) {
      setSelectedSymbol(urlSymbol);
      fetchFundamentals(urlSymbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSymbolSelect = (symbol: string) => {
    setSelectedSymbol(symbol);
    fetchFundamentals(symbol);
  };

  const lastUpdated = data?.last_updated
    ? fmtDate(data.last_updated)
    : null;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Fundamentals">
      <header className="flex items-center justify-between bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-cyan-500/20 p-2 rounded" aria-hidden="true">
            <LineChart className="text-cyan-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Fundamentals</h1>
            <p className="text-xs font-mono text-[#888]">
              Screener.in PBV / ROCE · Valuation · Price Action · Corporate Actions
            </p>
          </div>
        </div>
        <div className="w-64">
          <SymbolSearch
            lib={lib}
            onSymbolSelect={onSymbolSelect}
            placeholder="Search symbol..."
            initialValue={selectedSymbol}
          />
        </div>
      </header>

      {loading && (
        <div className="flex-1 flex items-center justify-center" role="status" aria-live="polite">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Loader2 size={32} className="animate-spin text-cyan-400" aria-hidden="true" />
            <p>Loading {selectedSymbol || 'fundamentals'}…</p>
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
            <p>Select a symbol to view its fundamentals.</p>
          </div>
        </div>
      )}

      {!loading && !error && data && (
        <>
          <div className="flex items-center justify-between text-[12px] font-mono text-[#888]">
            <div>
              Showing fundamentals for <span className="text-cyan-400 font-bold">{data.symbol}</span>
              {data.sector ? <span className="text-[#888]"> · {data.sector}</span> : null}
            </div>
            {lastUpdated && (
              <div className="flex items-center gap-1">
                <CalendarClock size={12} aria-hidden="true" />
                <span>Screener.in updated {lastUpdated}</span>
              </div>
            )}
          </div>

          <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3" aria-label="Fundamental metrics">
            <MetricCard
              icon={<Activity size={14} aria-hidden="true" />}
              label="Current Price"
              value={fmtPrice(data.close)}
              sub="Latest close"
            />
            <MetricCard
              icon={<TrendingUp size={14} aria-hidden="true" />}
              label="52W High / Low"
              value={`${fmtPrice(data['52w_high'])} / ${fmtPrice(data['52w_low'])}`}
              sub="Trailing 52 weeks"
            />
            <MetricCard
              icon={<Scale size={14} aria-hidden="true" />}
              label="P/E"
              value={fmtNum(data.pe)}
              sub="Price to earnings"
            />
            <MetricCard
              icon={<Coins size={14} aria-hidden="true" />}
              label="P/BV"
              value={fmtNum(data.pbv)}
              sub="Price to book value"
            />
            <MetricCard
              icon={<Percent size={14} aria-hidden="true" />}
              label="ROCE"
              value={data.roce != null ? `${fmtNum(data.roce)}%` : '—'}
              sub="Return on capital employed"
              accent="text-emerald-400"
            />
            <MetricCard
              icon={<Wallet size={14} aria-hidden="true" />}
              label="Market Cap"
              value={fmtMcap(data.market_cap)}
              sub="Total market capitalization"
            />
            <MetricCard
              icon={<Percent size={14} aria-hidden="true" />}
              label="Net Margin"
              value={fmtPct(data.net_margin)}
              sub="Net profit margin"
            />
            <MetricCard
              icon={<ShieldCheck size={14} aria-hidden="true" />}
              label="Promoter Holding"
              value={data.promoter_holding_pct != null ? `${fmtNum(data.promoter_holding_pct)}%` : '—'}
              sub="Promoter shareholding"
            />
            <MetricCard
              icon={<Building2 size={14} aria-hidden="true" />}
              label="Free Float"
              value={fmtMcap(data.free_float_mcap)}
              sub={
                data.free_float_pct != null
                  ? `Free-float equity ${fmtNum(data.free_float_pct)}%`
                  : 'Free-float market cap'
              }
            />
            <MetricCard
              icon={<Tag size={14} aria-hidden="true" />}
              label="Sector"
              value={data.sector || '—'}
              sub="GICS sector classification"
            />
          </section>

          <section className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden" aria-label="Recent corporate actions">
            <div className="px-4 py-3 flex items-center gap-2 text-xs font-mono text-[#888] border-b border-[#ffffff0a]">
              <CalendarClock size={14} className="text-cyan-400" aria-hidden="true" />
              <span className="font-semibold uppercase tracking-wider text-[#fafafa]">
                Recent Corporate Actions
              </span>
              <span className="text-[#888]">— last 12 months</span>
            </div>
            <table className="w-full text-left text-xs font-mono whitespace-nowrap" role="grid" aria-label="Corporate actions in the last 12 months" aria-rowcount={data.corporate_actions.length}>
              <thead className="text-[#888]">
                <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08)' }}>
                  <th role="columnheader" scope="col" className="px-4 py-2 font-semibold uppercase tracking-wider">Date</th>
                  <th role="columnheader" scope="col" className="px-4 py-2 font-semibold uppercase tracking-wider">Action</th>
                  <th role="columnheader" scope="col" className="px-4 py-2 font-semibold uppercase tracking-wider">Ex-Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#ffffff0a]">
                {data.corporate_actions.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="px-4 py-6 text-center text-[#888]">No corporate actions in the last 12 months.</td>
                  </tr>
                ) : (
                  data.corporate_actions.map((ca, index) => (
                    <tr key={`${ca.date}-${index}`} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                      <td className="px-4 py-2 text-[#ccc]">{fmtDate(ca.date)}</td>
                      <td className="px-4 py-2 text-[#fafafa]">{ca.action_type}</td>
                      <td className="px-4 py-2 text-[#888]">{fmtDate(ca.ex_date)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </section>
        </>
      )}
    </main>
  );
}
