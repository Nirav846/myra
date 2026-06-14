import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { API_BASE } from '../config';

interface Holding {
  symbol: string;
  category: string;
  net_qty: number;
  avg_price: number;
  ltp: number | null;
  current_value: number;
  overall_pnl: number;
  overall_pnl_pct: number;
  day_pnl: number;
  day_pnl_pct: number;
  delivery_pct: number | null;
  delivery_trend: string;
  vs_sma50_pct: number | null;
  vs_52w_high_pct: number | null;
  pe: number | null;
  sector: string;
  alert: string | null;
}

interface SectorAllocation {
  sector: string;
  count: number;
  total_value: number;
  weight_pct: number;
}

interface ScannerOverlap {
  [symbol: string]: Record<string, any>;
}

interface Alert {
  symbol: string;
  alert_type: string;
  severity: string;
  detail: string;
}

interface RiskData {
  concentration: { top3_pct: number; holdings: { symbol: string; pct: number; value: number }[] };
  drawdown: { peak_value: number; peak_date: string; current_value: number; drawdown_pct: number; days_from_peak: number };
  diversification_score: number;
  diversification_rating: string;
}

interface Freshness {
  prices_from: string;
  fundamentals_cached: string;
  fundamentals_coverage_pct: number;
}

interface PortfolioData {
  status: string;
  summary: {
    total_invested: number;
    total_current: number;
    overall_pnl: number;
    overall_pnl_pct: number;
    day_pnl: number;
    day_pnl_pct: number;
    holdings_count: number;
    last_refresh: string;
  };
  holdings: Holding[];
  sector_allocation: SectorAllocation[];
  scanner_overlap: ScannerOverlap;
  alerts: Alert[];
  risk: RiskData;
  freshness: Freshness;
}

const formatIndian = (n: number | null | undefined): string => {
  if (n == null) return '\u2014';
  return '\u20B9' + Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
};

