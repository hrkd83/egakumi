import { z } from "zod";
import { ApiKeyClientError } from "../api-keys/client.ts";

/** Graph照会の結果と確認時刻を区別し、確認不能を削除済みと誤認しない管理台帳です。 */
const administrationSchema = z.object({
  groups: z.array(z.object({
    id: z.uuid(), displayName: z.string(), directoryStatus: z.enum(["active", "deleted", "ineligible", "unknown"]),
    lastCheckedAt: z.iso.datetime().nullable(), lastErrorCode: z.string().nullable(),
    activeSubscriptions: z.number().int().nonnegative(), affectedSubscriptions: z.number().int().nonnegative(),
  })),
  checkedAt: z.iso.datetime().nullable(), syncErrors: z.number().int().nonnegative(),
  nextCursor: z.uuid().nullable(),
  summary: z.object({
    totalGroups: z.number().int().nonnegative(), activeGroups: z.number().int().nonnegative(),
    deletedGroups: z.number().int().nonnegative(), ineligibleGroups: z.number().int().nonnegative(), unknownGroups: z.number().int().nonnegative(),
    activeSubscriptions: z.number().int().nonnegative(), affectedSubscriptions: z.number().int().nonnegative(),
  }),
});

/** キーの秘密値を含めない、管理者向け所有グループの健全性です。 */
export type ApiKeyAdministrationOverview = z.infer<typeof administrationSchema>;

/** 同じグループは新しい観測へ置換し、全体集計と続きの位置は最新ページから採用します。 */
export function mergeAdministrationPages(previous: ApiKeyAdministrationOverview, page: ApiKeyAdministrationOverview): ApiKeyAdministrationOverview {
  return { ...page, groups: [...new Map([...previous.groups, ...page.groups].map((group) => [group.id.toLowerCase(), group])).values()] };
}

/** 認可済みのAPIだけを使い、取得失敗時に空の一覧を捏造しません。 */
export async function loadApiKeyAdministration(refresh = false, signal?: AbortSignal, cursor?: string): Promise<ApiKeyAdministrationOverview> {
  if (cursor !== undefined && (refresh || !z.uuid().safeParse(cursor).success)) throw new ApiKeyClientError("InvalidRequest");
  const url = cursor ? `/api/api-key-administration?${new URLSearchParams({ cursor })}` : "/api/api-key-administration";
  let response: Response;
  try {
    response = await fetch(url, {
      method: refresh ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
      headers: refresh ? { "Content-Type": "application/json" } : undefined,
      body: refresh ? JSON.stringify({ action: "refresh" }) : undefined,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(60_000)]) : AbortSignal.timeout(60_000),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiKeyClientError("NetworkUnavailable");
  }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new ApiKeyClientError("ServiceUnavailable"); }
  if (!response.ok) {
    const failure = z.object({ error: z.object({ code: z.string() }) }).safeParse(payload);
    throw new ApiKeyClientError(failure.success ? failure.data.error.code : "ServiceUnavailable");
  }
  const parsed = administrationSchema.safeParse(payload);
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data;
}
