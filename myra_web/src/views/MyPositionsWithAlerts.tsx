import { useState, useEffect, useCallback } from 'react';
import { Plus, XCircle, Trash2 } from 'lucide-react';
import { API_BASE } from '../config';
import ScrollableTable from '../components/ScrollableTable';

interface Position {
  symbol: string;
  first_entry_date: string;
  last_tranche_date: string;
  n_tranches: number;
  blended_basis: number | null;
  tranche_prices: number[] | null;
  current_price: number | null;
  days_held: number;
  days_to_cap: number;
  pct_to_target: number | null;
  alert: 'HOLD' | 'SELL' | 'AVERAGE' | 'CAP APPROACHING';
  alert_detail: string;
  delivery_pct: number | null;
}

interface FormData {
  symbol: string;
  first_entry_date: string;
  last_tranche_date: string;
  n_tranches: number;
  tranche_prices: string[];  // individual entry prices, one per tranche
}

const EMPTY_FORM: FormData = {
  symbol: '',
  first_entry_date: '',
  last_tranche_date: '',
  n_tranches: 1,
  tranche_prices: [''],
};

const ALERT_STYLES: Record<string, string> = {
  'HOLD': 'bg-blue-500/15 border-blue-500/30 text-blue-400',
  'SELL': 'bg-green-500/15 border-green-500/30 text-green-400',
  'AVERAGE': 'bg-amber-500/15 border-amber-500/30 text-amber-400',
  'CAP APPROACHING': 'bg-red-500/15 border-red-500/30 text-red-400',
};

