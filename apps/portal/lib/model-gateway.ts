import { MAX_BUFFERED_BODY_BYTES, modelDeadline } from "./model-runtime-limits.ts";
import "server-only";

import { z } from "zod";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getTextGenerationModels } from "./model-catalog.ts";
import { buildTextGenerationRequest, getTextGenerationHeaders } from "./model-integration.ts";
import { textGenerationExecutionSchema, MODEL_MAX_UPSTREAM_JSON_BYTES } from "./model-request.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";
import { relayModelStream } from "./model-stream.ts";
import { containsModelCredential } from "./model-credential.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure, TextGenerationInput, TextGenerationResult } from "./model-request.ts";
import type { TextGenerationModel } from "./model-types.ts";

const maximumInputBytes = MAX_BUFFERED_BODY_BYTES;
const maximumResponseBytes = MODEL_MAX_UPSTREAM_JSON_BYTES;
const maximumErrorBytes = 8_192;
const apiPaths = {
  "chat-completions": "/models/openai/v1/chat/completions",
  "responses-create": "/models/openai/v1/responses",
} as const;
const responseHeaders = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

/** サーバー内部だけで利用する、検証済みのAPIM接続設定です。 */
export type ModelGatewayEnvironment = { origin: string };

/** HTTPハンドラーの外部依存です。検証では認証と通信だけを置き換え、実際の制約を通します。 */
export type ModelGenerationDependencies = {
  readSession: () => Promise<ApiKeyActor | null>;
  readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<ModelGatewayEnvironment>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, apiKeyId: string, apiId: string) => Promise<string>;
  readModels?: () => readonly TextGenerationModel[] | Promise<readonly TextGenerationModel[]>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
};

/**
 * ビルド時には要求せず、生成リクエスト時に固定のAPIM接続先だけを読みます。
 * @remarks 購読キーは利用者の選択から別途解決し、共通キーや開発用ファイルへ代替しません。
 */
export async function getModelGatewayEnvironment(environment: Readonly<Record<string, string | undefined>> = process.env): Promise<ModelGatewayEnvironment> {
  const origin = getPublicModelGatewayOrigin(environment);
  if (!origin) throw new Error("Model gateway requires an HTTPS origin");
  return { origin };
}

/** 本文をストリームで読み、Content-Lengthの有無にかかわらずバイト数上限を守ります。 */
async function readLimitedBody(body: ReadableStream<Uint8Array> | null, maximum: number, signal?: AbortSignal): Promise<string> {
  if (!body) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  let abortListener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    if (!signal) return;
    abortListener = () => reject(new Error("Body read aborted"));
    if (signal.aborted) abortListener();
    else signal.addEventListener("abort", abortListener, { once: true });
  });
  try {
    while (true) {
      const part = await Promise.race([reader.read(), aborted]);
      if (part.done) break;
      length += part.value.byteLength;
      if (length > maximum) {
        await reader.cancel();
        throw new Error("Body limit exceeded");
      }
      chunks.push(part.value);
    }
  } finally {
    if (abortListener) signal?.removeEventListener("abort", abortListener);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  const content = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder("utf-8", { fatal: true }).decode(content);
}

/** 固定の失敗種別だけをJSON化し、秘密情報を含む上流本文は転送しません。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details } satisfies TextGenerationFailure, { status, headers: responseHeaders });
}

/** 出力を完成できなかった既知の上限応答だけを識別し、上流の任意の説明文は公開しません。 */
function isOutputLimitFailure(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value) || !("error" in value)) return false;
  const error = value.error;
  if (!error || typeof error !== "object" || Array.isArray(error)) return false;
  return "type" in error && error.type === "invalid_request_error"
    && "message" in error && error.message === "Could not finish the message because max_tokens or model output limit was reached. Please try again with higher max_tokens."
    && (!("code" in error) || error.code === null)
    && (!("param" in error) || error.param === null);
}

/** ログの突合に使う識別子だけを、長さと文字種を検証して取り出します。 */
function requestId(headers: Headers): string | undefined {
  const value = headers.get("apim-request-id");
  return value && /^[a-zA-Z0-9_-]{1,128}$/.test(value) ? value : undefined;
}

/**
 * 実モデル名と公開API形式で実行先を一意に選びます。
 * @remarks 形式の省略時はOpenAIのResponsesを優先し、それがなければ利用できるChatを使います。
 * 指定された組合せが未対応、または同じ組合せが重複する場合は代替せず拒否します。
 */
