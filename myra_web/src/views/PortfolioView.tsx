import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { API_BASE } from '../config';
import { TrendingUp, Plus, Edit, Trash2, X, Check, Loader2, AlertTriangle, ChevronDown } from 'lucide-react';

interface SignalDefinition {
  key: string;
  label: string;
  description: string;
  suggestion: string;
  severity: 'bullish' | 'neutral' | 'bearish' | 'info';
  icon: string;
}

const SCANNER_SIGNAL_CONFIG: SignalDefinition[] = [
  {
    key: 'FloatExhaustion',
    label: 'Float Exhaustion',
    description: 'Available shares being absorbed from the market',
    suggestion: 'Watch for price confirmation. Add if price starts rising on high delivery.',
    severity: 'bullish',
    icon: '\uD83E\uDEAB',
  },
  {
    key: 'FloatExh',
    label: 'Float Exhaustion',
    description: 'Available shares being absorbed from the market',
    suggestion: 'Watch for price confirmation. Add if price starts rising on high delivery.',
    severity: 'bullish',
    icon: '\uD83E\uDEAB',
  },
  {
    key: 'Darvas',
    label: 'Darvas Box',
    description: 'Price consolidating in a defined box range',
    suggestion: 'Buy near box bottom, sell near box top. Grade {grade} pattern.',
    severity: 'neutral',
    icon: '\uD83D\uDCE6',
  },
  {
    key: 'OpFinger',
    label: 'Operator Fingerprint',
    description: 'ATR compression + delivery drift. Stock is coiling.',
    suggestion: 'Wait for expansion on high delivery before entering.',
    severity: 'neutral',
    icon: '\uD83D\uDD0D',
  },
  {
    key: 'SeasDel',
    label: 'Seasonal Delivery',
    description: 'Historical delivery surge during this period',
    suggestion: 'Monitor if delivery confirms the seasonal pattern this year.',
    severity: 'info',
    icon: '\uD83D\uDCC5',
  },
  {
    key: 'Trigger',
    label: 'The Trigger',
    description: '4-gate technical setup: float utilisation, volume pinch, price range, smart-float',
    suggestion: 'Grade {grade} setup. Review gate details before acting.',
    severity: 'bullish',
    icon: '\u26A1',
  },
  {
    key: 'InvisibleHand',
    label: 'Invisible Hand',
    description: 'Systematic accumulation when nobody is watching',
    suggestion: 'Score {grade} \u2014 higher scores indicate stronger accumulation.',
    severity: 'bullish',
    icon: '\uD83D\uDC41',
  },
  {
    key: 'Wyckoff',
    label: 'Wyckoff Automaton',
    description: 'Accumulation/distribution phase detection',
    suggestion: 'Phase: {grade}. Check chart structure for confirmation.',
    severity: 'neutral',
    icon: '\uD83E\uDD16',
  },
  {
    key: 'LiqFlip',
    label: 'Liquidity Flip',
    description: 'Churn \u2192 conviction flip signal',
    suggestion: 'Delivery pattern shifting. Confirm with volume.',
    severity: 'bullish',
    icon: '\uD83D\uDD04',
  },
  {
    key: 'MultiBag',
    label: 'Multibagger Pro',
    description: 'Multibagger potential with strong delivery trend',
    suggestion: 'Grade {grade}. Review DAR median and base tightness.',
    severity: 'bullish',
    icon: '\uD83D\uDE80',
  },
];

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
  industry?: string | null;
  yf_sector?: string | null;
  alert: string | null;
  operating_margin?: number | null;
  gross_margin?: number | null;
  free_cash_flow_yield?: number | null;
  current_ratio?: number | null;
  quick_ratio?: number | null;
  payout_ratio?: number | null;
  promoter_holding?: number | null;
  market_cap?: number | null;
  beta?: number | null;
  morningstar_rating?: number;
  morningstar_fields_available?: number;
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
  message?: string;
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

const formatTimestamp = (): string => {
  const now = new Date();
  const hours = now.getHours().toString().padStart(2, '0');
  const mins = now.getMinutes().toString().padStart(2, '0');
  return `Today ${hours}:${mins} IST`;
};

function renderStars(rating: number | null | undefined): string {
  if (rating == null) return '\u2014';
  const filled = Math.max(1, Math.min(5, Math.round(rating)));
  return '\u2605'.repeat(filled) + '\u2606'.repeat(5 - filled);
}

function formatMarketCap(mc: number | null | undefined): string {
  if (mc == null) return '\u2014';
  const cr = mc / 1e7;
  if (cr >= 1000) return `${(cr / 1000).toFixed(1)}K Cr`;
  return `${cr.toFixed(0)} Cr`;
}

function fcfYieldColor(v: number | null | undefined): string {
  if (v == null) return '';
  if (v > 5) return 'text-green-400';
  if (v > 2) return 'text-amber-400';
  return 'text-red-400';
}

function promoterColor(v: number | null | undefined): string {
  if (v == null) return '';
  if (v > 50) return 'text-green-400';
  if (v > 30) return 'text-amber-400';
  return 'text-red-400';
}

function currentRatioColor(v: number | null | undefined): string {
  if (v == null) return '';
  if (v > 1.5) return 'text-green-400';
  if (v > 1.0) return 'text-amber-400';
  return 'text-red-400';
}

function peColor(v: number | null | undefined): string {
  if (v == null) return '';
  if (v < 0) return 'text-red-400';
  if (v < 15) return 'text-green-400';
  if (v < 25) return 'text-amber-400';
  return 'text-red-400';
}

