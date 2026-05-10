import { IndicatorModule } from "../types";
import {
  calculateVolumeProfile,
  VolumeProfileResult,
  VolumeProfileConfig,
} from "../volumeProfile";

export const volumeProfileIndicator: IndicatorModule<
  VolumeProfileResult | null,
  VolumeProfileConfig
> = {
  id: "volumeProfile",

  calculate: (data, config, context) => {
    const resolution = config?.resolution || "auto";
    if (resolution !== "cumulative" && !context?.viewport) return null;
    return calculateVolumeProfile(
      data,
      context?.viewport || null,
      config || { resolution: "auto" },
    );
  },
};
