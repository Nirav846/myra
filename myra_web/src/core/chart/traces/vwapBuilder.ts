import { TraceBuilder } from "./types";

export const vwapTraceBuilder: TraceBuilder<number[], any> = {
  id: "vwap",

  buildTraces: (result, context) => {
    const dates = context.data.map((d) => d.date);
    return [
      {
        type: "scattergl",
        mode: "lines",
        x: dates,
        y: result,
        name: "VWAP",
        line: { color: "#888", width: 1.5, dash: "dot" },
        yaxis: "y",
        hovertemplate: "%{y:.2f}",
      },
    ];
  },
};
