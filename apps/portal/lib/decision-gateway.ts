import "server-only";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getDecisionModels } from "./model-catalog.ts";
import { getModelGatewayEnvironment, type ModelGatewayEnvironment } from "./model-gateway.ts";
import { readEmbeddingBody } from "./embedding-response.ts";
import { buildDecisionRequest } from "./decision-integration.ts";
import { decisionExecutionSchema, parseDecisionResult } from "./decision-request.ts";
import type { DecisionModel } from "./decision-types.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const path = "/models/microsoft-decision/v1/systemone";

/** 認証・モデル・通信を境界で置き換え、実際の検証処理を試験するための依存です。 */
export type DecisionGatewayDependencies = {
  readSession: () => Promise<ApiKeyActor | null>;
  readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<ModelGatewayEnvironment>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, id: string, apiId: string) => Promise<string>;
  readModels?: () => readonly DecisionModel[] | Promise<readonly DecisionModel[]>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
};

/** エラー本文や接続先、秘密値を出さず、固定コードと相関IDだけを返します。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details } satisfies TextGenerationFailure, { status, headers: responseHeaders });
}

/**
 * ログイン本人が現在利用できる購読を解決し、意思決定APIへ一度だけ送信します。
 * @remarks 利用者が任意のURL・ヘッダーを渡す経路はなく、キャンセルを上流へ伝えます。自動再試行は行いません。
 */
export async function handleDecision(request: Request, dependencies: DecisionGatewayDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  if (Number(request.headers.get("content-length") ?? 0) > 16_384) return failure("InvalidRequest", 413);
  let input;
  try { input = decisionExecutionSchema.parse(JSON.parse(await readEmbeddingBody(request.body, 16_384, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)])))); }
  catch { return failure(request.signal.aborted ? "RequestCancelled" : "InvalidRequest", request.signal.aborted ? 499 : 400); }
  let model: DecisionModel | undefined;
  try { model = (await (dependencies.readModels ?? getDecisionModels)()).find((item) => item.id === input.modelId); } catch { return failure("ModelUnavailable", 503); }
  if (!model) return failure("ModelUnavailable", 400);
  if (model.apiId !== "microsoft-decision-v1" || model.operationId !== "systemone" || model.path !== path || model.providerId !== "microsoft") return failure("ModelUnavailable", 503);
  let body: string;
  try { body = JSON.stringify(buildDecisionRequest(model, { modelId: input.modelId, state: input.state, questions: input.questions })); } catch (error) { return failure("InvalidRequest", error instanceof RangeError ? 413 : 400); }
  let gateway: ModelGatewayEnvironment;
  try {
    gateway = await (dependencies.readGatewayEnvironment ?? getModelGatewayEnvironment)();
    const origin = new URL(gateway.origin);
    if (origin.protocol !== "https:" || origin.origin !== gateway.origin || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) throw new Error("Invalid origin");
  } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, model.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = AbortSignal.timeout(dependencies.timeoutMs ?? 90_000);
  const signal = AbortSignal.any([request.signal, timeout]);
  const started = performance.now();
  let requestId: string | undefined;
  try {
    const response = await (dependencies.fetch ?? globalThis.fetch)(new URL(model.path, gateway.origin), {
      method: "POST", redirect: "error", cache: "no-store", signal,
      headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": key }, body,
    });
    const id = response.headers.get("apim-request-id");
    if (id && /^[a-zA-Z0-9_-]{1,128}$/.test(id)) requestId = id;
    const details = requestId ? { requestId } : {};
    if (!response.ok) {
      await response.body?.cancel();
      if (response.status === 429) { const retry = Number(response.headers.get("retry-after")); return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(retry) && retry > 0 && retry <= 3600 ? { retryAfterSeconds: retry } : {}) }); }
      return failure([401, 403].includes(response.status) ? "GatewayUnauthorized" : "GenerationFailed", 502, details);
    }
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") { await response.body?.cancel(); return failure("GenerationFailed", 502, details); }
    const raw = await readEmbeddingBody(response.body, 1_048_576, signal);
    const value = JSON.parse(raw);
    if (raw.includes(key) || JSON.stringify(value).includes(key)) throw new Error("Credential reflection");
    const result = parseDecisionResult(value, model, input, Math.round(performance.now() - started), requestId);
    return Response.json(result, { headers: responseHeaders });
  } catch {
    const details = requestId ? { requestId } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("GatewayTimeout", 504, details);
    return failure("GenerationFailed", 502, details);
  }
}
