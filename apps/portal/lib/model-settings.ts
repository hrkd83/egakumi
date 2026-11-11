import type { EmbeddingInput } from "./embedding-request.ts";
import type { ImageGenerationInput } from "./image-request.ts";
import type { SpeechInput } from "./speech-request.ts";
import type { RealtimeInput } from "./realtime-request.ts";
import type { RerankInput } from "./rerank-request.ts";

/** 画面の再作成後も保持する設定だけです。本文・ファイル・購読・実行状態は含めません。 */
export type ModelSettings = {
  embeddings: Pick<EmbeddingInput, "modelId" | "dimensions" | "inputType">;
  image: Pick<ImageGenerationInput, "modelId" | "size" | "quality" | "outputFormat" | "width" | "height" | "count">;
  speech: Pick<SpeechInput, "modelId" | "voice" | "rate">;
  realtime: Pick<RealtimeInput, "modelId" | "voice" | "maxOutputTokens">;
  rerank: Pick<RerankInput, "modelId" | "topN">;
};
/** カテゴリーと提供元・シリーズで分離する、現在のPortal表示内だけの設定です。 */
export type ScopedModelSettings = { [Category in keyof ModelSettings]: Record<string, ModelSettings[Category]> };

const fields = {
  embeddings: ["modelId", "dimensions", "inputType"],
  image: ["modelId", "size", "quality", "outputFormat", "width", "height", "count"],
  speech: ["modelId", "voice", "rate"],
  realtime: ["modelId", "voice", "maxOutputTokens"],
  rerank: ["modelId", "topN"],
} as const;

/** 許可した設定を複製し、呼出元が余分な本文や実行状態を持っていても保存しません。 */
export function modelSettingsDraft<Category extends keyof ModelSettings>(category: Category, input: ModelSettings[Category]): ModelSettings[Category] {
  const source = input as unknown as Record<string, unknown>;
  return Object.fromEntries(fields[category].filter((field) => source[field] !== undefined).map((field) => [field, source[field]])) as ModelSettings[Category];
}

/** 同じ公開モデルの設定を復元し、カタログ変更で無効になった設定は現在の既定値へ戻します。 */
export function restoreModelSettings<Category extends keyof ModelSettings, Input extends ModelSettings[Category]>(category: Category, defaults: Input, saved: ModelSettings[Category] | undefined, validate: (input: Input) => unknown): Input {
  if (saved?.modelId !== defaults.modelId) return { ...defaults };
  const input = { ...defaults, ...modelSettingsDraft(category, saved) };
  try { validate(input); return input; } catch { return { ...defaults }; }
}
