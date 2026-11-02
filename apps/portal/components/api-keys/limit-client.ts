import { z } from "zod";
import { apiKeyIdSchema } from "../../lib/api-keys/contracts.ts";
import {
  apiKeyLimitProfileSchema, apiKeyLimitValuesSchema, updateApiKeyLimitsSchema,
  type ApiKeyLimitProfile, type ApiKeyLimitValues, type UpdateApiKeyLimits,
} from "../../lib/api-keys/limit-contracts.ts";
import { ApiKeyClientError } from "./client.ts";

/** 空欄と無制限を区別し、入力途中の文字列を数値として保存しない編集状態です。 */
export type ApiKeyLimitDraft = {
  requestEnabled: boolean; calls: string; seconds: string;
  tokenRateEnabled: boolean; tokensPerMinute: string;
  quotaEnabled: boolean; quotaTokens: string; quotaPeriod: NonNullable<ApiKeyLimitValues["tokenQuota"]>["period"];
};

/** 保存値を入力欄に展開します。無制限の項目には暗黙の上限値を補いません。 */
export function limitDraftFromProfile(profile: ApiKeyLimitProfile): ApiKeyLimitDraft {
  return {
    requestEnabled: profile.requestRate !== null,
    calls: profile.requestRate ? String(profile.requestRate.calls) : "",
    seconds: profile.requestRate ? String(profile.requestRate.renewalPeriodSeconds) : "",
    tokenRateEnabled: profile.tokensPerMinute !== null,
    tokensPerMinute: profile.tokensPerMinute === null ? "" : String(profile.tokensPerMinute),
    quotaEnabled: profile.tokenQuota !== null,
    quotaTokens: profile.tokenQuota ? String(profile.tokenQuota.tokens) : "",
    quotaPeriod: profile.tokenQuota?.period ?? "Monthly",
  };
}

/** 上限を有効にした項目だけを整数として検証し、無制限はnullで送ります。 */
export function limitsFromDraft(draft: ApiKeyLimitDraft): ApiKeyLimitValues {
  const integer = (value: string) => /^\d+$/.test(value) ? Number(value) : Number.NaN;
  const result = apiKeyLimitValuesSchema.safeParse({
    requestRate: draft.requestEnabled ? { calls: integer(draft.calls), renewalPeriodSeconds: integer(draft.seconds) } : null,
    tokensPerMinute: draft.tokenRateEnabled ? integer(draft.tokensPerMinute) : null,
    tokenQuota: draft.quotaEnabled ? { tokens: integer(draft.quotaTokens), period: draft.quotaPeriod } : null,
  });
  if (!result.success) throw new ApiKeyClientError("InvalidLimitSettings");
  return result.data;
}

/** 同一オリジンのプロファイルAPIだけを使い、SQLの版番号で上書き競合を検出します。 */
async function request(id: string, input?: UpdateApiKeyLimits, signal?: AbortSignal): Promise<ApiKeyLimitProfile> {
  if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyClientError("NotFound");
  if (input !== undefined && !updateApiKeyLimitsSchema.safeParse(input).success) throw new ApiKeyClientError("InvalidLimitSettings");
  let response: Response;
  try {
    response = await fetch(`/api/api-keys/${encodeURIComponent(id)}/limits`, {
      method: input === undefined ? "GET" : "PUT", credentials: "same-origin", cache: "no-store",
      headers: input === undefined ? undefined : { "Content-Type": "application/json" },
      body: input === undefined ? undefined : JSON.stringify(input),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(45_000)]) : AbortSignal.timeout(45_000),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiKeyClientError("NetworkUnavailable");
  }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new ApiKeyClientError("ServiceUnavailable"); }
  if (!response.ok) {
    const result = z.object({ error: z.object({ code: z.string() }) }).safeParse(payload);
    throw new ApiKeyClientError(result.success ? result.data.error.code : "ServiceUnavailable");
  }
  const result = apiKeyLimitProfileSchema.safeParse(payload);
  if (!result.success) throw new ApiKeyClientError("ServiceUnavailable");
  return result.data;
}

/** 現在の保存設定を取得します。未設定はversion=0・全項目nullで返されます。 */
export function loadApiKeyLimits(id: string, signal?: AbortSignal): Promise<ApiKeyLimitProfile> {
  return request(id, undefined, signal);
}

/** 読み込み時の版を指定して保存し、競合時の自動上書きは行いません。 */
export function saveApiKeyLimits(id: string, input: UpdateApiKeyLimits, signal?: AbortSignal): Promise<ApiKeyLimitProfile> {
  return request(id, input, signal);
}
