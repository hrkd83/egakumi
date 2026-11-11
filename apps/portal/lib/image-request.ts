import { z } from "zod";
import { MAX_BUFFERED_BODY_BYTES, MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";

/** プロンプト欄の文字数上限です。本文のバイト数はモデルの公開上限でも検査します。 */
export const MAX_IMAGE_PROMPT_CHARACTERS = MAX_RUNTIME_STRING_CHARACTERS;
/** 画像の説明文と、提供済みの生成オプションを受け取ります。 */
export const imageGenerationInputSchema = z.object({
  modelId: z.string().min(1).max(160),
  prompt: z.string().min(1).max(MAX_IMAGE_PROMPT_CHARACTERS).refine((value) => value.trim().length > 0),
  size: z.string().max(40), quality: z.string().max(40),
  width: z.number().int().positive().safe(), height: z.number().int().positive().safe(),
  count: z.number().int().positive().safe().optional(),
  outputFormat: z.enum(["png", "jpeg", "webp"]),
}).strict();

/** ブラウザーが指定する生成設定です。接続先URL・キー実値は含めません。 */
export type ImageGenerationInput = z.infer<typeof imageGenerationInputSchema>;

/** 実行時だけ、利用者が選択した台帳のキーIDを要求します。 */
export const imageGenerationExecutionSchema = imageGenerationInputSchema.extend({ apiKeyId: apiKeyIdSchema });

/** 画像本文を二重に送らず、検証済みの応答内の位置と表示方法を示します。 */
export type GeneratedImage = { index: number; kind: "base64"; mimeType: "image/png" | "image/jpeg" | "image/webp" } | { index: number; kind: "url" };

/** APIMの実際のJSONと、画面表示に必要な生成結果の情報です。 */
export type ImageGenerationResult = { model: string; images: GeneratedImage[]; rawResponse: Record<string, unknown>; durationMs: number; requestId?: string };

/** 画像応答とプレビューのメモリー使用量を抑えるための共通上限です。 */
export const MAX_IMAGE_RESPONSE_BYTES = MAX_BUFFERED_BODY_BYTES;
