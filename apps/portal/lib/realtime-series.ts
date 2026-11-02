import type { RealtimeModel } from "./model-types.ts";

/** 接続契約の異なるリアルタイム音声を、提供元とは別の固定順で案内します。 */
export const REALTIME_SERIES = [
  { id: "gpt-live", family: "live", label: "GPT-Live", providerId: "openai" },
  { id: "gpt-realtime", family: "realtime", label: "GPT Realtime", providerId: "openai" },
  { id: "voice-live-gpt", family: "voice-live", label: "Voice Live + GPT", providerId: "azure-ai-speech" },
  { id: "gpt-live-transcribe", family: "transcription", label: "GPT Live Transcribe", providerId: "openai" },
] as const satisfies readonly { id: string; family: RealtimeModel["family"]; label: string; providerId: RealtimeModel["providerId"] }[];

/** 表示・選択の保存に使うシリーズ識別子で、モデルの提供元IDとは区別します。 */
export type RealtimeSeriesId = (typeof REALTIME_SERIES)[number]["id"];
type RealtimeSeries = (typeof REALTIME_SERIES)[number];

/** 公開したシリーズ識別子だけを解決し、未知の値を既存シリーズへ置き換えません。 */
export function getRealtimeSeries(id: string): RealtimeSeries | undefined {
  return REALTIME_SERIES.find((series) => series.id === id);
}

/** カタログのfamilyで絞り込み、モデル・方式の追加や提供元の書き換えをせず空のシリーズを除きます。 */
export function groupRealtimeSeries(models: readonly RealtimeModel[]): (RealtimeSeries & { models: RealtimeModel[] })[] {
  return REALTIME_SERIES.flatMap((series) => {
    const seriesModels = models.filter((model) => model.family === series.family);
    return seriesModels.length ? [{ ...series, models: seriesModels }] : [];
  });
}
