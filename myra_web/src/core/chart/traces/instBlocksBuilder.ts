import { TraceBuilder } from "./types";
import { InstBlocksResult } from "../../technical-analysis/indicators/instBlocks";

export const instBlocksTraceBuilder: TraceBuilder<InstBlocksResult, any> = {
  id: "instBlocks",

  buildTraces: (result, context) => {
    if (result.dates.length === 0) return [];
    return [
      {
        type: "scattergl",
        mode: "markers+text",
        x: result.dates,
        y: result.y,
        hovertext: result.text,
        text: result.dates.map(() => "IB"),
        textposition: "bottom center",
        textfont: { size: 10, color: "#3b82f6", weight: "bold" },
        marker: {
          size: 16,
          color: "rgba(59, 130, 246, 0.2)",
          symbol: "square",
          line: { color: "#3b82f6", width: 2 },
        },
        name: "Inst. Block",
        yaxis: "y",
        hoverinfo: "text",
      },
    ];
  },
};
