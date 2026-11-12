import { MAX_BUFFERED_BODY_BYTES, modelDeadline } from "./model-runtime-limits.ts";
import "server-only";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getEmbeddingModels } from "./model-catalog.ts";
import { getModelGatewayEnvironment, type ModelGatewayEnvironment } from "./model-gateway.ts";
import { buildEmbeddingRequest } from "./embedding-integration.ts";
import { embeddingExecutionSchema, type EmbeddingInput } from "./embedding-request.ts";
import { parseEmbeddingResult, readEmbeddingBody } from "./embedding-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { EmbeddingModel } from "./model-types.ts";
import { containsModelCredential } from "./model-credential.ts";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const paths = { embeddings: "/models/openai-embeddings/v1/embeddings", "cohere-embeddings": "/models/cohere/v1/embed" } as const;

/** 認証と通信を差し替えて、実際の入力制約・認可・応答制約を検証するための依存です。 */
export type EmbeddingGatewayDependencies = {
  readSession: () => Promise<ApiKeyActor | null>;
  readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<ModelGatewayEnvironment>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, apiKeyId: string, apiId: string) => Promise<string>;
  readModels?: () => readonly EmbeddingModel[] | Promise<readonly EmbeddingModel[]>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
};

/** 上流の任意の本文や例外詳細を含めず、翻訳可能な固定エラーだけを返します。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details } satisfies TextGenerationFailure, { status, headers });
}

/**
 * 認証済みの同一オリジンPOSTを、許可された埋め込み操作へ一度だけ転送します。
 * @remarks 台帳IDから毎回利用権限を確認し、購読キーはサーバー内だけで使います。自動再試行は行いません。
 */
export async function handleEmbeddings(request: Request, dependencies: EmbeddingGatewayDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || (site && site !== "same-origin")) return failure("RequestNotAllowed", 403);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  if (new URL(request.url).searchParams.size > 0) return failure("InvalidRequest", 400);
  if (Number(request.headers.get("content-length") ?? 0) > MAX_BUFFERED_BODY_BYTES) return failure("InvalidRequest", 413);
  let input: EmbeddingInput & { apiKeyId: string };
  try { input = embeddingExecutionSchema.parse(JSON.parse(await readEmbeddingBody(request.body, MAX_BUFFERED_BODY_BYTES, request.signal))); } catch { return failure(request.signal.aborted ? "RequestCancelled" : "InvalidRequest", request.signal.aborted ? 499 : 400); }
  let model: EmbeddingModel | undefined;
  try { model = (await (dependencies.readModels ?? getEmbeddingModels)()).find((item) => item.id === input.modelId); } catch { return failure("ModelUnavailable", 503); }
  if (!model) return failure("ModelUnavailable", 400);
  if (model.path !== paths[model.operationId] || (model.operationId === "embeddings" ? model.apiId !== "openai-embeddings-v1" || model.providerId !== "openai" : model.apiId !== "cohere-inference-v1" || model.providerId !== "cohere")) return failure("ModelUnavailable", 503);
  let body: string;
  try { body = JSON.stringify(buildEmbeddingRequest(model, input)); } catch { return failure("InvalidRequest", 400); }
  if (Buffer.byteLength(body) > model.maxRequestBytes) return failure("InvalidRequest", 413);
  let gateway: ModelGatewayEnvironment;
  try {
    gateway = await (dependencies.readGatewayEnvironment ?? getModelGatewayEnvironment)();
    const origin = new URL(gateway.origin);
    if (origin.protocol !== "https:" || origin.origin !== gateway.origin || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) throw new Error("Invalid gateway origin");
  } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, model.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid subscription key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = modelDeadline(dependencies.timeoutMs);
  const signal = AbortSignal.any([request.signal, timeout]);
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
    const raw = await readEmbeddingBody(response.body, MAX_BUFFERED_BODY_BYTES, signal);
    const value: unknown = JSON.parse(raw);
    if (containsModelCredential(value, key, raw)) throw new Error("Unexpected credential reflection");
    const result = parseEmbeddingResult(value, model, input, Math.round(performance.now() - started), identifier);
    return Response.json(result, { headers });
  } catch {
    const details = identifier ? { requestId: identifier } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("GatewayTimeout", 504, details);
    return failure("GenerationFailed", 502, details);
  }
}
