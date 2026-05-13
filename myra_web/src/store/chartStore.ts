import { create } from 'zustand';

export interface NormalizedViewport {
  startIndex: number;
  endIndex: number;
  startTime: string | null;
  endTime: string | null;
  candleCount: number;
}

interface ChartState {
  viewport: NormalizedViewport | null;
  hoveredIndex: number;
  setViewport: (viewport: NormalizedViewport | null) => void;
  setHoveredIndex: (index: number) => void;
}

export const useChartStore = create<ChartState>((set) => ({
  viewport: null,
  hoveredIndex: -1,
  setViewport: (viewport) => set({ viewport }),
  setHoveredIndex: (index) => set({ hoveredIndex: index })
}));
