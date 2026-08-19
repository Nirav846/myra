import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import { RefreshCw, Loader2, AlertTriangle, ChevronDown, ChevronUp, PanelRightOpen, PanelRightClose } from 'lucide-react';
import { API_BASE } from '../config';

// ── Types ────────────────────────────────────────────────────────────────────
interface IndexEntry {
  id: string;
  label: string;
}

interface RRGPoint {
  id: string;
  label: string;
  x: number;
  y: number;
  quadrant: string;
}

interface RRGData {
  current: RRGPoint[];
  trails: Record<string, number[][]>;
  meta: {
    timeframe: string;
    trail: number;
    benchmark: string;
    date: string;
  };
}

const QUADRANT_COLORS: Record<string, string> = {
  Leading: '#22c55e',
  Weakening: '#f59e0b',
  Lagging: '#ef4444',
  Improving: '#3b82f6',
};

const TRAIL_OPACITY = 0.4;
const MAX_SECTORS = 25;
const TIMEFRAMES = ['weekly', 'daily'] as const;
const TRAIL_OPTIONS = [4, 8, 12, 16, 20];
const STORAGE_KEY = 'rrg_selected_sectors';

const DEFAULT_SECTORS = [
  'nifty bank', 'nifty it', 'nifty pharma', 'nifty auto',
  'nifty metal', 'nifty realty', 'nifty fmcg', 'nifty energy',
  'nifty financial services', 'nifty private bank', 'nifty psu bank',
  'nifty midcap 50', 'nifty midcap 100', 'nifty midcap 150',
  'nifty smallcap 50', 'nifty smallcap 100', 'nifty smallcap 250',
  'nifty next 50', 'nifty next 100', 'nifty 500', 'nifty 200',
  'nifty 100',
];

function loadSavedSectors(): string[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return Array.isArray(arr) && arr.length > 0 ? arr : null;
  } catch { return null; }
}

function saveSectors(ids: string[]) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(ids)); } catch { /* noop */ }
}

/** Compute dynamic axis range from all x/y values with padding. */
function computeRange(values: number[]): [number, number] {
  if (values.length === 0) return [-3.5, 3.5];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max(0.5, (max - min) * 0.15);
  return [Math.floor((min - pad) * 10) / 10, Math.ceil((max + pad) * 10) / 10];
}

