import { z } from "zod";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";

/** 1件の入力欄の上限です。API本文全体はモデルのバイト数上限でも検査します。 */
export const MAX_EMBEDDING_TEXT_CHARACTERS = 32_000;
/** 埋め込み対象の本文とAPIへ送るオプションを受け付けます。 */
export const embeddingInputSchema = z.object({
  modelId: z.string().min(1).max(160),
  dimensions: z.number().int().positive().max(3_072).nullable(),
  inputType: z.string().min(1).max(80).nullable(),
  texts: z.array(z.string().min(1).max(MAX_EMBEDDING_TEXT_CHARACTERS).refine((value) => value.trim().length > 0)).min(1).max(16),
}).strict();

/** 接続先や秘密キーを受け取らない埋め込み画面の入力です。 */
export type EmbeddingInput = z.infer<typeof embeddingInputSchema>;

/** 実行時は、現在の所属とAPI利用権限を再確認するための台帳IDを必須にします。 */
export const embeddingExecutionSchema = embeddingInputSchema.extend({ apiKeyId: apiKeyIdSchema });

const vectorSchema = z.array(z.number().finite()).min(1).max(3_072);

/** BFFの正常応答です。ベクトル全体と上流JSONを残し、表示の要約で情報を失わせません。 */
export const embeddingResultSchema = z.object({
  model: z.string().min(1).max(200),
  vectorCount: z.number().int().positive().max(16),
  dimensions: z.number().int().positive().max(3_072),
  vectors: z.array(vectorSchema).min(1).max(16),
  usage: z.object({ inputTokens: z.number().int().nonnegative().safe(), totalTokens: z.number().int().nonnegative().safe().optional() }).optional(),
  durationMs: z.number().finite().nonnegative(),
  requestId: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/).optional(),
  rawResponse: z.record(z.string(), z.unknown()),
}).strict().refine((value) => value.vectorCount === value.vectors.length && value.vectors.every((vector) => vector.length === value.dimensions));

/** APIMから実際に受信した埋め込みと使用量です。 */
export type EmbeddingResult = z.infer<typeof embeddingResultSchema>;
