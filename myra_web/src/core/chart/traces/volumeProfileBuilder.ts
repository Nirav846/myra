import { TraceBuilder, TraceBuilderContext } from "./types";
import {
  VolumeProfileResult,
  VolumeProfileConfig,
} from "../../technical-analysis/volumeProfile";

export const volumeProfileTraceBuilder: TraceBuilder<
  VolumeProfileResult,
  VolumeProfileConfig
> = {
  id: "volumeProfile",

  buildTraces: (result, context) => {
    const traces: any[] = [];

    // Ensure volume profile doesn't stretch across the entire screen
    // we can place it on x2 and manage the max size via layout.
    if (result.volProfileX.length > 0) {
      traces.push({
        type: "bar",
        orientation: "h",
        xaxis: "x2",
        yaxis: "y",
        x: result.volProfileX,
        y: result.volProfileY,
        marker: { color: result.volProfileColors },
        name: "Volume Profile",
        opacity: 0.5,
        hoverinfo: "none",
        showlegend: false,
      });

      traces.push({
        type: "bar",
        orientation: "h",
        xaxis: "x2",
        yaxis: "y",
        x: result.deliveryProfileX,
        y: result.deliveryProfileY,
        marker: { color: result.deliveryProfileColors },
        name: "Delivery Profile",
        opacity: 0.8,
        hoverinfo: "none",
        showlegend: false,
      });
    }

    return traces;
  },

  buildShapes: (result, context, config?: VolumeProfileConfig) => {
    const shapes: any[] = [];

    if (config?.showDeliveryProfile !== false && result.pocVolY !== null) {
      shapes.push({
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: result.pocVolY,
        y1: result.pocVolY,
        line: { color: "rgba(136, 136, 136, 0.7)", width: 1.5, dash: "solid" },
        label: {
          text: "Vol POC",
          font: { size: 10, color: "#888" },
          textposition: "start",
        },
      });
    }

    if (config?.showDeliveryProfile !== false && result.pocDelY !== null) {
      shapes.push({
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: result.pocDelY,
        y1: result.pocDelY,
        line: { color: "#06b6d4", width: 2, dash: "solid" },
        label: {
          text: "Del POC",
          font: { size: 10, color: "#06b6d4" },
          textposition: "start",
        },
      });
    }

    // Add Delivery SR lines
    if (config?.showDeliverySR !== false) {
      const maxProf = Math.max(...result.deliveryProfileX);
      const bins = result.deliveryProfileX.length;
      for (let i = 2; i < bins - 2; i++) {
        if (
          result.deliveryProfileX[i] > result.deliveryProfileX[i - 1] &&
          result.deliveryProfileX[i] > result.deliveryProfileX[i - 2] &&
          result.deliveryProfileX[i] > result.deliveryProfileX[i + 1] &&
          result.deliveryProfileX[i] > result.deliveryProfileX[i + 2] &&
          result.deliveryProfileX[i] > maxProf * 0.3
        ) {
          const y = result.deliveryProfileY[i];
          shapes.push({
            type: "line",
            xref: "paper",
            x0: 0,
            x1: 1,
            yref: "y",
            y0: y,
            y1: y,
            line: { color: "rgba(234, 179, 8, 0.6)", width: 2, dash: "dot" },
            label: {
              text: `Del SR`,
              font: { size: 10, color: "#eab308" },
              textposition: "end",
            },
          });
        }
      }
    }

    return shapes;
  },
};
