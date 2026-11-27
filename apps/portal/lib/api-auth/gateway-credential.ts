import "server-only";

import type { ApiKeyActor } from "../api-keys/contracts.ts";
import { getApiKeyService } from "../api-keys/service.ts";
import { ApiAuthenticationError } from "./settings.ts";
import { getPortalApiAuthentication, type PortalBearer } from "./service.ts";
import type { TryCredential } from "./try-credential.ts";

export { ApiAuthenticationError };

/** サーバー内の資格情報です。ヘッダーを組み立てた後も反射検知用の値を保持します。 */
export type GatewayCredential =
  | { scheme: "api-key"; value: string; headers: { "api-key": string }; bearer?: undefined }
  | { scheme: "bearer"; value: string; headers: { Authorization: string }; bearer: PortalBearer };

/** 公開サンプルのヘッダーから資格情報を除き、今回選択した方式を一つだけ設定します。 */
export function gatewayExecutionHeaders(example: HeadersInit, credential: GatewayCredential): Headers {
  const headers = new Headers(example);
  headers.delete("api-key");
  headers.delete("Authorization");
  for (const [name, value] of Object.entries(credential.headers)) headers.set(name, value);
  return headers;
}

/** 特定Gatewayへ送る資格情報を、同じ本人のSQLセッションまたはキー台帳から取得します。 */
export async function resolveGatewayCredential(request: Request, actor: ApiKeyActor, selection: TryCredential,
  apiId: string, origin: string,
  resolveSubscriptionKey = (owner: ApiKeyActor, id: string, api: string) => getApiKeyService().executionKey(owner, id, api),
  resolveBearer?: () => Promise<PortalBearer>): Promise<GatewayCredential> {
  if (selection.authentication !== "bearer") {
    const value = await resolveSubscriptionKey(actor, selection.apiKeyId, apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(value)) throw new Error("Invalid subscription key");
    return { scheme: "api-key", value, headers: { "api-key": value } };
  }
  let bearer: PortalBearer;
  try { bearer = resolveBearer ? await resolveBearer() : await readGatewayBearer(request, actor, origin); }
  catch (error) {
    if (error instanceof ApiAuthenticationError) throw error;
    throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
  }
  if (!/^[A-Za-z0-9._~+\/-]{16,16384}={0,2}$/.test(bearer.value) ||
      bearer.binding.objectId !== actor.objectId || bearer.binding.tenantId !== actor.tenantId) {
    throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
  }
  return { scheme: "bearer", value: bearer.value, headers: { Authorization: `Bearer ${bearer.value}` }, bearer };
}

/** Bearerを送る直前に、既存のHTTP認可と同じPortal sessionと固定宛先を確認します。 */
export async function readGatewayBearer(request: Request, actor: ApiKeyActor, origin: string): Promise<PortalBearer> {
  const service = getPortalApiAuthentication();
  if (origin !== service.settings.gatewayOrigin) throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
  const { requireApiAuthSession } = await import("./session.ts");
  const session = await requireApiAuthSession(request.headers);
  if (session.tenantId !== actor.tenantId || session.objectId !== actor.objectId) {
    throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
  }
  return service.bearer(session);
}

/** 長時間の操作では資格情報の期限と共有SQLの失効を監視します。 */
export function watchGatewayCredential(credential: GatewayCredential, lifetime: AbortSignal): AbortSignal {
  if (!credential.bearer) return lifetime;
  const bearer = credential.bearer;
  const ended = new AbortController();
  const signal = AbortSignal.any([lifetime, ended.signal, AbortSignal.timeout(Math.max(0, bearer.expiresAt - Date.now()))]);
  let timer: ReturnType<typeof setTimeout> | undefined;
  function stop() { clearTimeout(timer); }
  async function check() {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    try {
      const authorized = await Promise.race([
        getPortalApiAuthentication().stillAuthorized(bearer),
        new Promise<boolean>((resolve) => { timeout = setTimeout(() => resolve(false), 2_000); }),
      ]);
      if (!authorized) ended.abort();
    } catch { ended.abort(); }
    finally { clearTimeout(timeout); }
    if (!signal.aborted) timer = setTimeout(check, 5_000);
  }
  signal.addEventListener("abort", stop, { once: true });
  if (!signal.aborted) timer = setTimeout(check, 5_000);
  return signal;
}

/** 複数要求を含む操作では、後続要求の前にも本人と失効を確認します。 */
export async function assertGatewayCredential(credential: GatewayCredential): Promise<void> {
  if (credential.bearer && !await getPortalApiAuthentication().stillAuthorized(credential.bearer)) {
    throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
  }
}

/** APIの再接続や運用障害を呼出し元の固定応答に区別します。 */
export function apiAuthenticationFailure(error: unknown): { code: "ApiAuthenticationRequired" | "ApiAuthenticationBusy" | "ApiAuthenticationUnavailable"; status: number } | undefined {
  if (!(error instanceof ApiAuthenticationError)) return undefined;
  if (error.status === 401) return { code: "ApiAuthenticationRequired", status: 401 };
  if (error.status === 409) return { code: "ApiAuthenticationBusy", status: 409 };
  return { code: "ApiAuthenticationUnavailable", status: 503 };
}
