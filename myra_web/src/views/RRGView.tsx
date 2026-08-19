import { useState, useEffect, useCallback, useMemo } from 'react';
import Plot from 'react-plotly.js';
import { RefreshCw, Loader2, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
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

const TIMEFRAMES = ['weekly', 'daily'] as const;
const TRAIL_OPTIONS = [4, 8, 12, 16, 20];

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
  const [sectorSearch, setSectorSearch] = useState('');

  // Fetch indices on mount
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
        // Default: major sector indices (not all 195 — too crowded)
        const DEFAULT_SECTORS = [
          'nifty bank', 'nifty it', 'nifty pharma', 'nifty auto',
          'nifty metal', 'nifty realty', 'nifty fmcg', 'nifty energy',
          'nifty financial services', 'nifty private bank', 'nifty psu bank',
          'nifty midcap 50', 'nifty midcap 100', 'nifty midcap 150',
          'nifty smallcap 50', 'nifty smallcap 100', 'nifty smallcap 250',
          'nifty next 50', 'nifty next 100', 'nifty 500', 'nifty 200',
          'nifty 100',
        ];
        const available = new Set(idx.map((i) => i.id));
        const defaults = DEFAULT_SECTORS.filter((s) => available.has(s) && s !== benchmark);
        setSelectedSectors(new Set(defaults.length > 0 ? defaults : idx.filter((i) => i.id !== benchmark).slice(0, 20).map((i) => i.id)));
      } catch (e) {
        if (active) setError(`Failed to load indices: ${e}`);
      }
    };
    load();
    return () => { active = false; };
  }, []);

  // Fetch RRG data
  const fetchRRG = useCallback(async () => {
    if (indices.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const sectorList = Array.from(selectedSectors)
        .filter((s) => s !== benchmark)
        .join(',');
      const params = new URLSearchParams({
        timeframe,
        trail: String(trail),
        benchmark,
      });
      if (sectorList) params.set('sectors', sectorList);

      const res = await fetch(`${API_BASE}/rrg/?${params}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data: RRGData = await res.json();
      setRrgData(data);
    } catch (e) {
      setError(`RRG fetch failed: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [indices, timeframe, trail, benchmark, selectedSectors]);

  useEffect(() => {
    fetchRRG();
  }, [fetchRRG]);

  // Filter indices for sector panel
  const filteredIndices = useMemo(() => {
    const q = sectorSearch.toLowerCase();
    return indices.filter(
      (i) => i.id !== benchmark && (q === '' || i.label.toLowerCase().includes(q) || i.id.toLowerCase().includes(q))
    );
  }, [indices, sectorSearch, benchmark]);

  // Toggle sector
  const toggleSector = (id: string) => {
    setSelectedSectors((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Select / deselect all
  const selectAll = () => setSelectedSectors(new Set(indices.map((i) => i.id)));
  const deselectAll = () => setSelectedSectors(new Set([benchmark]));

  // ── Plotly traces ────────────────────────────────────────────────────────
  const { traces, layout } = useMemo(() => {
    if (!rrgData) return { traces: [], layout: {} };
    const plotTraces: any[] = [];

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

    // Current positions (dots)
    const colorMap = QUADRANT_COLORS;
    for (const q of ['Leading', 'Weakening', 'Lagging', 'Improving']) {
      const pts = rrgData.current.filter((c) => c.quadrant === q);
      if (pts.length === 0) continue;
      plotTraces.push({
        x: pts.map((p) => p.x),
        y: pts.map((p) => p.y),
        mode: 'markers+text',
        type: 'scatter',
        marker: { color: colorMap[q], size: 12, line: { color: '#fff', width: 1 } },
        text: pts.map((p) => p.label),
        textposition: 'top center',
        textfont: { size: 10, color: '#ccc' },
        name: q,
        customdata: pts.map((p) => [p.label, p.quadrant, p.x, p.y]),
        hovertemplate:
          '<b>%{customdata[0]}</b><br>Quadrant: %{customdata[1]}<br>RS-Ratio: %{customdata[2]:.2f}<br>RS-Momentum: %{customdata[3]:.2f}<extra></extra>',
      });
    }

    const plotLayout: any = {
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      font: { color: '#ccc', size: 12 },
      xaxis: {
        title: 'RS-Ratio (Z-score)',
        zeroline: true,
        zerolinecolor: '#555',
        zerolinewidth: 1,
        gridcolor: '#222',
        range: [-3.5, 3.5],
      },
      yaxis: {
        title: 'RS-Momentum (Z-score)',
        zeroline: true,
        zerolinecolor: '#555',
        zerolinewidth: 1,
        gridcolor: '#222',
        range: [-3.5, 3.5],
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
        // Quadrant background shading
        { type: 'rect', x0: 0, x1: 3.5, y0: 0, y1: 3.5, fillcolor: 'rgba(34,197,94,0.04)', line: { width: 0 } },
        { type: 'rect', x0: 0, x1: 3.5, y0: -3.5, y1: 0, fillcolor: 'rgba(245,158,11,0.04)', line: { width: 0 } },
        { type: 'rect', x0: -3.5, x1: 0, y0: -3.5, y1: 0, fillcolor: 'rgba(239,68,68,0.04)', line: { width: 0 } },
        { type: 'rect', x0: -3.5, x1: 0, y0: 0, y1: 3.5, fillcolor: 'rgba(59,130,246,0.04)', line: { width: 0 } },
        // Quadrant labels
        { type: 'text', x: 2.2, y: 3.2, text: 'LEADING', showarrow: false, font: { size: 14, color: 'rgba(34,197,94,0.35)' } },
        { type: 'text', x: 2.2, y: -3.2, text: 'WEAKENING', showarrow: false, font: { size: 14, color: 'rgba(245,158,11,0.35)' } },
        { type: 'text', x: -2.2, y: -3.2, text: 'LAGGING', showarrow: false, font: { size: 14, color: 'rgba(239,68,68,0.35)' } },
        { type: 'text', x: -2.2, y: 3.2, text: 'IMPROVING', showarrow: false, font: { size: 14, color: 'rgba(59,130,246,0.35)' } },
      ],
    };

    return { traces: plotTraces, layout: plotLayout };
  }, [rrgData]);

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
          onClick={fetchRRG}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 text-xs bg-[#ffffff0a] border border-[#ffffff1a] rounded font-mono text-[#888] hover:text-white transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 shrink-0">
        {/* Timeframe */}
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

        {/* Trail */}
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

        {/* Benchmark */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-[#888] mr-1">Benchmark:</span>
          <select
            value={benchmark}
            onChange={(e) => setBenchmark(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff1a] text-xs text-[#ccc] rounded px-2 py-1 font-mono max-w-[200px]"
          >
            {indices.map((i) => (
              <option key={i.id} value={i.id}>{i.label}</option>
            ))}
          </select>
        </div>
      </div>

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
          {loading && !rrgData ? (
            <div className="flex items-center justify-center h-full text-[#888]">
              <Loader2 size={24} className="animate-spin mr-2" /> Loading RRG data...
            </div>
          ) : rrgData ? (
            <Plot
              data={traces}
              layout={{
                ...layout,
                autosize: true,
              }}
              config={{ responsive: true, displayModeBar: false }}
              style={{ width: '100%', height: '100%' }}
              useResizeHandler
            />
          ) : (
            <div className="flex items-center justify-center h-full text-[#888]">
              No data available. Select sectors and click Refresh.
            </div>
          )}
        </div>

        {/* Sector panel */}
        <div className="w-64 shrink-0 flex flex-col">
          <button
            onClick={() => setShowSectorPanel(!showSectorPanel)}
            className="flex items-center justify-between px-3 py-2 bg-[#ffffff0a] border border-[#ffffff1a] rounded-t text-xs font-mono text-[#ccc]"
          >
            <span>Sectors ({selectedSectors.size - 1}/{indices.length - 1})</span>
            {showSectorPanel ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
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
