import { MAX_BUFFERED_BODY_BYTES, modelDeadline } from "./model-runtime-limits.ts";
import "server-only";

import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getImageGenerationModels } from "./model-catalog.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";
import { buildImageGenerationRequest, getImageGenerationHeaders } from "./image-integration.ts";
import { imageGenerationExecutionSchema, MAX_IMAGE_RESPONSE_BYTES, type ImageGenerationInput } from "./image-request.ts";
import { parseImageGenerationResult } from "./image-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { ImageGenerationModel } from "./model-types.ts";
import { containsModelCredential } from "./model-credential.ts";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const paths = { "image-generate": "/models/openai-images/v1/generations", "flux2-generate": "/models/blackforestlabs/v1/flux-2/generations" };
/** 画像処理の外部依存です。認証・認可を差し替えるテストでも入力と応答の検証は通します。 */
export type ImageGenerationDependencies = {
  readSession: () => Promise<ApiKeyActor | null>; readPortalOrigin: () => string;
  readModels?: () => readonly ImageGenerationModel[] | Promise<readonly ImageGenerationModel[]>;
  readGatewayEnvironment?: () => Promise<{ origin: string }>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, keyId: string, apiId: string) => Promise<string>;
  fetch?: typeof globalThis.fetch; timeoutMs?: number;
};

/** 秘密値や上流本文を公開しない固定のエラー応答です。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response { return Response.json({ error: { code }, ...details }, { status, headers }); }

/** 長さヘッダーがなくても、入力と画像の実バイト数を制限し、期限到達で読取りを止めます。 */
async function readBody(body: ReadableStream<Uint8Array> | null, maximum: number, signal: AbortSignal): Promise<string> {
  if (!body) throw new Error("Body required");
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  let listener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => { listener = () => reject(new Error("Read aborted")); if (signal.aborted) listener(); else signal.addEventListener("abort", listener, { once: true }); });
  try {
    while (true) { const part = await Promise.race([reader.read(), aborted]); if (part.done) break; length += part.value.byteLength; if (length > maximum) throw new Error("Body limit"); chunks.push(part.value); }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } finally { if (listener) signal.removeEventListener("abort", listener); void reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

/**
 * 本人と現在のグループ権限を確認し、選択したキーでAPIMの固定画像生成操作を1回だけ呼びます。
 * @remarks 同期画像生成の待機は利用者の中止と通信経路に従い、自動再試行による二重課金を避けます。
 */
export async function handleImageGeneration(request: Request, dependencies: ImageGenerationDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  // WAFの引数値除外は同名queryにも作用するため、本文以外の入力を受け付けません。
  if (new URL(request.url).searchParams.size > 0) return failure("InvalidRequest", 400);
  if (Number(request.headers.get("content-length") ?? 0) > MAX_BUFFERED_BODY_BYTES) return failure("InvalidRequest", 413);
  let input: ImageGenerationInput & { apiKeyId: string };
  try { input = imageGenerationExecutionSchema.parse(JSON.parse(await readBody(request.body, MAX_BUFFERED_BODY_BYTES, request.signal))); } catch { return failure("InvalidRequest", 400); }
  let model: ImageGenerationModel | undefined;
  try { model = (await (dependencies.readModels ?? getImageGenerationModels)()).find((entry) => entry.id === input.modelId); } catch { return failure("ModelUnavailable", 503); }
  if (!model) return failure("ModelUnavailable", 400);
  if (model.path !== paths[model.operationId] || model.operationId === "image-generate" && model.apiId !== "openai-images-v1" || model.operationId === "flux2-generate" && model.apiId !== "blackforest-images-v1") return failure("InvalidRequest", 400);
  let body: string;
  try { body = JSON.stringify(buildImageGenerationRequest(model, input)); if (Buffer.byteLength(body) > model.maxRequestBytes) return failure("InvalidRequest", 413); } catch { return failure("InvalidRequest", 400); }
  let origin: string;
  try {
    const environment = dependencies.readGatewayEnvironment ? await dependencies.readGatewayEnvironment() : { origin: getPublicModelGatewayOrigin() };
    const url = new URL(environment.origin ?? "");
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error("Invalid origin");
    origin = url.origin;
  } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, model.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = modelDeadline(dependencies.timeoutMs);
  const signal = AbortSignal.any([request.signal, timeout]);
  const started = performance.now();
  let requestId: string | undefined;
  try {
    const upstream = await (dependencies.fetch ?? globalThis.fetch)(new URL(model.path, origin), { method: "POST", redirect: "error", cache: "no-store", signal, body, headers: { ...getImageGenerationHeaders(model), "api-key": key } });
    const identifier = upstream.headers.get("apim-request-id");
    if (identifier && /^[a-zA-Z0-9_-]{1,128}$/.test(identifier)) requestId = identifier;
    const details = requestId ? { requestId } : {};
    if (!upstream.ok) {
      void upstream.body?.cancel().catch(() => undefined);
      if (upstream.status === 429) { const seconds = Number(upstream.headers.get("retry-after")); return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(seconds) && seconds > 0 && seconds <= 3600 ? { retryAfterSeconds: seconds } : {}) }); }
      return failure([401, 403].includes(upstream.status) ? "GatewayUnauthorized" : "GenerationFailed", 502, details);
    }
    if (upstream.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json" || Number(upstream.headers.get("content-length") ?? 0) > MAX_IMAGE_RESPONSE_BYTES) { void upstream.body?.cancel().catch(() => undefined); return failure("GenerationFailed", 502, details); }
    const raw = await readBody(upstream.body, MAX_IMAGE_RESPONSE_BYTES, signal);
    const value: unknown = JSON.parse(raw);
    // 想定外の上流がキーを正常応答へ反射しても、ブラウザーへの転送を止めます。
    if (containsModelCredential(value, key, raw)) return failure("GenerationFailed", 502, details);
    return Response.json(parseImageGenerationResult(value, model.id, Math.round(performance.now() - started), requestId), { headers });
  } catch {
    const details = requestId ? { requestId } : {};
    return request.signal.aborted ? failure("RequestCancelled", 499, details) : timeout.aborted ? failure("GatewayTimeout", 504, details) : failure("GenerationFailed", 502, details);
  }
}