export default function MyPositionsWithAlerts() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormData>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [editingSymbol, setEditingSymbol] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/bottom-hunter-m1/positions`);
      if (res.ok) {
        const data = await res.json();
        setPositions(data.positions ?? []);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchPositions(); }, [fetchPositions]);

  const handleSubmit = async () => {
    if (!form.symbol.trim()) {
      setError('Symbol is required');
      return;
    }
    // Parse tranche prices — at least one required
    const prices = form.tranche_prices
      .map((s) => parseFloat(s))
      .filter((n) => !isNaN(n) && n > 0);
    if (prices.length === 0) {
      setError('Enter at least one entry price');
      return;
    }
    if (prices.length > 3) {
      setError('Maximum 3 tranches');
      return;
    }

    setError(null);
    try {
      const res = await fetch(`${API_BASE}/bottom-hunter-m1/positions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: form.symbol.toUpperCase().trim(),
          first_entry_date: form.first_entry_date,
          last_tranche_date: form.last_tranche_date || form.first_entry_date,
          n_tranches: prices.length,
          tranche_prices: prices,
        }),
      });
      if (res.ok) {
        setForm(EMPTY_FORM);
        setShowForm(false);
        setEditingSymbol(null);
        fetchPositions();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Failed' }));
        setError(err.detail || 'Failed to save');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Connection error');
    }
  };

  const handleDelete = async (symbol: string) => {
    if (!confirm(`Remove ${symbol} from positions?`)) return;
    try {
      await fetch(`${API_BASE}/bottom-hunter-m1/positions/${symbol}`, { method: 'DELETE' });
      fetchPositions();
    } catch { /* ignore */ }
  };

  const startEdit = (pos: Position) => {
    setEditingSymbol(pos.symbol);
    const tp = pos.tranche_prices?.map((p) => p.toString()) ?? [];
    setForm({
      symbol: pos.symbol,
      first_entry_date: pos.first_entry_date,
      last_tranche_date: pos.last_tranche_date,
      n_tranches: pos.n_tranches,
      tranche_prices: tp.length > 0 ? tp : [''],
    });
    setShowForm(true);
  };

  return (
    <section className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden" aria-label="My Positions">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#ffffff1a]">
        <div className="flex items-center gap-3">
          <h2 className="text-xs font-mono font-semibold text-violet-300">My Positions</h2>
          <span className="text-[11px] font-mono text-[#888]">
            {loading ? 'Loading…' : `${positions.length} position${positions.length === 1 ? '' : 's'}`}
          </span>
        </div>
        <button
          onClick={() => { setShowForm(!showForm); setEditingSymbol(null); setForm(EMPTY_FORM); }}
          className="flex items-center gap-1 px-2 py-1 bg-violet-600 hover:bg-violet-700 text-white rounded text-[11px] font-mono transition-colors"
        >
          <Plus size={12} /> {showForm ? 'Cancel' : 'Add Position'}
        </button>
      </div>

      {error && (
        <div className="px-4 py-2 text-xs font-mono text-red-400 bg-red-500/10 flex items-center gap-2">
          {error}
          <button onClick={() => setError(null)} className="text-red-500/50 hover:text-red-300">
            <XCircle size={12} />
          </button>
        </div>
      )}

      {showForm && (
        <div className="px-4 py-3 border-b border-[#ffffff1a] bg-[#0e1117]">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-[#888] uppercase">Symbol *</span>
              <input
                value={form.symbol}
                onChange={(e) => setForm({ ...form, symbol: e.target.value })}
                placeholder="RELIANCE"
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-[#888] uppercase">Entry Date *</span>
              <input
                type="date"
                value={form.first_entry_date}
                onChange={(e) => setForm({ ...form, first_entry_date: e.target.value })}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-[#888] uppercase">Last Tranche Date</span>
              <input
                type="date"
                value={form.last_tranche_date}
                onChange={(e) => setForm({ ...form, last_tranche_date: e.target.value })}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none"
              />
            </label>
          </div>
          {/* Tranche price inputs — server computes harmonic-mean basis */}
          <div className="mt-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] font-mono text-[#888] uppercase">Entry Prices (₹) — one per tranche</span>
              <button
                type="button"
                onClick={() => {
                  if (form.tranche_prices.length < 3) {
                    setForm({ ...form, tranche_prices: [...form.tranche_prices, ''] });
                  }
                }}
                disabled={form.tranche_prices.length >= 3}
                className="text-[10px] font-mono text-violet-400 hover:text-violet-300 disabled:text-[#444]"
              >
                + Add Tranche
              </button>
            </div>
            <div className="flex gap-2">
              {form.tranche_prices.map((price, idx) => (
                <div key={idx} className="flex items-center gap-1">
                  <input
                    type="number"
                    step="0.01"
                    value={price}
                    onChange={(e) => {
                      const updated = [...form.tranche_prices];
                      updated[idx] = e.target.value;
                      setForm({ ...form, tranche_prices: updated });
                    }}
                    placeholder={`T${idx + 1}`}
                    className="w-24 bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none"
                  />
                  {form.tranche_prices.length > 1 && (
                    <button
                      type="button"
                      onClick={() => {
                        const updated = form.tranche_prices.filter((_, i) => i !== idx);
                        setForm({ ...form, tranche_prices: updated });
                      }}
                      className="text-[#666] hover:text-red-400 text-xs"
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={handleSubmit}
              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-700 text-white rounded text-[11px] font-mono transition-colors"
            >
              {editingSymbol ? 'Update' : 'Add'}
            </button>
            <span className="text-[10px] font-mono text-[#666]">
              Server computes harmonic-mean basis (total invested ÷ total shares) — matches the backtest engine.
            </span>
          </div>
        </div>
      )}

      {positions.length === 0 && !loading && (
        <div className="px-4 py-8 text-center text-xs font-mono text-[#666]">
          No positions tracked yet. Click "Add Position" to enter your holdings.
        </div>
      )}

      {positions.length > 0 && (
        <ScrollableTable>
          <table className="w-full text-left text-[12px] font-mono whitespace-nowrap">
            <thead>
              <tr className="border-b border-[#ffffff1a] text-[#888] uppercase text-[10px] tracking-wider">
                <th className="px-3 py-2">Alert</th>
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Entry Date</th>
                <th className="px-3 py-2">Last Tranche</th>
                <th className="px-3 py-2">Tranches</th>
                <th className="px-3 py-2">Blended Basis</th>
                <th className="px-3 py-2">Current ₹</th>
                <th className="px-3 py-2">% to Target</th>
                <th className="px-3 py-2">Days Held</th>
                <th className="px-3 py-2">Cap Days Left</th>
                <th className="px-3 py-2">Delivery</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.symbol} className="border-b border-[#ffffff0d] hover:bg-[#ffffff08]">
                  <td className="px-3 py-2">
                    <span className={`border rounded px-1.5 py-0.5 text-[10px] font-semibold ${ALERT_STYLES[p.alert]}`}>
                      {p.alert}
                    </span>
                    {p.alert_detail && (
                      <span className="ml-1 text-[10px] text-[#666]">{p.alert_detail}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-[#fafafa] font-semibold">{p.symbol}</td>
                  <td className="px-3 py-2 text-[#ccc]">{p.first_entry_date}</td>
                  <td className="px-3 py-2 text-[#ccc]">{p.last_tranche_date}</td>
                  <td className="px-3 py-2 text-[#fafafa]">{p.n_tranches}/3</td>
                  <td className="px-3 py-2 text-[#ccc]">
                    {p.blended_basis != null ? `₹ ${p.blended_basis.toFixed(2)}` : '—'}
                    {p.tranche_prices && p.tranche_prices.length > 0 && (
                      <span className="ml-1 text-[10px] text-[#555]">
                        [{p.tranche_prices.map((tp) => tp.toFixed(0)).join(',')}]
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-[#fafafa]">
                    {p.current_price != null ? `₹ ${p.current_price.toFixed(2)}` : '—'}
                  </td>
                  <td className="px-3 py-2">
                    {p.pct_to_target != null ? (
                      <span className={p.pct_to_target >= 10 ? 'text-green-400' : 'text-[#ccc]'}>
                        {p.pct_to_target >= 0 ? '+' : ''}{p.pct_to_target.toFixed(1)}%
                      </span>
                    ) : '—'}
                  </td>
                  <td className="px-3 py-2 text-[#ccc]">{p.days_held}</td>
                  <td className="px-3 py-2">
                    <span className={p.days_to_cap <= 20 ? 'text-red-400 font-semibold' : 'text-[#ccc]'}>
                      {p.days_to_cap}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-[#888]">
                    {p.delivery_pct != null ? `${p.delivery_pct}%` : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => startEdit(p)}
                      className="text-[#666] hover:text-violet-300 mr-2"
                      title="Edit"
                    >
                      ✎
                    </button>
                    <button
                      onClick={() => handleDelete(p.symbol)}
                      className="text-[#666] hover:text-red-400"
                      title="Remove"
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      )}
    </section>
  );
}
