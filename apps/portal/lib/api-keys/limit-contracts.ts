import { z } from "zod";

const tokenCount = z.number().int().min(1).max(2_147_483_647);

/** APIMが動的に読み取れる制限だけを保存します。nullは制限を設けない指定です。 */
export const apiKeyLimitValuesSchema = z.object({
  requestRate: z.object({
    calls: z.number().int().min(1).max(1_000_000),
    renewalPeriodSeconds: z.number().int().min(1).max(300),
  }).strict().nullable(),
  tokensPerMinute: tokenCount.nullable(),
  tokenQuota: z.object({
    tokens: tokenCount,
    period: z.enum(["Hourly", "Daily", "Weekly", "Monthly", "Yearly"]),
  }).strict().nullable(),
}).strict();

/** 保存済みの設定です。version=0は、まだ変更していない全項目無制限を表します。 */
export const apiKeyLimitProfileSchema = apiKeyLimitValuesSchema.extend({
  version: z.number().int().min(0).max(2_147_483_647),
  updatedAt: z.iso.datetime().nullable(),
}).strict();

/** 画面が読んだ版を必須にし、別の管理者による変更を上書きしない更新要求です。 */
export const updateApiKeyLimitsSchema = apiKeyLimitValuesSchema.extend({
  expectedVersion: z.number().int().min(0).max(2_147_483_646),
}).strict();

/** 呼び出し回数・トークンの任意の上限値です。 */
export type ApiKeyLimitValues = z.infer<typeof apiKeyLimitValuesSchema>;
/** 版と最終更新時刻を含む、秘密情報を持たない制限プロファイルです。 */
export type ApiKeyLimitProfile = z.infer<typeof apiKeyLimitProfileSchema>;
/** 楽観ロック用の版を伴う、プロファイル全体の更新指定です。 */
export type UpdateApiKeyLimits = z.infer<typeof updateApiKeyLimitsSchema>;

/** 呼び出し元が変更できるよう、共有オブジェクトではなく毎回新しい初期値を返します。 */
export function unlimitedApiKeyLimits(): ApiKeyLimitProfile {
  return { version: 0, requestRate: null, tokensPerMinute: null, tokenQuota: null, updatedAt: null };
}
