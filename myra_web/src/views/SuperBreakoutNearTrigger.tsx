import { useState, useEffect, useCallback } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || '';

interface NearTriggerCandidate {
  symbol: string;
  close: number;
  sma_50: number;
  sma_200: number;
  pct_to_trigger: number;
  delivery_value: number;
  mcap_rank: number;
  mcap_cr: number;
}

const BAND_OPTIONS = [3, 5, 7, 10];
const STORAGE_KEY = 'super_breakout_near_trigger_band_v1';

function loadBand(): number {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v) return Number(v);
  } catch { /* ignore */ }
  return 5;
}

function fmtCr(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`;
}

export default function SuperBreakoutNearTrigger() {
  const [candidates, setCandidates] = useState<NearTriggerCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [band, setBand] = useState(loadBand);
  const [error, setError] = useState<string | null>(null);
  const [scannedDate, setScannedDate] = useState<string | null>(null);

  const fetchNearTrigger = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/super-breakout/near-trigger?band_pct=${band}`);
      if (res.ok) {
        const data = await res.json();
        setCandidates(data.candidates ?? []);
        setScannedDate(data.scanned_date ?? null);
      } else {
        setError('Failed to load near-trigger data');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Connection error');
    } finally {
      setLoading(false);
    }
  }, [band]);

  useEffect(() => {
    fetchNearTrigger();
  }, [fetchNearTrigger]);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, String(band)); } catch { /* ignore */ }
  }, [band]);

  return (
    <section className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden mb-6" aria-label="Near-Trigger Watchlist">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#ffffff1a]">
        <div className="flex items-center gap-3">
          <h2 className="text-xs font-mono font-semibold text-amber-300">Near-Trigger Watchlist</h2>
          <span className="text-[11px] font-mono text-[#888]">
            {loading ? 'Loading…' : `${candidates.length} stock${candidates.length === 1 ? '' : 's'} within ${band}% of SMA(50) crossover`}
          </span>
          {scannedDate && (
            <span className="text-[11px] font-mono text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-0.5">
              as-of {scannedDate}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono text-[#666]">Proximity band:</span>
          {BAND_OPTIONS.map((b) => (
            <button
              key={b}
              onClick={() => setBand(b)}
              className={`px-2 py-0.5 rounded border text-[11px] font-mono transition-colors ${
                band === b
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                  : 'bg-[#0e1117] border-[#ffffff1a] text-[#888] hover:text-amber-300'
              }`}
            >
              {b}%
            </button>
          ))}
          <button
            onClick={fetchNearTrigger}
            disabled={loading}
            className="text-[11px] text-[#888] hover:text-amber-300 transition-colors ml-2"
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 text-xs font-mono text-red-400 bg-red-500/10">{error}</div>
      )}

      {!loading && candidates.length === 0 && !error && (
        <div className="px-4 py-8 text-center text-xs font-mono text-[#666]">
          No stocks within {band}% of the SMA(50) crossover — none approaching trigger.
        </div>
      )}

      {candidates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[12px] font-mono whitespace-nowrap">
            <thead>
              <tr className="border-b border-[#ffffff1a] text-[#888] uppercase text-[10px] tracking-wider">
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Close</th>
                <th className="px-3 py-2">SMA(50)</th>
                <th className="px-3 py-2">SMA(200)</th>
                <th className="px-3 py-2">% to Trigger</th>
                <th className="px-3 py-2">Delivery</th>
                <th className="px-3 py-2">Mkt Cap ₹</th>
                <th className="px-3 py-2">Rank</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.symbol} className="border-b border-[#ffffff0d] hover:bg-[#ffffff08]">
                  <td className="px-3 py-2 text-[#fafafa] font-semibold">{c.symbol}</td>
                  <td className="px-3 py-2 text-[#fafafa]">₹ {c.close.toFixed(2)}</td>
                  <td className="px-3 py-2 text-amber-300">₹ {c.sma_50.toFixed(2)}</td>
                  <td className="px-3 py-2 text-[#ccc]">₹ {c.sma_200.toFixed(2)}</td>
                  <td className="px-3 py-2">
                    <span className={`font-semibold ${c.pct_to_trigger <= 2 ? 'text-green-400' : 'text-[#fafafa]'}`}>
                      {c.pct_to_trigger.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-3 py-2 text-[#888]">{c.delivery_value.toLocaleString()}</td>
                  <td className="px-3 py-2 text-[#ccc]">{fmtCr(c.mcap_cr)}</td>
                  <td className="px-3 py-2 text-[#666]">#{c.mcap_rank}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
