import { memo, useRef, useEffect } from "react";
import Plot from "react-plotly.js";
import { useChartStore } from "../../store/chartStore";

interface PlotlyCanvasProps {
  data: any[];
  layout: any;
  config?: any;
  style?: React.CSSProperties;
  dates: string[];
}

export const PlotlyCanvas = memo(
  ({ data, layout, config, style, dates }: PlotlyCanvasProps) => {
    const setViewport = useChartStore((state) => state.setViewport);
    const setHoveredIndex = useChartStore((state) => state.setHoveredIndex);

    const handleRelayout = (e: any) => {
      if (
        e["xaxis.range[0]"] !== undefined &&
        e["xaxis.range[1]"] !== undefined
      ) {
        let from = e["xaxis.range[0]"];
        let to = e["xaxis.range[1]"];

        if (typeof from === "string") {
          const idx = dates.indexOf(from);
          if (idx !== -1) from = idx;
        }
        if (typeof to === "string") {
          const idx = dates.indexOf(to);
          if (idx !== -1) to = idx;
        }

        if (typeof from === "number" && typeof to === "number") {
          const startIndex = Math.min(from, to);
          const endIndex = Math.max(from, to);
          setViewport({
            startIndex,
            endIndex,
            startTime: dates[Math.floor(startIndex)] || null,
            endTime: dates[Math.ceil(endIndex)] || null,
            candleCount: endIndex - startIndex + 1,
          });
        } else {
          setViewport(null);
        }
      } else if (e["xaxis.autorange"]) {
        setViewport(null);
      }
    };

    const handleHover = (e: any) => {
      if (
        e.points &&
        e.points.length > 0 &&
        e.points[0].pointIndex !== undefined
      ) {
        setHoveredIndex(e.points[0].pointIndex);
      }
    };

    const handleUnhover = () => {
      setHoveredIndex(-1);
    };

    return (
      <Plot
        data={data}
        layout={layout}
        config={config}
        style={style}
        onRelayout={handleRelayout}
        onHover={handleHover}
        onUnhover={handleUnhover}
        useResizeHandler={true}
      />
    );
  },
);
