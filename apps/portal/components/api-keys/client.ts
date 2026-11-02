import { z } from "zod";
import { apiKeyIdSchema, apiKeyClientTypeSchema, apimSubscriptionIdSchema, createApiKeySchema, transferApiKeyOwnershipSchema } from "../../lib/api-keys/contracts.ts";
import type { ApiKeyOverview, ApiKeyRecord, ApiKeySecrets, CreateApiKeyInput } from "@/lib/api-keys/contracts";

/** 管理画面が扱う公開台帳だけを検証し、予期しない応答を表示しません。 */
const recordSchema = z.object({
  id: apiKeyIdSchema, apimSubscriptionId: apimSubscriptionIdSchema,
  groupId: z.string(), productId: z.string(), displayName: z.string(),
  state: z.enum(["creating", "active", "suspended", "deleting", "deleted"]),
  budgetMode: z.literal("unlimited"),
  clientType: apiKeyClientTypeSchema.nullable(),
  systemName: z.string().nullable(), teamName: z.string().nullable(),
  companyId: z.uuid().nullable(), companyName: z.string().nullable(), contactEmail: z.email().nullable(),
  createdAt: z.string(), updatedAt: z.string(), lastReviewedAt: z.string().nullable(),
});
const groupSchema = z.object({
    id: z.string(), displayName: z.string(),
    directoryStatus: z.enum(["active", "deleted", "ineligible", "unknown"]),
    lastCheckedAt: z.string().nullable(),
    products: z.array(z.object({ id: z.string(), displayName: z.string(), maxSubscriptions: z.number().int().nonnegative() })),
});
/** 所属の有無をGraphで確認した、移管先の候補です。 */
export type ApiKeyOwnerTarget = z.infer<typeof groupSchema> & { isMember: boolean };
/** ページの続きを明示し、候補の一部だけを全件と表示しない検索結果です。 */
export type ApiKeyOwnerTargets = { groups: ApiKeyOwnerTarget[]; nextCursor: string | null };
const overviewSchema = z.object({
  groups: z.array(groupSchema),
  keys: z.array(recordSchema),
});
const secretsSchema = z.object({ primaryKey: z.string().min(1), secondaryKey: z.string().min(1) });
const companySchema = z.object({
  id: z.uuid(), name: z.string().min(1), isActive: z.boolean(), createdAt: z.string(), updatedAt: z.string(),
});

/** キーの利用会社として選択できる、管理者が登録した会社です。 */
export type ApiKeyCompany = z.infer<typeof companySchema>;

/** 上流本文や秘密値を持たず、画面の翻訳キーだけに変換できるエラーです。 */
export class ApiKeyClientError extends Error {
  readonly code: string;
  constructor(code: string) { super(code); this.name = "ApiKeyClientError"; this.code = code; }
}

/**
 * 同じポータルのAPIへだけ送信し、ブラウザーの応答キャッシュを使いません。
 * @remarks APIキーをURL・ログ・永続ストレージへ書きません。
 */
async function request(path: string, body?: unknown, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(45_000)]) : AbortSignal.timeout(45_000),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiKeyClientError("NetworkUnavailable");
  }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new ApiKeyClientError("ServiceUnavailable"); }
  if (!response.ok) {
    const error = z.object({ error: z.object({ code: z.string() }) }).safeParse(payload);
    throw new ApiKeyClientError(error.success ? error.data.error.code : "ServiceUnavailable");
  }
  return payload;
}

/** 所属確認済みのグループと、管理可能な購読だけを取得します。 */
export async function loadApiKeys(signal?: AbortSignal): Promise<ApiKeyOverview> {
  const parsed = overviewSchema.safeParse(await request("/api/api-keys", undefined, signal));
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data;
}

/** 同じ冪等キーを再試行にも使い、通信切断による二重発行を防ぎます。 */
export async function issueApiKey(input: CreateApiKeyInput): Promise<ApiKeyRecord> {
  const validated = createApiKeySchema.safeParse(input);
  if (!validated.success) throw new ApiKeyClientError("InvalidRequest");
  const parsed = recordSchema.safeParse(await request("/api/api-keys", validated.data));
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data;
}

/** 有効な会社だけを取得し、未登録と通信失敗を区別して表示します。 */
export async function loadApiKeyCompanies(signal?: AbortSignal): Promise<ApiKeyCompany[]> {
  const parsed = z.object({ companies: z.array(companySchema) }).safeParse(await request("/api/companies", undefined, signal));
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data.companies.filter((company) => company.isActive);
}

/** 利用者が選択した管理操作の結果を、秘密値と公開台帳に分けて検証します。 */
export async function actOnApiKey(id: string, action: "reveal", signal?: AbortSignal): Promise<ApiKeySecrets>;
export async function actOnApiKey(id: string, action: "suspend" | "resume" | "review" | "delete", signal?: AbortSignal): Promise<ApiKeyRecord>;
export async function actOnApiKey(id: string, action: "reveal" | "suspend" | "resume" | "review" | "delete", signal?: AbortSignal): Promise<ApiKeySecrets | ApiKeyRecord> {
  const payload = await request(`/api/api-keys/${encodeURIComponent(id)}`, { action }, signal);
  const parsed = action === "reveal" ? secretsSchema.safeParse(payload) : recordSchema.safeParse(payload);
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data;
}

/** 元の所有グループのメンバーだけが、環境の対象条件に一致する移管先を検索します。 */
export async function findApiKeyOwnerGroups(id: string, search: string, cursor?: string, signal?: AbortSignal): Promise<ApiKeyOwnerTargets> {
  const query = search.trim();
  if (!apiKeyIdSchema.safeParse(id).success || query.length < 2 || query.length > 80) throw new ApiKeyClientError("InvalidRequest");
  if (cursor !== undefined && (!cursor || cursor.length > 24000)) throw new ApiKeyClientError("InvalidRequest");
  const parameters = new URLSearchParams({ search: query });
  if (cursor !== undefined) parameters.set("cursor", cursor);
  const result = z.object({ groups: z.array(groupSchema.extend({ isMember: z.boolean() })), nextCursor: z.string().min(1).max(24000).nullable() })
    .safeParse(await request(`/api/api-keys/${encodeURIComponent(id)}/owner?${parameters}`, undefined, signal));
  if (!result.success) throw new ApiKeyClientError("ServiceUnavailable");
  return result.data;
}

/** 所有グループだけを変更します。キーや製品、利用制限の変更要求は送信しません。 */
export async function transferApiKeyOwner(id: string, input: { targetGroupId: string; expectedSourceGroupId: string }, signal?: AbortSignal): Promise<ApiKeyRecord> {
  if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyClientError("NotFound");
  const parsed = transferApiKeyOwnershipSchema.safeParse(input);
  if (!parsed.success) throw new ApiKeyClientError("InvalidRequest");
  const result = recordSchema.safeParse(await request(`/api/api-keys/${encodeURIComponent(id)}/owner`, parsed.data, signal));
  if (!result.success) throw new ApiKeyClientError("ServiceUnavailable");
  return result.data;
}

/** 安全に表示できるコード以外は、一般的な障害メッセージへまとめます。 */
export function apiKeyErrorCode(error: unknown): string {
  return error instanceof ApiKeyClientError ? error.code : "ServiceUnavailable";
}
