import "server-only";

import { ApiKeyError, type ApiKeyActor, type CreateApiKeyInput, type TransferApiKeyOwnershipInput } from "../api-keys/contracts.ts";
import { getApiKeyService } from "../api-keys/service.ts";
import { getApiKeyLimitService } from "../api-keys/limit-service.ts";
import type { UpdateApiKeyLimits } from "../api-keys/limit-contracts.ts";
import { getApiKeyAdministrationService } from "../api-key-administration/service.ts";
import type { ApiKeyAdministrationPageRequest } from "../api-key-administration/contracts.ts";
import { getCompanyAdministrationService } from "../company-administration/service.ts";
import type { CreateCompanyInput, UpdateCompanyInput } from "../company-administration/contracts.ts";
import { getSubscriptionSyncService } from "../subscription-sync/factory.ts";
import { ApiAuthenticationError } from "../api-auth/settings.ts";
import type { SubscriptionOperation, SubscriptionResult } from "./operations.ts";

/** 配備先の内部ACA接続を固定します。未設定時の直接実行はローカル開発だけに限定します。 */
export function subscriptionManagerOrigin(): string | null {
  const value = process.env.PORTAL_SUBSCRIPTION_MANAGER_URL;
  if (!value && process.env.NODE_ENV !== "production") return null;
  if (typeof value !== "string" || !/^https:\/\/ca-[a-z0-9-]+-sub-admin\.internal\.[a-z0-9-]+\.[a-z0-9-]+\.azurecontainerapps\.io$/.test(value)) {
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
  return value;
}

/** ログイン済みのSQLセッションから今回の本人トークンを取得し、固定された内部操作だけを送ります。 */
async function call<K extends SubscriptionOperation>(origin: string, actor: ApiKeyActor, operation: K, input: unknown,
  request?: Request | Headers): Promise<SubscriptionResult<K>> {
  let bearer: { value: string };
  try {
    const { readSubscriptionManagementBearer } = await import("../api-auth/subscription-management.ts");
    bearer = await readSubscriptionManagementBearer(request ?? new Headers(await (await import("next/headers")).headers()), actor);
  } catch (error) {
    if (error instanceof ApiAuthenticationError && error.status === 401) {
      throw new ApiKeyError("SubscriptionManagementAuthenticationRequired", 401);
    }
    if (error instanceof ApiAuthenticationError && error.status === 409) throw new ApiKeyError("ApiAuthenticationBusy", 409);
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
  let result: Response;
  try {
    result = await fetch(`${origin}/api/internal/subscriptions`, {
      method: "POST", headers: { Authorization: `Bearer ${bearer.value}`, "Content-Type": "application/json" },
      body: JSON.stringify({ operation, input }), redirect: "error", cache: "no-store",
      signal: AbortSignal.timeout(230_000),
    });
  } catch {
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
  const value = await result.json().catch(() => undefined);
  if (!result.ok) {
    const code = value?.error?.code;
    if (result.status === 401) throw new ApiKeyError("SubscriptionManagementAuthenticationRequired", 401);
    if (typeof code === "string" && /^[A-Za-z][A-Za-z0-9]{0,79}$/.test(code)
        && [400, 403, 404, 409, 429, 503].includes(result.status)) throw new ApiKeyError(code, result.status);
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
  if (value === undefined) throw new ApiKeyError("ConfigurationUnavailable", 503);
  return value as SubscriptionResult<K>;
}

/** 既存キー画面と試用が使う操作を、専用サービスへ渡します。 */
export function getPortalApiKeyService(request?: Request | Headers) {
  const origin = subscriptionManagerOrigin();
  if (!origin) return getApiKeyService();
  return {
    overview: (actor: ApiKeyActor) => call(origin, actor, "keyOverview", null, request),
    executionChoices: (actor: ApiKeyActor, apiId: string) => call(origin, actor, "keyChoices", { apiId }, request),
    executionCredential: (actor: ApiKeyActor, id: string, apiId: string) => call(origin, actor, "keyExecution", { id, apiId }, request),
    executionKey: async (actor: ApiKeyActor, id: string, apiId: string) =>
      (await call(origin, actor, "keyExecution", { id, apiId }, request)).key,
    create: (actor: ApiKeyActor, input: CreateApiKeyInput) => call(origin, actor, "keyCreate", input, request),
    act: (actor: ApiKeyActor, id: string, action: string) => call(origin, actor, "keyAction", { id, action }, request),
    ownerTargets: (actor: ApiKeyActor, id: string, search: string, cursor?: string) =>
      call(origin, actor, "ownerTargets", { id, search, cursor }, request),
    transfer: (actor: ApiKeyActor, id: string, values: TransferApiKeyOwnershipInput) =>
      call(origin, actor, "ownerTransfer", { id, values }, request),
  };
}

/** 利用枠の編集操作を独立した台帳更新権限で実行します。 */
export function getPortalApiKeyLimitService() {
  const origin = subscriptionManagerOrigin();
  if (!origin) return getApiKeyLimitService();
  return {
    get: (actor: ApiKeyActor, id: string) => call(origin, actor, "limitGet", { id }),
    update: (actor: ApiKeyActor, id: string, values: UpdateApiKeyLimits) => call(origin, actor, "limitUpdate", { id, values }),
  };
}

/** 管理者とグループ所属は専用サービス側でも独立して確認します。 */
export function getPortalApiKeyAdministrationService() {
  const origin = subscriptionManagerOrigin();
  if (!origin) return getApiKeyAdministrationService();
  return {
    isAdmin: (actor: ApiKeyActor) => call(origin, actor, "adminAccess", null),
    overview: (actor: ApiKeyActor, input: ApiKeyAdministrationPageRequest = {}) => call(origin, actor, "adminOverview", input),
    refresh: (actor: ApiKeyActor) => call(origin, actor, "adminRefresh", null),
  };
}

/** 会社の設定と参照は同じ台帳を使用し、更新だけを管理者に絞ります。 */
export function getPortalCompanyAdministrationService() {
  const origin = subscriptionManagerOrigin();
  if (!origin) return getCompanyAdministrationService();
  return {
    available: (actor: ApiKeyActor) => call(origin, actor, "companyAvailable", null),
    overview: (actor: ApiKeyActor) => call(origin, actor, "companyOverview", null),
    create: (actor: ApiKeyActor, input: CreateCompanyInput) => call(origin, actor, "companyCreate", input),
    update: (actor: ApiKeyActor, id: string, values: UpdateCompanyInput) => call(origin, actor, "companyUpdate", { id, values }),
  };
}

/** 管理者の同期プレビューと実行を、専用の署名鍵を持つサービスへ送ります。 */
export function getPortalSubscriptionSyncService() {
  const origin = subscriptionManagerOrigin();
  if (!origin) return getSubscriptionSyncService();
  return {
    overview: (actor: ApiKeyActor) => call(origin, actor, "syncOverview", null),
    preview: (actor: ApiKeyActor, ids: string[]) => call(origin, actor, "syncPreview", { action: "preview", ids }),
    apply: (actor: ApiKeyActor, token: string) => call(origin, actor, "syncApply", { action: "apply", confirmed: true, token }),
  };
}