const formatIndianDec = (n: number | null | undefined): string => {
  if (n == null) return '\u2014';
  return '\u20B9' + Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const formatPct = (n: number | null | undefined): string => {
  if (n == null) return '\u2014';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
};

const formatQty = (n: number): string => n.toLocaleString('en-IN');

const SECTOR_COLORS = [
  'bg-blue-500', 'bg-emerald-500', 'bg-amber-500', 'bg-violet-500',
  'bg-rose-500', 'bg-cyan-500', 'bg-lime-500', 'bg-fuchsia-500',
  'bg-teal-500', 'bg-orange-500', 'bg-indigo-500', 'bg-pink-500',
];

export default function PortfolioView() {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<string>('symbol');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [riskExpanded, setRiskExpanded] = useState(false);
  const mountedRef = useRef(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/portfolio`);
      if (!mountedRef.current) return;
      if (!res.ok) {
        setError(`Server returned ${res.status}`);
        setLoading(false);
        return;
      }
      const result: PortfolioData = await res.json();
      if (!mountedRef.current) return;
      if (result.status === 'empty') {
        setData(result);
        setError(null);
      } else if (result.status === 'ok') {
        setData(result);
        setError(null);
      } else {
        setError(result.status);
      }
      setLoading(false);
    } catch (e: any) {
      if (!mountedRef.current) return;
      setError(e.message || 'Failed to load portfolio data.');
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    fetchPortfolio();
    intervalRef.current = setInterval(fetchPortfolio, 300000);
    return () => {
      mountedRef.current = false;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchPortfolio]);

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sortedHoldings = useMemo(() => {
    if (!data?.holdings) return [];
    const arr = [...data.holdings];
    arr.sort((a, b) => {
      let va: any = (a as any)[sortKey];
      let vb: any = (b as any)[sortKey];
      if (va == null) va = -Infinity;
      if (vb == null) vb = -Infinity;
      if (typeof va === 'string') {
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return sortDir === 'asc' ? va - vb : vb - va;
    });
    return arr;
  }, [data, sortKey, sortDir]);

  const sortIndicator = (key: string) => {
    if (sortKey !== key) return ' \u2195';
    return sortDir === 'asc' ? ' \u2191' : ' \u2193';
  };

  const thClass = 'px-3 py-2 text-left text-[11px] font-mono text-[#888] cursor-pointer hover:text-white select-none whitespace-nowrap border-b border-[#ffffff1a]';
  const tdClass = 'px-3 py-2 text-[11px] font-mono whitespace-nowrap border-b border-[#ffffff0a]';

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-pulse text-[#888] font-mono text-sm">Loading portfolio...</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-6 py-4 text-red-400 font-mono text-sm">
          Failed to load portfolio data: {error}
        </div>
      </div>
    );
  }

  if (data?.status === 'empty') {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg px-8 py-6 text-center max-w-lg">
          <div className="text-3xl mb-3">\uD83D\uDCB1</div>
          <h3 className="text-[#fafafa] font-semibold mb-2">No Portfolio Data</h3>
          <p className="text-[#888] font-mono text-xs leading-relaxed">
            {data.message}
          </p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-[#888] font-mono text-sm">No data available.</div>
      </div>
    );
  }

  const { summary, sector_allocation, alerts, risk, freshness } = data;
  const hasAlerts = alerts.length > 0;

  return (
    <div className="flex flex-col gap-4">
      {/* ── Alerts Banner ── */}
      {hasAlerts && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-amber-400 text-sm">⚠ ALERTS</span>
            <span className="text-[10px] text-[#888] font-mono">({alerts.length})</span>
          </div>
          {alerts.map((a, i) => (
            <div key={i} className="text-[11px] font-mono text-amber-300 flex items-start gap-2 mb-1 last:mb-0">
              <span className="font-bold shrink-0">{a.symbol}:</span>
              <span>{a.alert_type.replace('_', ' ')} \u2014 {a.detail}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Summary Cards ── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <SummaryCard label="Invested" value={formatIndianDec(summary.total_invested)} color="text-[#fafafa]" />
        <SummaryCard
          label="Current Value"
          value={formatIndianDec(summary.total_current)}
          color={summary.overall_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <SummaryCard
          label="Overall P&L"
          value={`${summary.overall_pnl >= 0 ? '+' : '-'}${formatIndianDec(summary.overall_pnl)} (${formatPct(summary.overall_pnl_pct)})`}
          color={summary.overall_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <SummaryCard
          label="Day P&L"
          value={`${summary.day_pnl >= 0 ? '+' : ''}${formatIndianDec(summary.day_pnl)} (${formatPct(summary.day_pnl_pct)})`}
          color={summary.day_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <SummaryCard label="Holdings" value={String(summary.holdings_count)} color="text-[#fafafa]" />
        <SummaryCard label="Last Refresh" value={summary.last_refresh} color="text-[#888]" small />
      </div>

      {/* ── Holdings Table ── */}
      <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] overflow-hidden">
        <div className="px-4 py-2 border-b border-[#ffffff1a] flex items-center justify-between">
          <h3 className="text-xs font-semibold text-[#fafafa]">Holdings</h3>
          <span className="text-[10px] text-[#888] font-mono">{sortedHoldings.length} positions</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-[#0e1117]/50">
                <th className={thClass} onClick={() => toggleSort('symbol')}>Symbol{sortIndicator('symbol')}</th>
                <th className={thClass} onClick={() => toggleSort('net_qty')}>Qty{sortIndicator('net_qty')}</th>
                <th className={thClass} onClick={() => toggleSort('avg_price')}>Avg{sortIndicator('avg_price')}</th>
                <th className={thClass} onClick={() => toggleSort('ltp')}>LTP{sortIndicator('ltp')}</th>
                <th className={thClass} onClick={() => toggleSort('current_value')}>Value{sortIndicator('current_value')}</th>
                <th className={thClass} onClick={() => toggleSort('overall_pnl_pct')}>P&L%{sortIndicator('overall_pnl_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('day_pnl_pct')}>Day%{sortIndicator('day_pnl_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('delivery_pct')}>Del{sortIndicator('delivery_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('vs_sma50_pct')}>vs SMA50{sortIndicator('vs_sma50_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('pe')}>P/E{sortIndicator('pe')}</th>
                <th className={thClass} onClick={() => toggleSort('sector')}>Sector{sortIndicator('sector')}</th>
              </tr>
            </thead>
            <tbody>
              {sortedHoldings.map((h) => (
                <tr key={h.symbol} className="hover:bg-[#ffffff05] transition-colors">
                  <td className={`${tdClass} font-bold text-[#fafafa]`}>{h.symbol}</td>
                  <td className={tdClass}>{formatQty(h.net_qty)}</td>
                  <td className={tdClass}>{formatIndianDec(h.avg_price)}</td>
                  <td className={tdClass}>{h.ltp ? formatIndianDec(h.ltp) : '\u2014'}</td>
                  <td className={tdClass}>{formatIndianDec(h.current_value)}</td>
                  <td className={`${tdClass} ${h.overall_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {formatPct(h.overall_pnl_pct)}
                  </td>
                  <td className={`${tdClass} ${h.day_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {formatPct(h.day_pnl_pct)}
                  </td>
                  <td className={tdClass}>
                    {h.delivery_pct != null ? `${h.delivery_pct.toFixed(1)}% ${h.delivery_trend}` : '\u2014'}
                  </td>
                  <td className={`${tdClass} ${h.vs_sma50_pct != null ? (h.vs_sma50_pct >= 0 ? 'text-green-400' : 'text-red-400') : ''}`}>
                    {h.vs_sma50_pct != null ? `${h.vs_sma50_pct >= 0 ? '+' : ''}${h.vs_sma50_pct.toFixed(1)}%` : '\u2014'}
                  </td>
                  <td className={tdClass}>{h.pe != null ? h.pe.toFixed(1) : '\u2014'}</td>
                  <td className={tdClass}>{h.sector}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Sector Allocation ── */}
      {sector_allocation.length > 0 && (
        <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-4">
          <h3 className="text-xs font-semibold text-[#fafafa] mb-3">Sector Allocation</h3>
          <div className="flex flex-col gap-2">
            {sector_allocation.map((s, i) => (
              <div key={s.sector} className="flex items-center gap-3">
                <span className="text-[11px] font-mono text-[#fafafa] w-24 shrink-0 truncate">{s.sector}</span>
                <div className="flex-1 h-5 bg-[#0e1117] rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${SECTOR_COLORS[i % SECTOR_COLORS.length]}`}
                    style={{ width: `${Math.max(s.weight_pct, 1)}%` }}
                  />
                </div>
                <span className="text-[11px] font-mono text-[#888] w-32 text-right shrink-0">
                  {s.weight_pct.toFixed(1)}% ({formatIndianDec(s.total_value)})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Scanner Overlap ── */}
      <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-4">
        <h3 className="text-xs font-semibold text-[#fafafa] mb-3">Scanner Signals on Your Holdings</h3>
        {(() => {
          const symbolsWithSignals = Object.entries(data.scanner_overlap).filter(
            ([_, scanners]) => Object.values(scanners).some((v: any) => v != null)
          );
          if (symbolsWithSignals.length === 0) {
            return <p className="text-[#888] font-mono text-[11px]">No active scanner signals on your holdings.</p>;
          }
          return (
            <div className="flex flex-col gap-2">
              {symbolsWithSignals.map(([sym, scanners]) => {
                const active = Object.entries(scanners).filter(([_, v]) => v != null) as [string, any][];
                return (
                  <div key={sym} className="flex items-center gap-2 text-[11px] font-mono">
                    <span className="font-bold text-[#fafafa] w-28 shrink-0">{sym}</span>
                    <div className="flex flex-wrap gap-1.5">
                      {active.map(([name, val]) => {
                        let display: string;
                        if (typeof val === 'number') display = `${name} (${val})`;
                        else if (typeof val === 'object' && val !== null) {
                          const grade = val.grade || val.score || val.signal || '';
                          display = `${name}${grade ? ` (${grade})` : ''}`;
                        } else display = `${name} (${String(val)})`;
                        return (
                          <span
                            key={name}
                            className="px-2 py-0.5 bg-[#ffffff0a] rounded text-[10px] text-cyan-400 border border-[#ffffff1a]"
                          >
                            {display}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()}
      </div>

      {/* ── Risk Metrics (Collapsible) ── */}
      <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] overflow-hidden">
        <button
          onClick={() => setRiskExpanded(!riskExpanded)}
          className="w-full px-4 py-2.5 flex items-center justify-between text-xs font-semibold text-[#fafafa] hover:bg-[#ffffff05] transition-colors"
        >
          <span>Risk Metrics</span>
          <span className="text-[#888] text-[11px] font-mono">{riskExpanded ? '\u25B2' : '\u25BC'}</span>
        </button>
        {riskExpanded && (
          <div className="px-4 pb-4 grid grid-cols-1 md:grid-cols-3 gap-4 text-[11px] font-mono">
            <div className="bg-[#0e1117] rounded-lg p-3 border border-[#ffffff0a]">
              <div className="text-[#888] mb-1">Concentration</div>
              <div className="text-[#fafafa] font-semibold">
                Top 3 holdings = {risk.concentration.top3_pct.toFixed(1)}% of portfolio
              </div>
              {risk.concentration.holdings.slice(0, 3).map((h: any) => (
                <div key={h.symbol} className="text-[10px] text-[#888] mt-0.5">
                  {h.symbol}: {h.pct.toFixed(1)}%
                </div>
              ))}
            </div>
            <div className="bg-[#0e1117] rounded-lg p-3 border border-[#ffffff0a]">
              <div className="text-[#888] mb-1">Drawdown</div>
              <div className={`font-semibold ${risk.drawdown.drawdown_pct < 0 ? 'text-red-400' : 'text-green-400'}`}>
                {risk.drawdown.drawdown_pct.toFixed(1)}% from peak
              </div>
              <div className="text-[10px] text-[#888] mt-0.5">
                Peak: {formatIndianDec(risk.drawdown.peak_value)} on {risk.drawdown.peak_date || 'N/A'}
              </div>
              <div className="text-[10px] text-[#888]">
                {risk.drawdown.days_from_peak > 0 ? `${risk.drawdown.days_from_peak} days since peak` : 'At peak'}
              </div>
            </div>
            <div className="bg-[#0e1117] rounded-lg p-3 border border-[#ffffff0a]">
              <div className="text-[#888] mb-1">Diversification</div>
              <div className="text-[#fafafa] font-semibold">
                {risk.diversification_score}/100
              </div>
              <div className="text-[10px] text-[#888] mt-0.5">{risk.diversification_rating}</div>
            </div>
          </div>
        )}
      </div>

      {/* ── Data Freshness Footer ── */}
      <div className="text-[10px] font-mono text-[#555] text-center py-2 border-t border-[#ffffff0a]">
        Prices: {freshness.prices_from} | Fundamentals: {freshness.fundamentals_cached} | 
        Coverage: {freshness.fundamentals_coverage_pct}% | Auto-refreshes every 5 min
      </div>
    </div>
  );
}

function SummaryCard({ label, value, color, small }: { label: string; value: string; color: string; small?: boolean }) {
  return (
    <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-3 flex flex-col gap-1">
      <span className="text-[10px] font-mono text-[#888] uppercase tracking-wider">{label}</span>
      <span className={`${small ? 'text-[11px]' : 'text-sm'} font-semibold font-mono ${color}`}>{value}</span>
    </div>
  );
}
