/**
 * Chart Layout Configuration
 *
 * Centralized Plotly layout configuration for consistent chart appearance.
 * Separates layout concerns from rendering logic.
 */

import type { Layout } from 'plotly.js-dist-min';

export interface ChartLayoutConfig {
  showGrid: boolean;
  gridColor: string;
  backgroundColor: string;
  textColor: string;
  fontFamily: string;
  candleWidth: number;
}

const DEFAULT_CONFIG: ChartLayoutConfig = {
  showGrid: true,
  gridColor: '#2a2c34',
  backgroundColor: '#1a1c24',
  textColor: '#888899',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  candleWidth: 0.8,
};

/**
 * Build base layout for candlestick chart
 */
export function buildBaseLayout(config: Partial<ChartLayoutConfig> = {}): Layout {
  const cfg = { ...DEFAULT_CONFIG, ...config };

  return {
    // Container
    autosize: true,
    paper_bgcolor: cfg.backgroundColor,
    plot_bgcolor: cfg.backgroundColor,

    // Margins
    margin: { l: 60, r: 60, t: 10, b: 40 },

    // X-axis (date/time)
    xaxis: {
      type: 'category' as const,
      categoriesorder: 'array' as const,
      showgrid: cfg.showGrid,
      gridcolor: cfg.gridColor,
      gridwidth: 1,
      zeroline: false,
      showline: false,
      tickfont: {
        family: cfg.fontFamily,
        size: 11,
        color: cfg.textColor,
      },
      tickformat: '%Y-%m-%d',
      tickangle: -45,
      nticks: 12,
      automargin: true,
    },

    // Y-axis (price)
    yaxis: {
      showgrid: cfg.showGrid,
      gridcolor: cfg.gridColor,
      gridwidth: 1,
      zeroline: false,
      showline: false,
      tickfont: {
        family: cfg.fontFamily,
        size: 11,
        color: cfg.textColor,
      },
      tickformat: '.2f',
      automargin: true,
      fixedrange: false, // Allow zooming
    },

    // Remove default title
    title: undefined,

    // Hide modebar in production (enable for dev)
    modebar: {
      bgcolor: cfg.backgroundColor + '80',
      color: cfg.textColor,
      activecolor: '#ffffff',
    },

    // Legend
    legend: {
      font: {
        family: cfg.fontFamily,
        size: 11,
        color: cfg.textColor,
      },
      bgcolor: cfg.backgroundColor + '80',
      bordercolor: cfg.gridColor,
      borderwidth: 1,
    },

    // Hover mode
    hovermode: 'x unified' as const,

    // Drag modes
    dragmode: 'zoom' as const,

    // Spikes (crosshair lines)
    spikedistance: -1,
    hoverdistance: -1,
  } as Layout;
}

/**
 * Build layout with multiple y-axes for sub-panes
 */
export function buildMultiPaneLayout(
  paneConfigs: Array<{
    id: string;
    side: 'left' | 'right';
    overlay?: boolean;
    domain?: [number, number];
    title?: string;
  }>,
  config: Partial<ChartLayoutConfig> = {}
): Layout {
  const baseLayout = buildBaseLayout(config);
  const layout = baseLayout as any;

  // Add additional y-axes for each pane
  paneConfigs.forEach((pane, index) => {
    if (index === 0) return; // Skip first pane (uses default yaxis)

    const axisKey = `yaxis${index + 1}`;
    layout[axisKey] = {
      ...layout.yaxis,
      side: pane.side,
      overlaying: pane.overlay ? 'y' : undefined,
      domain: pane.domain || undefined,
      title: pane.title ? { text: pane.title } : undefined,
      anchor: 'free',
      position: pane.side === 'right' ? 0.95 : 0.05,
    };
  });

  return layout as Layout;
}

/**
 * Default Plotly config for chart interactions
 */
export const DEFAULT_PLOTLY_CONFIG = {
  responsive: true,
  displayModeBar: false as const,
  scrollZoom: true,
  doubleClick: 'reset' as const,
  showTips: false,
  displaylogo: false,
  modeBarButtonsToRemove: [
    'lasso2d' as any,
    'select2d' as any,
    'autoScale2d' as any,
  ],
  toImageButtonOptions: {
    format: 'png' as const,
    filename: 'chart',
    height: 800,
    width: 1200,
    scale: 2,
  },
};