const SECTOR_COLORS: Record<string, string> = {
  'Basic Materials': 'bg-amber-600',
  'Industrials': 'bg-blue-600',
  'Utilities': 'bg-green-600',
  'Technology': 'bg-purple-600',
  'Energy': 'bg-red-600',
  'Financial': 'bg-yellow-600',
  'Consumer': 'bg-pink-600',
  'Healthcare': 'bg-teal-600',
  'Unknown': 'bg-gray-600',
};

export default function PortfolioView() {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<string>('symbol');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [riskExpanded, setRiskExpanded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState<{type: 'success' | 'error'; message: string} | null>(null);
  const [lastRefreshedLabel, setLastRefreshedLabel] = useState<string>('');
  const [showFundamentals, setShowFundamentals] = useState(false);
  const [showLivePrices, setShowLivePrices] = useState(false);
  const [livePrices, setLivePrices] = useState<Record<string, any>>({});
  const [liveLoading, setLiveLoading] = useState(false);
  const mountedRef = useRef(true);
  const autoRefreshDoneRef = useRef(false);
  const statusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [showAddModal, setShowAddModal] = useState(false);
  const [addForm, setAddForm] = useState({
    symbol: '',
    qty: '',
    price: '',
    category: 'NSE EQ',
  });
  const [adding, setAdding] = useState(false);

  const [toasts, setToasts] = useState<Array<{id: string; type: 'success' | 'error'; message: string}>>([]);
  const addToast = (type: 'success' | 'error', message: string) => {
    const id = Math.random().toString(36).substr(2, 9);
    setToasts(prev => [...prev, { id, type, message }]);
  };
  const removeToast = (id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  };

  const [editingCell, setEditingCell] = useState<{symbol: string; field: 'net_qty' | 'avg_price'} | null>(null);
  const [editValue, setEditValue] = useState('');
  const [savingCell, setSavingCell] = useState<{symbol: string; field: string} | null>(null);

  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const [sectorWarningDismissed, setSectorWarningDismissed] = useState(false);

  const [showScannerQuickAdd, setShowScannerQuickAdd] = useState(false);
  const [signalsExpanded, setSignalsExpanded] = useState(true);
  const [scannerSymbolToAdd, setScannerSymbolToAdd] = useState<string>('');

  const [benchmark, setBenchmark] = useState<{portfolio_return: number; nifty_return: number; alpha: number; period?: string} | null>(null);
  const [showIndustry, setShowIndustry] = useState(false);
  const [industryLoading, setIndustryLoading] = useState(false);
  
  const fetchBenchmark = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/portfolio/benchmark`);
      const result = await res.json();
      if (result.status === 'ok') {
        setBenchmark(result.benchmark);
      }
    } catch {
      // silent fail
    }
  }, []);

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
        fetchBenchmark();
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

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshStatus(null);
    try {
      const refreshRes = await fetch(`${API_BASE}/portfolio/refresh`, { method: 'POST' });
      if (!mountedRef.current) return;
      const refreshResult = await refreshRes.json();
      const pricesUpdated = refreshResult?.result?.prices_updated ?? 0;
      const fundsUpdated = refreshResult?.result?.fundamentals_updated ?? 0;
      if (refreshRes.ok && refreshResult.status === 'ok') {
        await fetchPortfolio();
        fetchBenchmark();
        setRefreshStatus({
          type: 'success',
          message: `✓ Refreshed — ${pricesUpdated} prices, ${fundsUpdated} fundamentals updated`,
        });
      } else {
        await fetchPortfolio();
        setRefreshStatus({
          type: 'error',
          message: `Could not refresh — using cached data from ${data?.freshness?.prices_from || 'earlier'}`,
        });
      }
    } catch {
      if (!mountedRef.current) return;
      await fetchPortfolio();
      setRefreshStatus({
        type: 'error',
        message: `Could not refresh — using cached data from ${data?.freshness?.prices_from || 'earlier'}`,
      });
    } finally {
      setRefreshing(false);
      setLastRefreshedLabel(formatTimestamp());
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
      statusTimerRef.current = setTimeout(() => {
        if (mountedRef.current) setRefreshStatus(null);
      }, 4000);
    }
  }, [refreshing, fetchPortfolio, data?.freshness?.prices_from]);

  const [liveSource, setLiveSource] = useState<string>('');
  const fetchLivePrices = useCallback(async () => {
    setLiveLoading(true);
    try {
      const res = await fetch(`${API_BASE}/portfolio/live-prices`);
      const data = await res.json();
      if (data.status === 'ok') {
        setLivePrices(data.prices);
        setLiveSource(data.source || 'yfinance');
        setShowLivePrices(true);
      }
    } catch {
      // live fetch failed silently
    } finally {
      setLiveLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      setToasts(prev => {
        if (prev.length > 0 && Date.now() - parseInt(prev[0].id, 36) > 4000) {
          return prev.slice(1);
        }
        return prev;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const handleAddStock = async () => {
    if (!addForm.symbol.trim() || !addForm.qty || !addForm.price) {
      addToast('error', 'Symbol, quantity, and price are required');
      return;
    }
    setAdding(true);
    try {
      const res = await fetch(`${API_BASE}/portfolio/holdings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: addForm.symbol.toUpperCase().trim(),
          qty: parseInt(addForm.qty),
          avg_price: parseFloat(addForm.price),
          category: addForm.category,
        }),
      });
      const result = await res.json();
      if (result.status === 'ok') {
        addToast('success', result.message);
        setShowAddModal(false);
        setAddForm({ symbol: '', qty: '', price: '', category: 'NSE EQ' });
        await fetchPortfolio();
      } else {
        addToast('error', result.message || 'Failed to add stock');
      }
    } catch (e: any) {
      addToast('error', e.message || 'Network error');
    } finally {
      setAdding(false);
    }
  };

  const handleUpdateCell = async (symbol: string, field: 'net_qty' | 'avg_price') => {
    if (!editValue.trim()) {
      setEditingCell(null);
      return;
    }
    const value = field === 'net_qty' ? parseInt(editValue) : parseFloat(editValue);
    if (isNaN(value) || value <= 0) {
      addToast('error', `${field === 'net_qty' ? 'Quantity' : 'Price'} must be a positive number`);
      setEditingCell(null);
      return;
    }
    setSavingCell({ symbol, field });
    try {
      const res = await fetch(`${API_BASE}/portfolio/holdings/${symbol}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      });
      const result = await res.json();
      if (result.status === 'ok') {
        addToast('success', result.message);
        await fetchPortfolio();
      } else {
        addToast('error', result.message || 'Failed to update');
      }
    } catch (e: any) {
      addToast('error', e.message || 'Network error');
    } finally {
      setSavingCell(null);
      setEditingCell(null);
      setEditValue('');
    }
  };

  const handleDeleteHolding = async (symbol: string) => {
    try {
      const res = await fetch(`${API_BASE}/portfolio/holdings/${symbol}`, { method: 'DELETE' });
      const result = await res.json();
      if (result.status === 'ok') {
        addToast('success', result.message);
        await fetchPortfolio();
      } else {
        addToast('error', result.message || 'Failed to delete');
      }
    } catch (e: any) {
      addToast('error', e.message || 'Network error');
    } finally {
      setDeleteConfirm(null);
    }
  };

  const handleQuickAddFromScanner = (symbol: string) => {
    setAddForm({ ...addForm, symbol: symbol.toUpperCase() });
    setShowAddModal(true);
    setShowScannerQuickAdd(false);
  };

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    fetchPortfolio();
    return () => {
      mountedRef.current = false;
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
    };
  }, [fetchPortfolio]);

  useEffect(() => {
    if (!data || autoRefreshDoneRef.current) return;
    autoRefreshDoneRef.current = true;
    const priceDate = data.freshness?.prices_from;
    if (!priceDate) return;
    const lastPriceDate = new Date(priceDate);
    const today = new Date();
    const isWeekday = today.getDay() !== 0 && today.getDay() !== 6;
    if (lastPriceDate.toDateString() !== today.toDateString() && isWeekday) {
      handleRefresh();
    }
  }, [data, handleRefresh]);

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

  const allocationData = useMemo(() => {
    if (!showIndustry || !data?.holdings) return data?.sector_allocation || [];
    const grouped: Record<string, { count: number; total_value: number }> = {};
    let totalPortfolioValue = 0;
    
    for (const h of data.holdings) {
      const ind = h.industry || h.yf_sector || h.sector || 'Unknown';
      if (!grouped[ind]) grouped[ind] = { count: 0, total_value: 0 };
      grouped[ind].count += 1;
      grouped[ind].total_value += h.current_value;
      totalPortfolioValue += h.current_value;
    }
    
    return Object.entries(grouped)
      .map(([sector, stats]) => ({
        sector,
        count: stats.count,
        total_value: stats.total_value,
        weight_pct: totalPortfolioValue ? (stats.total_value / totalPortfolioValue) * 100 : 0
      }))
      .sort((a, b) => b.weight_pct - a.weight_pct);
  }, [data, showIndustry]);
  const resolveSignals = useMemo(() => {
    if (!data?.scanner_overlap) return [];

    const configMap = new Map<string, SignalDefinition>();
    SCANNER_SIGNAL_CONFIG.forEach(s => configMap.set(s.key, s));

    const results: Array<{
      symbol: string;
      signals: Array<SignalDefinition & { grade: string }>;
      signalCount: number;
      highestSeverity: SignalDefinition['severity'];
    }> = [];

    for (const [symbol, scannerSignals] of Object.entries(data.scanner_overlap)) {
      const enriched: Array<SignalDefinition & { grade: string }> = [];

      for (const [scannerKey, grade] of Object.entries(scannerSignals)) {
        const def = configMap.get(scannerKey);
        if (def) {
                  // Normalize grade — some scanner caches return full objects instead of strings
        let gradeStr = '';
        if (typeof grade === 'string') {
          gradeStr = grade;
        } else if (grade && typeof grade === 'object' && grade.grade) {
          gradeStr = grade.grade;  // Extract grade from candidate object
        } else if (grade && typeof grade === 'object') {
          gradeStr = '';  // Object without grade — presence-only signal
        }
        enriched.push({ ...def, grade: gradeStr });
        }
      }

      if (enriched.length > 0) {
        const severityOrder: Record<string, number> = { bullish: 3, neutral: 2, info: 1, bearish: 0 };
        results.push({
          symbol,
          signals: enriched,
          signalCount: enriched.length,
          highestSeverity: enriched.reduce((max, s) =>
            severityOrder[s.severity] > severityOrder[max] ? s.severity : max
          , 'info' as SignalDefinition['severity']),
        });
      }
    }

    return results.sort((a, b) => {
      if (b.signalCount !== a.signalCount) return b.signalCount - a.signalCount;
      const order: Record<string, number> = { bullish: 3, neutral: 2, info: 1, bearish: 0 };
      return order[b.highestSeverity] - order[a.highestSeverity];
    });
  }, [data?.scanner_overlap]);

  const severityColors: Record<string, string> = {
    bullish: 'border-l-green-500 bg-green-500/5',
    neutral: 'border-l-amber-500 bg-amber-500/5',
    bearish: 'border-l-red-500 bg-red-500/5',
    info: 'border-l-blue-500 bg-blue-500/5',
  };

  const severityDotColors: Record<string, string> = {
    bullish: 'bg-green-500',
    neutral: 'bg-amber-500',
    bearish: 'bg-red-500',
    info: 'bg-blue-500',
  };

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
      {/* ── Toast Notifications ── */}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`px-4 py-2 rounded text-[11px] font-mono pointer-events-auto cursor-pointer animate-in fade-in slide-in-from-right ${
              t.type === 'success'
                ? 'bg-emerald-500/20 border border-emerald-500/50 text-emerald-300'
                : 'bg-red-500/20 border border-red-500/50 text-red-300'
            }`}
            onClick={() => removeToast(t.id)}
          >
            {t.message}
          </div>
        ))}
      </div>

      {/* ── Add Stock Modal ── */}
      {showAddModal && (
        <div className="fixed inset-0 z-40 bg-black/80 flex items-center justify-center">
          <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg p-6 max-w-md w-full mx-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-[#fafafa]">Add / Append Stock</h3>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-[#888] hover:text-white"
              >
                <X size={16} />
              </button>
            </div>
            <div className="flex flex-col gap-3">
              <input
                type="text"
                placeholder="Symbol (e.g., INFY)"
                value={addForm.symbol}
                onChange={(e) => setAddForm({ ...addForm, symbol: e.target.value.toUpperCase() })}
                className="px-3 py-2 text-[11px] rounded bg-[#0e1117] border border-[#ffffff1a] text-[#fafafa] placeholder-[#888]"
              />
              <input
                type="number"
                placeholder="Quantity"
                value={addForm.qty}
                onChange={(e) => setAddForm({ ...addForm, qty: e.target.value })}
                className="px-3 py-2 text-[11px] rounded bg-[#0e1117] border border-[#ffffff1a] text-[#fafafa] placeholder-[#888]"
              />
              <input
                type="number"
                placeholder="Buy Price"
                value={addForm.price}
                onChange={(e) => setAddForm({ ...addForm, price: e.target.value })}
                step="0.01"
                className="px-3 py-2 text-[11px] rounded bg-[#0e1117] border border-[#ffffff1a] text-[#fafafa] placeholder-[#888]"
              />
              <select
                value={addForm.category}
                onChange={(e) => setAddForm({ ...addForm, category: e.target.value })}
                className="px-3 py-2 text-[11px] rounded bg-[#0e1117] border border-[#ffffff1a] text-[#fafafa]"
              >
                <option>NSE EQ</option>
                <option>NSE FO</option>
                <option>BSE EQ</option>
              </select>
              <div className="flex gap-2 mt-2">
                <button
                  onClick={handleAddStock}
                  disabled={adding}
                  className="flex-1 px-3 py-2 text-[11px] rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 flex items-center justify-center gap-1"
                >
                  {adding ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                  Add
                </button>
                <button
                  onClick={() => setShowAddModal(false)}
                  className="flex-1 px-3 py-2 text-[11px] rounded bg-[#ffffff0a] text-[#888] hover:text-white"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete Confirmation Modal ── */}
      {deleteConfirm && (
        <div className="fixed inset-0 z-40 bg-black/80 flex items-center justify-center">
          <div className="bg-[#1a1c24] border border-red-500/30 rounded-lg p-6 max-w-sm w-full mx-4">
            <h3 className="text-sm font-semibold text-red-400 mb-3">Remove from Portfolio?</h3>
            <p className="text-[11px] text-[#888] mb-4">
              Remove <span className="text-white font-bold">{deleteConfirm}</span> from your portfolio. This cannot be undone.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => handleDeleteHolding(deleteConfirm)}
                className="flex-1 px-3 py-2 text-[11px] rounded bg-red-600 text-white hover:bg-red-700 flex items-center justify-center gap-1"
              >
                <Trash2 size={12} /> Remove
              </button>
              <button
                onClick={() => setDeleteConfirm(null)}
                className="flex-1 px-3 py-2 text-[11px] rounded bg-[#ffffff0a] text-[#888] hover:text-white"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
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

      {/* ── Sector Concentration Warning ── */}
      {!sectorWarningDismissed && (() => {
        const highConcentration = sector_allocation.filter(s => s.weight_pct > 30);
        if (highConcentration.length === 0) return null;
        const top = highConcentration[0];
        return (
          <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-3 flex items-start gap-3">
            <AlertTriangle size={16} className="text-orange-400 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-[11px] font-mono text-orange-300">
                <span className="font-bold">{top.count} {top.sector}</span> holdings ({top.weight_pct.toFixed(1)}% of portfolio) — high sector concentration
              </p>
            </div>
            <button
              onClick={() => setSectorWarningDismissed(true)}
              className="text-[#888] hover:text-white shrink-0"
            >
              <X size={14} />
            </button>
          </div>
        );
      })()}

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
        <div
          onClick={handleRefresh}
          className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-3 flex flex-col gap-1 cursor-pointer hover:bg-[#ffffff08] transition-colors group"
          title="Click to refresh portfolio data"
        >
          <span className="text-[10px] font-mono text-[#888] uppercase tracking-wider">Last Refreshed</span>
          <span className="text-[11px] font-semibold font-mono text-[#888] flex items-center gap-1.5">
            {refreshing ? (
              <>
                <span className="inline-block w-3 h-3 border-2 border-[#888] border-t-transparent rounded-full animate-spin" />
                Refreshing...
              </>
            ) : (
              <>
                {lastRefreshedLabel || summary.last_refresh}
                <span className="opacity-0 group-hover:opacity-100 transition-opacity text-[10px] text-cyan-400">⟳</span>
              </>
            )}
          </span>
        </div>
        {benchmark && (
          <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-3 flex flex-col gap-1">
            <span className="text-[10px] font-mono text-[#888] uppercase tracking-wider">You vs Nifty</span>
            <span className="text-[11px] font-semibold font-mono">
              You: {benchmark.portfolio_return >= 0 ? '+' : ''}{Math.abs(benchmark.portfolio_return).toFixed(1)}% vs Nifty: {benchmark.nifty_return >= 0 ? '+' : ''}{Math.abs(benchmark.nifty_return).toFixed(1)}%
            </span>
            <span className="text-[10px] font-mono">
              {benchmark.portfolio_return >= benchmark.nifty_return
                ? '✓ Beating Nifty by ' + (benchmark.portfolio_return - benchmark.nifty_return).toFixed(1) + '%'
                : '⚠ Underperforming by ' + (benchmark.nifty_return - benchmark.portfolio_return).toFixed(1) + '%'}
            </span>
            <span className="text-[9px] font-mono text-[#888]">
              {benchmark.period}
            </span>
          </div>
        )}
         {benchmark === null && (
          <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-3 flex items-center justify-center">
            <span className="text-[10px] font-mono text-[#888]">Loading benchmark...</span>
          </div>
        )}
      </div>

      {/* ── Refresh Status Flash ── */}
      {refreshStatus && (
        <div
          className={`text-[11px] font-mono px-4 py-2 rounded-lg border animate-in fade-in ${
            refreshStatus.type === 'success'
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-amber-500/10 border-amber-500/30 text-amber-300'
          }`}
        >
          {refreshStatus.message}
        </div>
      )}

      {/* ── Toolbar ── */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => setShowAddModal(true)}
          className="px-3 py-1 text-[11px] rounded font-mono transition-colors bg-emerald-600 text-white hover:bg-emerald-700 flex items-center gap-1"
        >
          <Plus size={14} /> Add Stock
        </button>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="px-3 py-1 text-[11px] rounded font-mono transition-colors bg-[#ffffff0a] text-[#888] hover:text-white disabled:opacity-50"
        >
          {refreshing ? '\u23F3 Refreshing...' : '\u27F3 Refresh'}
        </button>
        <button
          onClick={() => setShowFundamentals(!showFundamentals)}
          className={`px-3 py-1 text-[11px] rounded font-mono transition-colors ${
            showFundamentals ? 'bg-blue-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-white'
          }`}
        >
          {'\uD83D\uDCCA'} Fundamentals {showFundamentals ? 'ON' : 'OFF'}
        </button>
        <button
          onClick={() => {
            if (showLivePrices) {
              setShowLivePrices(false);
              setTimeout(() => fetchLivePrices(), 50);
            } else {
              fetchLivePrices();
            }
          }}
          disabled={liveLoading}
          className={`px-3 py-1 text-[11px] rounded font-mono transition-colors disabled:opacity-50 ${
            showLivePrices ? 'bg-green-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-white'
          }`}
        >
          {liveLoading ? '\u23F3 Loading...' : showLivePrices ? '\uD83D\uDCE1 Live ON' : '\uD83D\uDCE1 Live Prices'}
        </button>
        <button
          onClick={async () => {
            if (!showIndustry) {
              const hasData = data.holdings.some(h => h.industry);
              if (!hasData) {
                setIndustryLoading(true);
                try {
                  await fetch(`${API_BASE}/portfolio/refresh-industry`, { method: 'POST' });
                  await fetchPortfolio();
                } finally {
                  setIndustryLoading(false);
                }
              }
            }
            setShowIndustry(!showIndustry);
          }}
          disabled={industryLoading}
          className={`px-3 py-1 text-[11px] rounded font-mono transition-colors ${
            showIndustry ? 'bg-purple-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-white'
          }`}
        >
          {industryLoading ? '⏳ Loading...' : showIndustry ? '🏭 Industry' : '🏭 Sector'}
        </button>
        <button
          onClick={() => {
            const headers = ['Symbol','Qty','Avg','LTP','Live','Value','P&L%','Day%','Del%','vsSMA50%','Sector'];
            if (showFundamentals) {
              headers.push('Stars','OpMgn%','FCFY%','Prmtr%','CurRatio','MktCap','P/E');
            }
            const csvRows = [headers];
            for (const h of sortedHoldings) {
              const live = showLivePrices && livePrices[h.symbol];
              const ltpVal = live ? livePrices[h.symbol].ltp : h.ltp;
              const row = [
                h.symbol, String(h.net_qty), String(h.avg_price),
                ltpVal != null ? String(ltpVal) : '',
                live ? 'Y' : 'N',
                String(h.current_value), String(h.overall_pnl_pct),
                String(h.day_pnl_pct),
                h.delivery_pct != null ? String(h.delivery_pct) : '',
                h.vs_sma50_pct != null ? String(h.vs_sma50_pct) : '',
                h.sector,
              ];
              if (showFundamentals) {
                row.push(
                  h.morningstar_rating != null ? String(h.morningstar_rating) : '',
                  h.operating_margin != null ? String(h.operating_margin) : '',
                  h.free_cash_flow_yield != null ? String(h.free_cash_flow_yield) : '',
                  h.promoter_holding != null ? String(h.promoter_holding) : '',
                  h.current_ratio != null ? String(h.current_ratio) : '',
                  h.market_cap != null ? String(h.market_cap) : '',
                  h.pe != null ? String(h.pe) : '',
                );
              }
              csvRows.push(row);
            }
            const csv = csvRows.map(r => r.join(',')).join('\n');
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `portfolio_${new Date().toISOString().slice(0,10)}.csv`; a.click();
            URL.revokeObjectURL(url);
          }}
          className="px-3 py-1 text-[11px] rounded font-mono transition-colors bg-[#ffffff0a] text-[#888] hover:text-white"
        >
          {'\uD83D\uDCCB'} Export CSV
        </button>
      </div>

      {/* ── Live Price Disclaimer ── */}
      {showLivePrices && (
        <div className="text-[10px] font-mono text-[#888] bg-emerald-500/5 border border-emerald-500/20 rounded px-3 py-1.5 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-green-500 inline-block animate-pulse" />
          Live ({liveSource === 'cache' ? 'cached' : 'yfinance'}) via Yahoo Finance. 15-minute delayed. For reference only.
        </div>
      )}

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
                <th className={thClass} onClick={() => toggleSort('ltp')}>
                  {showLivePrices ? '\uD83D\uDFE2 Live' : 'LTP'}{sortIndicator('ltp')}
                </th>
                <th className={thClass} onClick={() => toggleSort('current_value')}>Value{sortIndicator('current_value')}</th>
                <th className={thClass} onClick={() => toggleSort('overall_pnl_pct')}>P&L%{sortIndicator('overall_pnl_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('day_pnl_pct')}>Day%{sortIndicator('day_pnl_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('delivery_pct')}>Del{sortIndicator('delivery_pct')}</th>
                <th className={thClass} onClick={() => toggleSort('vs_sma50_pct')}>vs SMA50{sortIndicator('vs_sma50_pct')}</th>
                {showFundamentals && (
                  <>
                    <th className={thClass} onClick={() => toggleSort('morningstar_rating')}>Stars{sortIndicator('morningstar_rating')}</th>
                    <th className={thClass} onClick={() => toggleSort('operating_margin')}>OpMgn%{sortIndicator('operating_margin')}</th>
                    <th className={thClass} onClick={() => toggleSort('free_cash_flow_yield')}>FCFY%{sortIndicator('free_cash_flow_yield')}</th>
                    <th className={thClass} onClick={() => toggleSort('promoter_holding')}>Prmtr%{sortIndicator('promoter_holding')}</th>
                    <th className={thClass} onClick={() => toggleSort('current_ratio')}>CurRatio{sortIndicator('current_ratio')}</th>
                    <th className={thClass} onClick={() => toggleSort('market_cap')}>MktCap{sortIndicator('market_cap')}</th>
                  </>
                )}
                <th className={thClass} onClick={() => toggleSort('pe')}>P/E{sortIndicator('pe')}</th>
                <th className={thClass} onClick={() => toggleSort('sector')}>{showIndustry ? 'Industry' : 'Sector'}{sortIndicator('sector')}</th>
                <th className="px-3 py-2 border-b border-[#ffffff1a]"></th>
              </tr>
            </thead>
            <tbody>
              {sortedHoldings.map((h) => {
                const live = showLivePrices && livePrices[h.symbol];
                const ltpDisplay = live ? livePrices[h.symbol].ltp : h.ltp;
                const ltpClass = live ? 'text-cyan-400' : (h.overall_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400');
                return (
                <tr key={h.symbol} className="hover:bg-[#ffffff05] transition-colors">
                  <td className={`${tdClass} font-bold`}>
                    <div className="flex items-center gap-1">
                      <span
                        className="text-cyan-400 hover:text-white cursor-pointer"
                        onClick={() => window.open(`/chart?symbol=${h.symbol}`, '_blank')}
                        title={`Open chart for ${h.symbol}`}
                      >
                        {h.symbol}
                      </span>
                      <TrendingUp size={10} className="opacity-30" />
                    </div>
                  </td>
                  <td
                    className={`${tdClass} cursor-pointer hover:bg-[#ffffff0a] ${
                      editingCell?.symbol === h.symbol && editingCell?.field === 'net_qty' ? 'p-0' : ''
                    }`}
                    onClick={() => {
                      if (editingCell) return;
                      setEditingCell({ symbol: h.symbol, field: 'net_qty' });
                      setEditValue(String(h.net_qty));
                    }}
                  >
                    {editingCell?.symbol === h.symbol && editingCell?.field === 'net_qty' ? (
                      <input
                        autoFocus
                        className="w-full h-full bg-blue-600 text-white px-3 py-2 outline-none"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => handleUpdateCell(h.symbol, 'net_qty')}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleUpdateCell(h.symbol, 'net_qty');
                          if (e.key === 'Escape') setEditingCell(null);
                        }}
                      />
                    ) : (
                      <div className="flex items-center justify-between group">
                        <span>{formatQty(h.net_qty)}</span>
                        {savingCell?.symbol === h.symbol && savingCell?.field === 'net_qty' ? (
                          <Loader2 size={10} className="animate-spin text-[#888]" />
                        ) : (
                          <Edit size={10} className="text-[#888] opacity-0 group-hover:opacity-100" />
                        )}
                      </div>
                    )}
                  </td>
                  <td
                    className={`${tdClass} cursor-pointer hover:bg-[#ffffff0a] ${
                      editingCell?.symbol === h.symbol && editingCell?.field === 'avg_price' ? 'p-0' : ''
                    }`}
                    onClick={() => {
                      if (editingCell) return;
                      setEditingCell({ symbol: h.symbol, field: 'avg_price' });
                      setEditValue(String(h.avg_price));
                    }}
                  >
                    {editingCell?.symbol === h.symbol && editingCell?.field === 'avg_price' ? (
                      <input
                        autoFocus
                        className="w-full h-full bg-blue-600 text-white px-3 py-2 outline-none"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => handleUpdateCell(h.symbol, 'avg_price')}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleUpdateCell(h.symbol, 'avg_price');
                          if (e.key === 'Escape') setEditingCell(null);
                        }}
                      />
                    ) : (
                      <div className="flex items-center justify-between group">
                        <span>{formatIndianDec(h.avg_price)}</span>
                        {savingCell?.symbol === h.symbol && savingCell?.field === 'avg_price' ? (
                          <Loader2 size={10} className="animate-spin text-[#888]" />
                        ) : (
                          <Edit size={10} className="text-[#888] opacity-0 group-hover:opacity-100" />
                        )}
                      </div>
                    )}
                  </td>
                  <td className={`${tdClass} ${ltpClass}`}>
                    {live ? '\u25CF ' : ''}{ltpDisplay ? formatIndianDec(ltpDisplay) : '\u2014'}
                  </td>
                  <td className={tdClass}>{formatIndianDec(h.current_value)}</td>
                  <td className={`${tdClass} ${h.overall_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {formatPct(h.overall_pnl_pct)}
                  </td>
                  <td className={`${tdClass} ${h.day_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {formatPct(h.day_pnl_pct)}
                  </td>
                  <td className={tdClass}>
                    {h.delivery_pct != null ? `${h.delivery_pct.toFixed(1)}% ${h.delivery_trend}` : <span title="Delivery data not available for this symbol">N/A</span>}
                  </td>
                  <td className={`${tdClass} ${h.vs_sma50_pct != null ? (h.vs_sma50_pct >= 0 ? 'text-green-400' : 'text-red-400') : ''}`}>
                    {h.vs_sma50_pct != null ? `${h.vs_sma50_pct >= 0 ? '+' : ''}${h.vs_sma50_pct.toFixed(1)}%` : '\u2014'}
                  </td>
                  {showFundamentals && (
                    <>
                      <td className={tdClass}>{renderStars(h.morningstar_rating)}</td>
                      <td className={tdClass}>{h.operating_margin != null ? `${(h.operating_margin * 100).toFixed(1)}%` : '\u2014'}</td>
                      <td className={`${tdClass} ${h.free_cash_flow_yield != null ? fcfYieldColor(h.free_cash_flow_yield * 100) : ''}`}>
                        {h.free_cash_flow_yield != null ? `${(h.free_cash_flow_yield * 100).toFixed(1)}%` : '\u2014'}
                      </td>
                      <td className={`${tdClass} ${promoterColor(h.promoter_holding)}`}>
                        {h.promoter_holding != null ? `${h.promoter_holding.toFixed(1)}%` : '\u2014'}
                      </td>
                      <td className={`${tdClass} ${currentRatioColor(h.current_ratio)}`}>
                        {h.current_ratio != null ? h.current_ratio.toFixed(2) : '\u2014'}
                      </td>
                      <td className={tdClass}>{formatMarketCap(h.market_cap)}</td>
                    </>
                  )}
                  <td className={`${tdClass} ${peColor(h.pe)}`}>{h.pe != null ? h.pe.toFixed(1) : '\u2014'}</td>
                  <td className={tdClass}>{showIndustry ? (h.industry || h.yf_sector || h.sector || '\u2014') : (h.sector || '\u2014')}</td>
                  <td className={`${tdClass} text-right`}>
                    <button
                      onClick={() => setDeleteConfirm(h.symbol)}
                      className="text-[#555] hover:text-red-400 transition-colors"
                      title={`Remove ${h.symbol}`}
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Sector / Industry Allocation ── */}
      {allocationData.length > 0 && (
        <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-4">
          <h3 className="text-xs font-semibold text-[#fafafa] mb-3">
            {showIndustry ? 'Industry Allocation' : 'Sector Allocation'}
          </h3>
          <div className="flex flex-col gap-2">
            {allocationData.map((s, i) => (
              <div key={s.sector} className="flex items-center gap-3">
                <span className="text-[11px] font-mono text-[#fafafa] w-24 shrink-0 truncate">{s.sector}</span>
                  <div className="flex-1 h-5 bg-[#ffffff0a] rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${SECTOR_COLORS[s.sector] || SECTOR_COLORS['Unknown'] || 'bg-gray-600'}`}
                      style={{ width: `${Math.max(s.weight_pct, 2)}%` }}
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

      {/* ── Scanner Signals Section ── */}
      {resolveSignals.length > 0 && (
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg overflow-hidden">
          <div
            className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-[#ffffff05] transition-colors"
            onClick={() => setSignalsExpanded(!signalsExpanded)}
          >
            <div className="flex items-center gap-2">
              <span className="text-sm">{'\uD83D\uDCE1'}</span>
              <h3 className="text-sm font-semibold text-[#fafafa]">Scanner Signals on Your Holdings</h3>
              <span className="text-[10px] text-[#888] bg-[#ffffff0a] px-2 py-0.5 rounded-full">
                {resolveSignals.length} stocks flagged
              </span>
            </div>
            <span className={`text-[#888] text-xs transition-transform ${signalsExpanded ? 'rotate-180' : ''}`}>
              {'\u25BE'}
            </span>
          </div>

          {signalsExpanded && (
            <div className="border-t border-[#ffffff0a] px-4 py-3 space-y-3">
              {/* Summary bar */}
              <div className="flex items-center gap-4 text-[11px] font-mono text-[#888] mb-2">
                <span>{'\uD83D\uDFE2'} {resolveSignals.filter(s => s.highestSeverity === 'bullish').length} bullish</span>
                <span>{'\uD83D\uDFE1'} {resolveSignals.filter(s => s.highestSeverity === 'neutral').length} neutral</span>
                <span>{'\uD83D\uDD35'} {resolveSignals.filter(s => s.highestSeverity === 'info').length} informational</span>
                {resolveSignals.filter(s => s.highestSeverity === 'bearish').length > 0 && (
                  <span>{'\uD83D\uDD34'} {resolveSignals.filter(s => s.highestSeverity === 'bearish').length} bearish</span>
                )}
              </div>

              {/* Signal cards */}
              {resolveSignals.map((item) => (
                <div
                  key={item.symbol}
                  className={`border-l-2 rounded-r-lg p-3 ${severityColors[item.highestSeverity]}`}
                >
                  {/* Stock header */}
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full ${severityDotColors[item.highestSeverity]}`} />
                      <span
                        className="text-sm font-semibold text-cyan-400 hover:text-white cursor-pointer"
                        onClick={() => window.open(`/chart?symbol=${item.symbol}`, '_blank')}
                      >
                        {item.symbol}
                      </span>
                      <span className="text-[10px] text-[#888]">
                        {item.signalCount} signal{item.signalCount > 1 ? 's' : ''}
                        {item.signalCount >= 3 ? ' \u2014 Highest conviction' : ''}
                      </span>
                    </div>
                    <button
                      onClick={() => {
                        setScannerSymbolToAdd(item.symbol);
                        setAddForm({ symbol: item.symbol, qty: '', price: '', category: 'NSE EQ' });
                        setShowAddModal(true);
                      }}
                      className="text-[10px] text-[#888] hover:text-white bg-[#ffffff0a] hover:bg-[#ffffff1a] px-2 py-0.5 rounded transition-colors"
                      title="Add to portfolio"
                    >
                      + Add
                    </button>
                  </div>

                  {/* Individual signals */}
                  <div className="space-y-1.5 ml-4">
                    {item.signals.map((signal, idx) => (
                      <div key={idx} className="flex items-start gap-2 text-xs">
                        <span className="mt-0.5">{signal.icon}</span>
                        <div className="flex-1">
                          <span className="text-[#ccc] font-medium">{signal.label}</span>
                          {signal.grade ? (
                            <span className={`ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                              signal.grade === 'A' ? 'bg-green-500/20 text-green-400 border-green-500/30' :
                              signal.grade === 'B' ? 'bg-blue-500/20 text-blue-400 border-blue-500/30' :
                              'bg-[#ffffff0a] text-[#aaa] border-[#ffffff1a]'
                            }`}>
                              {signal.grade}
                            </span>
                          ) : null}
                          <span className="text-[#888] ml-1">\u2014 {signal.description}</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Consolidated suggestion */}
                  {item.signals.length > 0 && (
                    <div className="mt-2 ml-4 text-[10px] text-[#888] italic">
                      {'\u2192'} SUGGESTION: {(() => {
                        const bestSignal = item.signals.reduce((best, s) => {
                          const order = { bullish: 3, neutral: 2, info: 1, bearish: 0 };
                          return order[s.severity] > order[best.severity] ? s : best;
                        }, item.signals[0]);
                        const grade = typeof bestSignal.grade === 'string' && bestSignal.grade ? bestSignal.grade : '';
                        return bestSignal.suggestion.replace('{grade}', grade || 'N/A');
                      })()}
                    </div>
                  )}
                </div>
              ))}

              {/* Stocks with no signals */}
              {data?.holdings && (() => {
                const flaggedSymbols = new Set(resolveSignals.map(s => s.symbol));
                const unflagged = data.holdings.filter(h => !flaggedSymbols.has(h.symbol));
                if (unflagged.length > 0) {
                  return (
                    <div className="text-[10px] text-[#555] mt-2 pt-2 border-t border-[#ffffff05]">
                      {'\uD83D\uDFE2'} {unflagged.length} stock{unflagged.length > 1 ? 's' : ''} with no active signals \u2014 {unflagged.map(h => h.symbol).join(', ')}
                    </div>
                  );
                }
                return null;
              })()}
            </div>
          )}
        </div>
      )}

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
      <div className="text-[10px] font-mono text-[#555] text-center py-3 border-t border-[#ffffff0a] flex items-center justify-center gap-3">
        <span>Prices as of: {freshness.prices_from || 'N/A'} (EOD) | Fundamentals: {freshness.fundamentals_cached || 'N/A'}</span>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="px-2 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-[10px] text-cyan-400 hover:text-white hover:bg-[#ffffff15] transition-colors disabled:opacity-50"
        >
          {refreshing ? '⟳ Refreshing...' : '[Refresh now]'}
        </button>
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
