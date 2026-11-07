import "server-only";
import { containsModelCredential } from "./model-credential.ts";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getRerankModels } from "./model-catalog.ts";
import { getModelGatewayEnvironment, type ModelGatewayEnvironment } from "./model-gateway.ts";
import { readEmbeddingBody } from "./embedding-response.ts";
import { buildRerankRequest } from "./rerank-integration.ts";
import { rerankExecutionSchema, type RerankInput } from "./rerank-request.ts";
import { parseRerankResult } from "./rerank-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { RerankModel } from "./rerank-types.ts";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** 上流通信と本人確認を差し替えて、BFFの境界を検証する依存です。 */
export type RerankGatewayDependencies = {
  readSession: () => Promise<ApiKeyActor | null>;
  readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<ModelGatewayEnvironment>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, apiKeyId: string, apiId: string) => Promise<string>;
  readModels?: () => readonly RerankModel[] | Promise<readonly RerankModel[]>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
};

/** 秘密や上流の自由文を含まない、翻訳可能な固定エラーを返します。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details } satisfies TextGenerationFailure, { status, headers: responseHeaders });
}

/**
 * 本人が利用できる購読キーだけを解決し、固定されたリランクAPIへ一度だけ送信します。
 * @remarks 失敗時に代替キー・別モデルへ切り替えず、ブラウザーへ秘密値を返しません。
 */
export async function handleRerank(request: Request, dependencies: RerankGatewayDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || (site && site !== "same-origin")) return failure("RequestNotAllowed", 403);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  // 台帳IDなどBFF用のフィールド分を含めた受信上限です。転送本文は別途API上限で検査します。
  if (Number(request.headers.get("content-length") ?? 0) > 1_048_576) return failure("InvalidRequest", 413);
  let input: RerankInput & { apiKeyId: string };
  try { input = rerankExecutionSchema.parse(JSON.parse(await readEmbeddingBody(request.body, 1_048_576, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)])))); } catch { return failure(request.signal.aborted ? "RequestCancelled" : "InvalidRequest", request.signal.aborted ? 499 : 400); }
  let model: RerankModel | undefined;
  try { model = (await (dependencies.readModels ?? getRerankModels)()).find((entry) => entry.id === input.modelId); } catch { return failure("ModelUnavailable", 503); }
  if (!model) return failure("ModelUnavailable", 400);
  if (model.apiId !== "cohere-inference-v1" || model.operationId !== "rerank" || model.providerId !== "cohere" || model.path !== "/models/cohere/v1/rerank") return failure("ModelUnavailable", 503);
  let body: string;
  try { body = JSON.stringify(buildRerankRequest(model, input)); } catch { return failure("InvalidRequest", 400); }
  if (Buffer.byteLength(body) > model.maxRequestBytes) return failure("InvalidRequest", 413);
  let gateway: ModelGatewayEnvironment;
  try {
    gateway = await (dependencies.readGatewayEnvironment ?? getModelGatewayEnvironment)();
    const origin = new URL(gateway.origin);
    if (origin.protocol !== "https:" || origin.origin !== gateway.origin || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) throw new TypeError("Invalid gateway origin");
  } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, model.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new TypeError("Invalid subscription key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = AbortSignal.timeout(dependencies.timeoutMs ?? 90_000), signal = AbortSignal.any([request.signal, timeout]);
  const started = performance.now();
  let identifier: string | undefined;
  try {
    const response = await (dependencies.fetch ?? globalThis.fetch)(new URL(model.path, gateway.origin), { method: "POST", redirect: "error", cache: "no-store", signal, headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": key }, body });
    const id = response.headers.get("apim-request-id");
    if (id && /^[a-zA-Z0-9_-]{1,128}$/.test(id)) identifier = id;
    const details = identifier ? { requestId: identifier } : {};
    if (!response.ok) {
      await response.body?.cancel();
      if (response.status === 429) { const retry = Number(response.headers.get("retry-after")); return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(retry) && retry > 0 && retry <= 3_600 ? { retryAfterSeconds: retry } : {}) }); }
      return failure([401, 403].includes(response.status) ? "GatewayUnauthorized" : "GenerationFailed", 502, details);
    }
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") { await response.body?.cancel(); return failure("GenerationFailed", 502, details); }
    const raw = await readEmbeddingBody(response.body, 1_048_576, signal);
    const value: unknown = JSON.parse(raw);
    if (containsModelCredential(value, key, raw)) throw new TypeError("Unexpected credential reflection");
    return Response.json(parseRerankResult(value, model, input, Math.round(performance.now() - started), identifier), { headers: responseHeaders });
  } catch {
    const details = identifier ? { requestId: identifier } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("GatewayTimeout", 504, details);
    return failure("GenerationFailed", 502, details);
  }
}