// ── Component ────────────────────────────────────────────────────────────────
export default function RRGView() {
  const [indices, setIndices] = useState<IndexEntry[]>([]);
  const [rrgData, setRrgData] = useState<RRGData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Controls
  const [timeframe, setTimeframe] = useState<'weekly' | 'daily'>('weekly');
  const [trail, setTrail] = useState(8);
  const [benchmark, setBenchmark] = useState('nifty 50');
  const [selectedSectors, setSelectedSectors] = useState<Set<string>>(new Set());
  const [showSectorPanel, setShowSectorPanel] = useState(true);
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);
  const [sectorSearch, setSectorSearch] = useState('');
  const [sectorCapWarning, setSectorCapWarning] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const forceRefreshRef = useRef(false);

  // ── Fetch indices on mount ──────────────────────────────────────────────
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/rrg/indices`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!active) return;
        const idx = data.indices as IndexEntry[];
        setIndices(idx);
        const available = new Set(idx.map((i) => i.id));
        // Try localStorage first, fall back to defaults
        const saved = loadSavedSectors();
        if (saved) {
          const valid = saved.filter((s) => available.has(s) && s !== benchmark);
          if (valid.length > 0) { setSelectedSectors(new Set(valid)); return; }
        }
        const defaults = DEFAULT_SECTORS.filter((s) => available.has(s) && s !== benchmark);
        setSelectedSectors(new Set(defaults.length > 0 ? defaults : idx.filter((i) => i.id !== benchmark).slice(0, 20).map((i) => i.id)));
      } catch (e) {
        if (active) setError(`Failed to load indices: ${e}`);
      }
    };
    load();
    return () => { active = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Fetch RRG data (with AbortController) ──────────────────────────────
  const fetchRRG = useCallback(async () => {
    if (indices.length === 0) return;
    // Guard: need at least one sector
    const sectorList = Array.from(selectedSectors).filter((s) => s !== benchmark);
    if (sectorList.length === 0) {
      setRrgData(null);
      return;
    }

    // Cancel previous in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        timeframe,
        trail: String(trail),
        benchmark,
        sectors: sectorList.join(','),
      });
      if (forceRefreshRef.current) {
        params.set('refresh', 'true');
        forceRefreshRef.current = false;
      }
      const res = await fetch(`${API_BASE}/rrg/?${params}`, { signal: controller.signal });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data: RRGData = await res.json();
      setRrgData(data);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      setError(`RRG fetch failed: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [indices, timeframe, trail, benchmark, selectedSectors]);

  useEffect(() => {
    fetchRRG();
    return () => { abortRef.current?.abort(); };
  }, [fetchRRG]);

  // ── Derived state ──────────────────────────────────────────────────────
  const filteredIndices = useMemo(() => {
    const q = sectorSearch.toLowerCase();
    return indices.filter(
      (i) => i.id !== benchmark && (q === '' || i.label.toLowerCase().includes(q) || i.id.toLowerCase().includes(q))
    );
  }, [indices, sectorSearch, benchmark]);

  const hasSectors = useMemo(() => {
    return Array.from(selectedSectors).some((s) => s !== benchmark);
  }, [selectedSectors, benchmark]);

  // ── Sector management ──────────────────────────────────────────────────
  const toggleSector = (id: string) => {
    setSelectedSectors((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        setSectorCapWarning(false);
      } else {
        // Enforce cap: block if at limit
        const currentCount = Array.from(next).filter((s) => s !== benchmark).length;
        if (currentCount >= MAX_SECTORS) {
          setSectorCapWarning(true);
          return prev; // don't add
        }
        next.add(id);
      }
      saveSectors(Array.from(next));
      return next;
    });
  };

  const selectAll = () => {
    const candidates = indices.filter((i) => i.id !== benchmark).slice(0, MAX_SECTORS);
    const all = new Set(candidates.map((i) => i.id));
    setSelectedSectors(all);
    setSectorCapWarning(indices.length - 1 > MAX_SECTORS);
    saveSectors(Array.from(all));
  };

  const deselectAll = () => {
    const empty = new Set<string>();
    setSelectedSectors(empty);
    setSectorCapWarning(false);
    saveSectors([]);
  };

  // ── Benchmark switch: remove new from selection ────────────────────────
  const handleBenchmarkChange = (newBench: string) => {
    setBenchmark(newBench);
    setSelectedSectors((prev) => {
      const next = new Set(prev);
      next.delete(newBench);
      saveSectors(Array.from(next));
      return next;
    });
  };

  // ── Plotly traces ──────────────────────────────────────────────────────
  const { traces, layout } = useMemo(() => {
    if (!rrgData || rrgData.current.length === 0) return { traces: [] as Data[], layout: {} as Partial<Layout> };

    // Collect all x/y for dynamic range
    const allX: number[] = [];
    const allY: number[] = [];
    for (const pt of rrgData.current) { allX.push(pt.x); allY.push(pt.y); }
    for (const pts of Object.values(rrgData.trails)) {
      for (const p of pts) { allX.push(p[0]); allY.push(p[1]); }
    }
    const [xMin, xMax] = computeRange(allX);
    const [yMin, yMax] = computeRange(allY);

    const plotTraces: Data[] = [];

    // Trails (lines)
    for (const sector of rrgData.current) {
      const pts = rrgData.trails[sector.id];
      if (!pts || pts.length === 0) continue;
      plotTraces.push({
        x: pts.map((p) => p[0]),
        y: pts.map((p) => p[1]),
        mode: 'lines',
        type: 'scatter',
        line: { color: QUADRANT_COLORS[sector.quadrant] || '#888', width: 2 },
        opacity: TRAIL_OPACITY,
        showlegend: false,
        hoverinfo: 'skip',
      });
    }

    // Current positions (dots) — one trace per quadrant for legend
    for (const q of ['Leading', 'Weakening', 'Lagging', 'Improving']) {
      const pts = rrgData.current.filter((c) => c.quadrant === q);
      if (pts.length === 0) continue;
      plotTraces.push({
        x: pts.map((p) => p.x),
        y: pts.map((p) => p.y),
        mode: 'markers+text',
        type: 'scatter',
        marker: { color: QUADRANT_COLORS[q], size: 12, line: { color: '#fff', width: 1 } },
        text: pts.map((p) => p.label),
        textposition: 'top center' as const,
        textfont: { size: 10, color: '#ccc' },
        name: q,
        customdata: pts.map((p) => [p.label, p.quadrant, p.x, p.y]),
        hovertemplate:
          '<b>%{customdata[0]}</b><br>Quadrant: %{customdata[1]}<br>RS-Ratio: %{customdata[2]:.2f}<br>RS-Momentum: %{customdata[3]:.2f}<extra></extra>',
      });
    }

    const plotLayout: Partial<Layout> = {
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      font: { color: '#ccc', size: 12 },
      xaxis: {
        title: 'RS-Ratio (Z-score)',
        zeroline: true,
        zerolinecolor: '#555',
        zerolinewidth: 1,
        gridcolor: '#222',
        range: [xMin, xMax],
      },
      yaxis: {
        title: 'RS-Momentum (Z-score)',
        zeroline: true,
        zerolinecolor: '#555',
        zerolinewidth: 1,
        gridcolor: '#222',
        range: [yMin, yMax],
      },
      legend: {
        orientation: 'h',
        x: 0.5,
        xanchor: 'center',
        y: 1.08,
        font: { size: 11 },
      },
      margin: { l: 60, r: 20, t: 40, b: 60 },
      hovermode: 'closest',
      shapes: [
        { type: 'rect', x0: 0, x1: xMax, y0: 0, y1: yMax, fillcolor: 'rgba(34,197,94,0.04)', line: { width: 0 } },
        { type: 'rect', x0: 0, x1: xMax, y0: yMin, y1: 0, fillcolor: 'rgba(245,158,11,0.04)', line: { width: 0 } },
        { type: 'rect', x0: xMin, x1: 0, y0: yMin, y1: 0, fillcolor: 'rgba(239,68,68,0.04)', line: { width: 0 } },
        { type: 'rect', x0: xMin, x1: 0, y0: 0, y1: yMax, fillcolor: 'rgba(59,130,246,0.04)', line: { width: 0 } },
        { type: 'text', x: xMax * 0.65, y: yMax * 0.9, text: 'LEADING', showarrow: false, font: { size: 14, color: 'rgba(34,197,94,0.35)' } },
        { type: 'text', x: xMax * 0.65, y: yMin * 0.9, text: 'WEAKENING', showarrow: false, font: { size: 14, color: 'rgba(245,158,11,0.35)' } },
        { type: 'text', x: xMin * 0.65, y: yMin * 0.9, text: 'LAGGING', showarrow: false, font: { size: 14, color: 'rgba(239,68,68,0.35)' } },
        { type: 'text', x: xMin * 0.65, y: yMax * 0.9, text: 'IMPROVING', showarrow: false, font: { size: 14, color: 'rgba(59,130,246,0.35)' } },
      ],
    };

    return { traces: plotTraces, layout: plotLayout };
  }, [rrgData]);

  const totalSectors = indices.length - 1; // exclude benchmark from count
  const selectedCount = Array.from(selectedSectors).filter((s) => s !== benchmark).length;

  return (
    <div className="flex flex-col h-full gap-4 p-4 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-bold">Relative Rotation Graphs</h1>
          <p className="text-xs text-[#888] mt-0.5">
            {rrgData?.meta.date ? `Data as of ${rrgData.meta.date}` : 'Loading...'}
          </p>
        </div>
        <button
          onClick={() => { forceRefreshRef.current = true; fetchRRG(); }}
          disabled={loading || !hasSectors}
          className="flex items-center gap-2 px-3 py-1.5 text-xs bg-[#ffffff0a] border border-[#ffffff1a] rounded font-mono text-[#888] hover:text-white transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 shrink-0">
        <div className="flex items-center gap-1">
          <span className="text-xs text-[#888] mr-1">Timeframe:</span>
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
                timeframe === tf
                  ? 'bg-indigo-600 text-white'
                  : 'bg-[#ffffff0a] text-[#888] border border-[#ffffff1a] hover:text-white'
              }`}
            >
              {tf.charAt(0).toUpperCase() + tf.slice(1)}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1">
          <span className="text-xs text-[#888] mr-1">Trail:</span>
          <select
            value={trail}
            onChange={(e) => setTrail(Number(e.target.value))}
            className="bg-[#ffffff0a] border border-[#ffffff1a] text-xs text-[#ccc] rounded px-2 py-1 font-mono"
          >
            {TRAIL_OPTIONS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-1">
          <span className="text-xs text-[#888] mr-1">Benchmark:</span>
          <select
            value={benchmark}
            onChange={(e) => handleBenchmarkChange(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff1a] text-xs text-[#ccc] rounded px-2 py-1 font-mono max-w-[200px]"
          >
            {indices.map((i) => (
              <option key={i.id} value={i.id}>{i.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Sector cap warning */}
      {sectorCapWarning && (
        <div className="bg-amber-950/40 border border-amber-500/50 p-2 rounded-lg text-xs text-amber-400 shrink-0">
          Max {MAX_SECTORS} sectors. Deselect some to add others.
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-950/40 border border-red-500/50 p-3 rounded-lg flex items-center gap-2 text-sm text-red-400 shrink-0">
          <AlertTriangle size={16} />
          {error}
        </div>
      )}

      {/* Main content */}
      <div className="flex gap-4 flex-1 min-h-0">
        {/* Chart */}
        <div className="flex-1 bg-[#ffffff05] border border-[#ffffff1a] rounded-lg p-2 min-h-[500px]">
          {loading ? (
            <div className="flex items-center justify-center h-full text-[#888]">
              <Loader2 size={24} className="animate-spin mr-2" /> Loading RRG data...
            </div>
          ) : !hasSectors ? (
            <div className="flex items-center justify-center h-full text-[#888]">
              Select at least one sector to display.
            </div>
          ) : rrgData ? (
            <Plot
              data={traces}
              layout={{ ...layout, autosize: true }}
              config={{ responsive: true, displayModeBar: false, scrollZoom: true }}
              style={{ width: '100%', height: '100%' }}
              useResizeHandler
            />
          ) : (
            <div className="flex items-center justify-center h-full text-[#888]">
              No data available. Select sectors and click Refresh.
            </div>
          )}
        </div>

        {/* Sector panel — desktop sidebar */}
        <div className={`${showSectorPanel ? 'w-64' : 'w-10'} shrink-0 flex-col transition-all duration-200 hidden md:flex`}>
          <button
            onClick={() => setShowSectorPanel(!showSectorPanel)}
            className="flex items-center justify-between px-3 py-2 bg-[#ffffff0a] border border-[#ffffff1a] rounded-t text-xs font-mono text-[#ccc]"
          >
            {showSectorPanel ? (
              <>
                <span>Sectors ({selectedCount}/{totalSectors})</span>
                <ChevronUp size={14} />
              </>
            ) : (
              <PanelRightOpen size={14} />
            )}
          </button>
          {showSectorPanel && (
            <div className="flex flex-col flex-1 border border-t-0 border-[#ffffff1a] rounded-b overflow-hidden">
              <div className="p-2 border-b border-[#ffffff1a]">
                <input
                  type="text"
                  value={sectorSearch}
                  onChange={(e) => setSectorSearch(e.target.value)}
                  placeholder="Search sectors..."
                  className="w-full bg-[#ffffff0a] border border-[#ffffff1a] text-xs text-[#ccc] rounded px-2 py-1 font-mono"
                />
                <div className="flex gap-1 mt-1">
                  <button onClick={selectAll} className="text-[10px] text-indigo-400 hover:text-indigo-300">All</button>
                  <span className="text-[#555]">|</span>
                  <button onClick={deselectAll} className="text-[10px] text-indigo-400 hover:text-indigo-300">None</button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {filteredIndices.map((i) => (
                  <label
                    key={i.id}
                    className="flex items-center gap-2 px-2 py-1 text-xs text-[#ccc] hover:bg-[#ffffff0a] cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedSectors.has(i.id)}
                      onChange={() => toggleSector(i.id)}
                      className="accent-indigo-500"
                    />
                    <span className="truncate">{i.label}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Mobile: floating sector toggle + overlay drawer */}
        <button
          onClick={() => setMobilePanelOpen(true)}
          className="md:hidden fixed bottom-20 right-4 z-30 bg-indigo-600 text-white p-3 rounded-full shadow-lg"
          title="Select sectors"
        >
          <PanelRightOpen size={20} />
        </button>
        {mobilePanelOpen && (
          <div className="md:hidden fixed inset-0 z-40 flex">
            <div className="absolute inset-0 bg-black/60" onClick={() => setMobilePanelOpen(false)} />
            <div className="ml-auto w-72 h-full bg-[#0e1117] border-l border-[#ffffff1a] flex flex-col relative z-50">
              <div className="flex items-center justify-between px-3 py-2 border-b border-[#ffffff1a]">
                <span className="text-xs font-mono text-[#ccc]">Sectors ({selectedCount}/{totalSectors})</span>
                <button onClick={() => setMobilePanelOpen(false)} className="text-[#888] hover:text-white">
                  <PanelRightClose size={16} />
                </button>
              </div>
              <div className="p-2 border-b border-[#ffffff1a]">
                <input
                  type="text"
                  value={sectorSearch}
                  onChange={(e) => setSectorSearch(e.target.value)}
                  placeholder="Search sectors..."
                  className="w-full bg-[#ffffff0a] border border-[#ffffff1a] text-xs text-[#ccc] rounded px-2 py-1 font-mono"
                />
                <div className="flex gap-1 mt-1">
                  <button onClick={selectAll} className="text-[10px] text-indigo-400 hover:text-indigo-300">All</button>
                  <span className="text-[#555]">|</span>
                  <button onClick={deselectAll} className="text-[10px] text-indigo-400 hover:text-indigo-300">None</button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {filteredIndices.map((i) => (
                  <label
                    key={i.id}
                    className="flex items-center gap-2 px-2 py-1 text-xs text-[#ccc] hover:bg-[#ffffff0a] cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedSectors.has(i.id)}
                      onChange={() => toggleSector(i.id)}
                      className="accent-indigo-500"
                    />
                    <span className="truncate">{i.label}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Quadrant legend */}
      <div className="flex items-center gap-4 shrink-0 text-xs text-[#888]">
        <span>Quadrants:</span>
        {Object.entries(QUADRANT_COLORS).map(([q, c]) => (
          <div key={q} className="flex items-center gap-1">
            <span className="w-3 h-3 rounded-full" style={{ backgroundColor: c }} />
            {q}
          </div>
        ))}
      </div>
    </div>
  );
}
