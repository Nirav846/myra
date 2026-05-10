import { IndicatorModule } from "../types";

export interface InstBlocksResult {
  dates: string[];
  y: number[];
  text: string[];
}

export const instBlocksIndicator: IndicatorModule<any, InstBlocksResult> = {
  id: "instBlocks",

  calculate: (data, config, context) => {
    // we use data directly
    const dates: string[] = [];
    const y: number[] = [];
    const text: string[] = [];

    // simple SMA over 20 for volumes
    const avgVol20: number[] = [];
    for (let i = 0; i < data.length; i++) {
      if (i < 19) {
        avgVol20.push(0);
        continue;
      }
      let sum = 0;
      for (let j = 0; j < 20; j++)
        sum += data[i - j].volume_final
          ? Number(data[i - j].volume_final)
          : Number(data[i - j].volume) || 0;
      avgVol20.push(sum / 20);
    }

    for (let i = 0; i < data.length; i++) {
      const d = data[i];
      const vol =
        d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1;
      const delPct =
        d.delivery_pct != null
          ? d.delivery_pct
          : (d.delivery_final ? (Number(d.delivery_final) / vol) * 100 : 0) ||
            0;
      const delVol = vol * (delPct / 100);

      const avgV = avgVol20[i] || 1;

      if (vol > avgV * 2.5 && delPct > 60) {
        dates.push(d.date);
        const isBullish = d.close >= d.open;
        y.push(isBullish ? d.low * 0.99 : d.high * 1.01);
        text.push(`Inst. Block<br>Del Vol: ${(delVol / 1e6).toFixed(2)}M`);
      }
    }

    return {
      dates,
      y,
      text,
    };
  },
};