function selectGenerationModel(models: readonly TextGenerationModel[], input: TextGenerationInput): TextGenerationModel | undefined {
  const candidates = models.filter((model) => model.id === input.modelId);
  const operation = input.operationId ?? (candidates.some((model) => model.providerId === "openai" && model.operationId === "responses-create")
    ? "responses-create" : candidates.some((model) => model.operationId === "chat-completions") ? "chat-completions" : "responses-create");
  const matches = candidates.filter((model) => model.operationId === operation);
  return matches.length === 1 ? matches[0] : undefined;
}

const tokenCount = z.number().int().nonnegative().safe();
const usageSchema = z.object({ inputTokens: tokenCount, outputTokens: tokenCount, totalTokens: tokenCount });
const chatResponseSchema = z.object({
  model: z.string().min(1).max(200),
  choices: z.array(z.object({
    message: z.object({ content: z.string().nullable(), refusal: z.string().nullable().optional() }),
    finish_reason: z.string().max(100).nullable().optional(),
  })).min(1),
  usage: z.object({ prompt_tokens: tokenCount, completion_tokens: tokenCount, total_tokens: tokenCount }).optional(),
});
const responsesSchema = z.object({
  model: z.string().min(1).max(200),
  status: z.enum(["completed", "incomplete"]),
  output: z.array(z.object({
    type: z.string(),
    content: z.array(z.object({ type: z.string(), text: z.string().optional(), refusal: z.string().optional() })).optional(),
  })),
  usage: z.object({ input_tokens: tokenCount, output_tokens: tokenCount, total_tokens: tokenCount }).optional(),
});

/** 正常なAPIM応答を検証し、実際のJSONを残したまま表示用の生成文と使用量を取り出します。 */
function generationResult(value: unknown, model: TextGenerationModel, durationMs: number, identifier?: string): TextGenerationResult {
  if (!value || typeof value !== "object" || Array.isArray(value) || ("error" in value && value.error !== null && value.error !== undefined)) throw new Error("Invalid generation response");
  const rawResponse = value as Record<string, unknown>;
  if (model.operationId === "responses-create") {
    const data = responsesSchema.parse(value);
    const text = data.output.filter((item) => item.type === "message")
      .flatMap((item) => item.content ?? [])
      .filter((item) => item.type === "output_text" || item.type === "refusal")
      .map((item) => item.text ?? item.refusal ?? "").join("\n");
    const usage = data.usage && usageSchema.parse({ inputTokens: data.usage.input_tokens, outputTokens: data.usage.output_tokens, totalTokens: data.usage.total_tokens });
    return { text, model: data.model, durationMs, rawResponse, ...(usage ? { usage } : {}), ...(identifier ? { requestId: identifier } : {}), ...(data.status ? { finishReason: data.status } : {}) };
  }
  const data = chatResponseSchema.parse(value);
  const choice = data.choices[0];
  const usage = data.usage && usageSchema.parse({ inputTokens: data.usage.prompt_tokens, outputTokens: data.usage.completion_tokens, totalTokens: data.usage.total_tokens });
  return {
    text: choice.message.content ?? choice.message.refusal ?? "", model: data.model, durationMs, rawResponse,
    ...(usage ? { usage } : {}), ...(identifier ? { requestId: identifier } : {}),
    ...(choice.finish_reason ? { finishReason: choice.finish_reason } : {}),
  };
}

/**
 * ログイン済みの同一オリジン要求だけを、登録済みモデルのAPIM操作へ転送します。
 * @remarks APIキーと入力本文はログやエラーに出しません。自動再試行は二重課金を避けるため行いません。
 */
