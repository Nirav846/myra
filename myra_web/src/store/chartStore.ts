import { create } from 'zustand';

export interface NormalizedViewport {
  startIndex: number;
  endIndex: number;
  startTime: string | null;
  endTime: string | null;
  candleCount: number;
}

const VIEWPORT_KEY = 'chart-viewport';

function loadViewport(): NormalizedViewport | null {
  try {
    const raw = localStorage.getItem(VIEWPORT_KEY);
    if (raw) return JSON.parse(raw) as NormalizedViewport;
  } catch { /* ignore */ }
  return null;
}

function saveViewport(v: NormalizedViewport | null) {
  try {
    if (v) localStorage.setItem(VIEWPORT_KEY, JSON.stringify(v));
    else localStorage.removeItem(VIEWPORT_KEY);
  } catch { /* ignore */ }
}

interface ChartState {
  viewport: NormalizedViewport | null;
  hoveredIndex: number;
  setViewport: (viewport: NormalizedViewport | null) => void;
  setHoveredIndex: (index: number) => void;
}

export const useChartStore = create<ChartState>((set) => ({
  viewport: loadViewport(),
  hoveredIndex: -1,
  setViewport: (viewport) => { saveViewport(viewport); set({ viewport }); },
  setHoveredIndex: (index) => set({ hoveredIndex: index })
}));
