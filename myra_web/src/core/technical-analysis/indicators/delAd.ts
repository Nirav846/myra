import { IndicatorModule } from "../types";

export const delAdIndicator: IndicatorModule<any, number[]> = {
  id: "delAd",

  calculate: (data, config, context) => {
    const delAdLine: number[] = [];
    let currentAd = 0;

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

      let multiplier =
        d.high === d.low
          ? 0
          : (d.close - d.low - (d.high - d.close)) / (d.high - d.low);
      currentAd += multiplier * delVol;
      delAdLine.push(currentAd);
    }

    return delAdLine;
  },
};
