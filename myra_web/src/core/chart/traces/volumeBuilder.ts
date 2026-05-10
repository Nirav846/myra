import { TraceBuilder } from "./types";

export const volumeTraceBuilder: TraceBuilder<number[], any> = {
  id: "volume",
  buildTraces: (result, context) => {
    const dates = context.data.map((d) => d.date);
    const volumeColors = context.data.map((d) =>
      d.close >= d.open ? "#22c55e" : "#ef4444",
    );

    return [
      {
        type: "bar",
        x: dates,
        y: result,
        name: "Vol",
        yaxis: "y2",
        marker: { color: volumeColors },
        hovertemplate: "%{y:.2s}<extra></extra>",
      },
    ];
  },
};
