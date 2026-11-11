import { z } from "zod";
import { MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";

/** 単独フィールドの安全上限です。本文全体はGatewayのUTF-8バイト上限でも検査します。 */
export const MAX_RERANK_TEXT_CHARACTERS = MAX_RUNTIME_STRING_CHARACTERS;
const nonblankText = z.string().min(1).max(MAX_RERANK_TEXT_CHARACTERS).refine((value) => value.trim().length > 0);

/** 接続先や秘密を受け取らず、検索質問と候補文書を自由に編集できる入力です。 */
export const rerankInputSchema = z.object({
  modelId: z.string().min(1).max(160),
  query: nonblankText,
  documents: z.array(nonblankText).min(1),
  topN: z.number().int().positive().safe(),
}).strict();

/** リランク画面とBFFが共有する入力型です。 */
export type RerankInput = z.infer<typeof rerankInputSchema>;

/** 実行時だけ台帳IDを受け取り、現在の所属・購読権限をサーバー側で再確認します。 */
export const rerankExecutionSchema = rerankInputSchema.extend({ apiKeyId: apiKeyIdSchema });

/** 元の候補文書との対応を失わないリランク応答です。 */
export const rerankResultSchema = z.object({
  model: z.string().min(1).max(160),
  ranking: z.array(z.object({
    index: z.number().int().nonnegative().safe(),
    relevanceScore: z.number().finite().min(0).max(1),
    document: nonblankText,
  }).strict()).min(1),
  searchUnits: z.number().int().nonnegative().safe().optional(),
  durationMs: z.number().finite().nonnegative(),
  requestId: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/).optional(),
  rawResponse: z.record(z.string(), z.unknown()),
}).strict().refine((value) => new Set(value.ranking.map((entry) => entry.index)).size === value.ranking.length
  && value.ranking.every((entry, index) => index === 0 || value.ranking[index - 1].relevanceScore >= entry.relevanceScore));

/** 検証済みの順位・関連度と、受信した元のJSONです。 */
export type RerankResult = z.infer<typeof rerankResultSchema>;