export async function handleTextGeneration(request: Request, dependencies: ModelGenerationDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; }
  catch { return failure("GatewayNotConfigured", 503); }
  const fetchSite = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || (fetchSite && fetchSite !== "same-origin")) {
    return failure("RequestNotAllowed", 403);
  }
  let actor: ApiKeyActor | null;
  try {
    actor = await dependencies.readSession();
    if (!actor) return failure("AuthenticationRequired", 401);
  } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    return failure("InvalidRequest", 415);
  }
  if (Number(request.headers.get("content-length") ?? 0) > maximumInputBytes) return failure("InvalidRequest", 413);
  let input: TextGenerationInput & { apiKeyId: string };
  try { input = textGenerationExecutionSchema.parse(JSON.parse(await readLimitedBody(request.body, maximumInputBytes))); }
  catch { return failure("InvalidRequest", 400); }
  let model: TextGenerationModel | undefined;
  try { model = selectGenerationModel(await (dependencies.readModels ?? getTextGenerationModels)(), input); }
  catch { return failure("ModelUnavailable", 503); }
  if (!model) return failure("ModelUnavailable", 400);
  if (model.path !== apiPaths[model.operationId]) return failure("InvalidRequest", 400);
  let body: string;
  try { body = JSON.stringify(buildTextGenerationRequest(model, input)); } catch { return failure("InvalidRequest", 400); }
  if (Buffer.byteLength(body) > model.maxRequestBytes) return failure("InvalidRequest", 413);
  let gateway: ModelGatewayEnvironment;
  try { gateway = await (dependencies.readGatewayEnvironment ?? getModelGatewayEnvironment)(); }
  catch { return failure("GatewayNotConfigured", 503); }
  let subscriptionKey: string;
  try {
    const resolve = dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId));
    subscriptionKey = await resolve(actor, input.apiKeyId, model.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(subscriptionKey)) throw new Error("Invalid subscription key");
  } catch (error) {
    return error instanceof ApiKeyError && error.status < 500
      ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503);
  }
  const timeout = modelDeadline(dependencies.timeoutMs);
  const upstreamAbort = new AbortController();
  const signal = AbortSignal.any([timeout, request.signal, upstreamAbort.signal]);
  const started = performance.now();
  let identifier: string | undefined;
  try {
    const upstream = await (dependencies.fetch ?? globalThis.fetch)(new URL(model.path, gateway.origin), {
      method: "POST", redirect: "error", cache: "no-store", signal,
      headers: { ...getTextGenerationHeaders(model, input.stream), "api-key": subscriptionKey },
      body,
    });
    identifier = requestId(upstream.headers);
    const details = identifier ? { requestId: identifier } : {};
    if (!upstream.ok) {
      if (upstream.status === 400 && upstream.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() === "application/json") {
        const raw = await readLimitedBody(upstream.body, maximumErrorBytes, signal);
        const error: unknown = JSON.parse(raw);
        if (!containsModelCredential(error, subscriptionKey, raw) && isOutputLimitFailure(error)) {
          return failure("OutputLimitReached", 400, details);
        }
        return failure("GenerationFailed", 502, details);
      }
      await upstream.body?.cancel();
      if (upstream.status === 429) {
        const seconds = Number(upstream.headers.get("retry-after"));
        return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(seconds) && seconds > 0 && seconds <= 3600 ? { retryAfterSeconds: seconds } : {}) });
      }
      if ([401, 403].includes(upstream.status)) return failure("GatewayUnauthorized", 502, details);
      return failure("GenerationFailed", 502, details);
    }
    if (input.stream) {
      if (!upstream.body || upstream.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "text/event-stream") {
        await upstream.body?.cancel();
        return failure("GenerationFailed", 502, details);
      }
      return new Response(relayModelStream({
        body: upstream.body, operation: model.operationId, signal, timeoutSignal: timeout,
        requestSignal: request.signal, abortUpstream: () => upstreamAbort.abort(), requestId: identifier, credential: subscriptionKey,
      }), { headers: {
        ...responseHeaders, "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store, no-transform",
        ...(identifier ? { "apim-request-id": identifier } : {}),
      } });
    }
    if (upstream.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
      await upstream.body?.cancel();
      return failure("GenerationFailed", 502, details);
    }
    if (Number(upstream.headers.get("content-length") ?? 0) > maximumResponseBytes) { await upstream.body?.cancel(); return failure("GenerationFailed", 502, details); }
    const raw = await readLimitedBody(upstream.body, maximumResponseBytes, signal);
    const data: unknown = JSON.parse(raw);
    if (containsModelCredential(data, subscriptionKey, raw)) throw new Error("Unexpected credential reflection");
    const result = generationResult(data, model, Math.round(performance.now() - started), identifier);
    return Response.json(result, { headers: responseHeaders });
  } catch {
    const details = identifier ? { requestId: identifier } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("GatewayTimeout", 504, details);
    return failure("GenerationFailed", 502, details);
  }
}
