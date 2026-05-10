import { LayoutBuilder } from "./types";

export const fibonacciLayoutBuilder: LayoutBuilder = {
  id: "fibonacci",

  buildShapes: (context) => {
    const shapes: any[] = [];
    const { data } = context;
    if (data.length === 0) return shapes;

    const lows = data.map((d) => d.low);
    const highs = data.map((d) => d.high);

    const minLow = Math.min(...lows);
    const maxHigh = Math.max(...highs);
    const diff = maxHigh - minLow;

    const levels = [
      { ratio: 0, color: "rgba(255, 255, 255, 0.5)" },
      { ratio: 0.236, color: "rgba(244, 63, 94, 0.5)" },
      { ratio: 0.382, color: "rgba(250, 204, 21, 0.5)" },
      { ratio: 0.5, color: "rgba(74, 222, 128, 0.5)" },
      { ratio: 0.618, color: "rgba(56, 189, 248, 0.5)" },
      { ratio: 0.786, color: "rgba(168, 85, 247, 0.5)" },
      { ratio: 1, color: "rgba(255, 255, 255, 0.5)" },
    ];

    levels.forEach((level) => {
      const y = maxHigh - diff * level.ratio;
      shapes.push({
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: y,
        y1: y,
        line: { color: level.color, width: 1, dash: "dot" },
        label: {
          text: `Fib ${(level.ratio * 100).toFixed(1)}% (${y.toFixed(2)})`,
          font: { size: 10, color: level.color },
          textposition: "start",
        },
      });
    });

    return shapes;
  },
};
