import "server-only";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getModelGatewayEnvironment, type ModelGatewayEnvironment } from "./model-gateway.ts";
import { readEmbeddingBody } from "./embedding-response.ts";
import { PII_API, buildPiiRequest, piiExecutionSchema, piiResponseSchema } from "./pii-contract.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** 本人確認と上流通信をテストで差し替えるための依存です。 */
export type PiiGatewayDependencies = {
  readSession: () => Promise<ApiKeyActor | null>; readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<ModelGatewayEnvironment>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, apiKeyId: string, apiId: string) => Promise<string>;
  fetch?: typeof globalThis.fetch; timeoutMs?: number;
};

/** エラー本文には固定コードと安全な要求IDのみを含めます。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details }, { status, headers: responseHeaders });
}

/** 本人が現在使用できる購読だけで、固定PII操作へ一度だけ本文を中継します。 */
export async function handlePiiRedaction(request: Request, dependencies: PiiGatewayDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || (site && site !== "same-origin")) return failure("RequestNotAllowed", 403);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  // 台帳ID分の余白を含めます。Gatewayへ渡す本文は別途正本のサイズ制限で検査します。
  const maxBffBytes = PII_API.maxRequestBytes + 1024;
  if (Number(request.headers.get("content-length") ?? 0) > maxBffBytes) return failure("InvalidRequest", 413);
  let input: ReturnType<typeof piiExecutionSchema.parse>, body: string;
  try {
    input = piiExecutionSchema.parse(JSON.parse(await readEmbeddingBody(request.body, maxBffBytes, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]))));
    body = JSON.stringify(buildPiiRequest(input));
  } catch { return failure(request.signal.aborted ? "RequestCancelled" : "InvalidRequest", request.signal.aborted ? 499 : 400); }
  let gateway: ModelGatewayEnvironment;
  try {
    gateway = await (dependencies.readGatewayEnvironment ?? getModelGatewayEnvironment)();
    const origin = new URL(gateway.origin);
    if (origin.protocol !== "https:" || origin.origin !== gateway.origin || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) throw new TypeError("Invalid gateway origin");
  } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, PII_API.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new TypeError("Invalid subscription key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = AbortSignal.timeout(dependencies.timeoutMs ?? 90_000), signal = AbortSignal.any([request.signal, timeout]);
  const started = performance.now();
  let identifier: string | undefined;
  try {
    const response = await (dependencies.fetch ?? globalThis.fetch)(new URL(PII_API.path, gateway.origin), { method: "POST", redirect: "error", cache: "no-store", signal, headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": key }, body });
    const id = response.headers.get("apim-request-id");
    if (id && /^[a-zA-Z0-9_-]{1,128}$/.test(id)) identifier = id;
    const details = identifier ? { requestId: identifier } : {};
    if (!response.ok) {
      await response.body?.cancel();
      if (response.status === 429) { const retry = Number(response.headers.get("retry-after")); return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(retry) && retry > 0 && retry <= 3600 ? { retryAfterSeconds: retry } : {}) }); }
      if ([400, 413, 422].includes(response.status)) return failure("InvalidRequest", response.status, details);
      if (response.status === 504) return failure("GatewayTimeout", 504, details);
      return failure([401, 403].includes(response.status) ? "GatewayUnauthorized" : "GenerationFailed", 502, details);
    }
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") { await response.body?.cancel(); return failure("GenerationFailed", 502, details); }
    const raw = await readEmbeddingBody(response.body, 131_072, signal), value: unknown = JSON.parse(raw);
    if (raw.includes(key) || JSON.stringify(value).includes(key)) throw new TypeError("Unexpected credential reflection");
    const result = piiResponseSchema.parse(value);
    if (result.language !== input.language) throw new TypeError("Unexpected language");
    return Response.json({ result, durationMs: Math.round(performance.now() - started) }, { headers: responseHeaders });
  } catch {
    const details = identifier ? { requestId: identifier } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("GatewayTimeout", 504, details);
    return failure("GenerationFailed", 502, details);
  }
}
