import { TraceBuilder } from "./types";
import { DelVwapBandsResult } from "../../technical-analysis/indicators/delVwapBands";

export const delVwapBandsTraceBuilder: TraceBuilder<DelVwapBandsResult, any> = {
  id: "delVwapBands",

  buildTraces: (result, context) => {
    const dates = context.data.map((d) => d.date);
    return [
      {
        type: "scattergl",
        mode: "lines",
        x: dates,
        y: result.upper,
        name: "Del. VWAP High",
        line: { color: "rgba(251, 146, 60, 0.7)", width: 1.5, dash: "dot" },
        yaxis: "y",
        hoverinfo: "none",
      },
      {
        type: "scattergl",
        mode: "lines",
        x: dates,
        y: result.lower,
        name: "Del. VWAP Low",
        line: { color: "rgba(251, 146, 60, 0.7)", width: 1.5, dash: "dot" },
        fill: "tonexty",
        fillcolor: "rgba(251, 146, 60, 0.1)",
        yaxis: "y",
        hoverinfo: "none",
      },
      {
        type: "scattergl",
        mode: "lines",
        x: dates,
        y: result.mid,
        name: "Del. VWAP",
        line: { color: "#fbbf24", width: 2, dash: "solid" },
        yaxis: "y",
        hovertemplate: "Del VWAP: %{y:.2f}",
      },
    ];
  },
};
