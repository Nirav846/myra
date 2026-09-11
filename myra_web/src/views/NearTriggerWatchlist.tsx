import { useState, useEffect, useCallback } from 'react';
import { API_BASE } from '../config';
import ScrollableTable from '../components/ScrollableTable';

interface NearTriggerCandidate {
  symbol: string;
  close: number;
  year_low: number;
  recovery_line: number;
  pct_to_trigger: number;
  mcap_rank: number;
  mcap_cr: number;
  delivery_pct: number | null;
}

interface Props {
  scannedDate: string | null;
}

const BAND_OPTIONS = [3, 5, 7, 10];
const STORAGE_KEY = 'recovery_ladder_near_trigger_band_v1';

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

export default function NearTriggerWatchlist({ scannedDate }: Props) {
  const [candidates, setCandidates] = useState<NearTriggerCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [band, setBand] = useState(loadBand);
  const [error, setError] = useState<string | null>(null);

  const fetchNearTrigger = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/bottom-hunter-m1/near-trigger?band_pct=${band}`);
      if (res.ok) {
        const data = await res.json();
        setCandidates(data.candidates ?? []);
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
    <section className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden" aria-label="Near-Trigger Watchlist">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#ffffff1a]">
        <div className="flex items-center gap-3">
          <h2 className="text-xs font-mono font-semibold text-violet-300">Near-Trigger Watchlist</h2>
          <span className="text-[11px] font-mono text-[#888]">
            {loading ? 'Loading…' : `${candidates.length} stock${candidates.length === 1 ? '' : 's'} within ${band}% of trigger`}
          </span>
          {scannedDate && (
            <span className="text-[11px] font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 rounded px-2 py-0.5">
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
                  ? 'bg-violet-500/20 border-violet-500/40 text-violet-300'
                  : 'bg-[#0e1117] border-[#ffffff1a] text-[#888] hover:text-violet-300'
              }`}
            >
              {b}%
            </button>
          ))}
          <button
            onClick={fetchNearTrigger}
            disabled={loading}
            className="text-[11px] text-[#888] hover:text-violet-300 transition-colors ml-2"
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
          No stocks within {band}% of the recovery line — market is quiet.
        </div>
      )}

      {candidates.length > 0 && (
        <ScrollableTable>
          <table className="w-full text-left text-[12px] font-mono whitespace-nowrap">
            <thead>
              <tr className="border-b border-[#ffffff1a] text-[#888] uppercase text-[10px] tracking-wider">
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Close</th>
                <th className="px-3 py-2">Year Low</th>
                <th className="px-3 py-2">Recovery Line</th>
                <th className="px-3 py-2">% to Trigger</th>
                <th className="px-3 py-2">Mkt Cap ₹</th>
                <th className="px-3 py-2">Rank</th>
                <th className="px-3 py-2">Delivery %</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.symbol} className="border-b border-[#ffffff0d] hover:bg-[#ffffff08]">
                  <td className="px-3 py-2 text-[#fafafa] font-semibold">{c.symbol}</td>
                  <td className="px-3 py-2 text-[#fafafa]">₹ {c.close.toFixed(2)}</td>
                  <td className="px-3 py-2 text-[#ccc]">₹ {c.year_low.toFixed(2)}</td>
                  <td className="px-3 py-2 text-violet-300">₹ {c.recovery_line.toFixed(2)}</td>
                  <td className="px-3 py-2">
                    <span className={`font-semibold ${c.pct_to_trigger <= 2 ? 'text-amber-400' : 'text-[#fafafa]'}`}>
                      {c.pct_to_trigger.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-3 py-2 text-[#ccc]">{fmtCr(c.mcap_cr)}</td>
                  <td className="px-3 py-2 text-[#666]">#{c.mcap_rank}</td>
                  <td className="px-3 py-2 text-[#888]">
                    {c.delivery_pct != null ? `${c.delivery_pct}%` : '—'}
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
