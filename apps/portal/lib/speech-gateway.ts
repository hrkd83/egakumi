import "server-only";

import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getSpeechModels } from "./model-catalog.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";
import { buildSpeechRequest, getSpeechEndpoint, getSpeechHeaders, validateSpeechModel } from "./speech-integration.ts";
import { MAX_SPEECH_AUDIO_BYTES, MAX_SPEECH_JSON_BYTES, MAX_SPEECH_UPLOAD_BYTES, speechExecutionSchema, type SpeechInput } from "./speech-request.ts";
import { inspectSpeechWav, parseSpeechAudio, parseSpeechTranscript, readSpeechBody } from "./speech-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { SpeechModel } from "./model-types.ts";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** 音声操作の外部依存です。テストで差し替えても、実際の入力・音声・権限の検査を通します。 */
export type SpeechDependencies = {
  readSession: () => Promise<ApiKeyActor | null>; readPortalOrigin: () => string;
  readModels?: () => readonly SpeechModel[] | Promise<readonly SpeechModel[]>;
  readGatewayEnvironment?: () => Promise<{ origin: string }>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, keyId: string, apiId: string) => Promise<string>;
  fetch?: typeof globalThis.fetch; timeoutMs?: number;
};

/** 上流の本文・例外・認証情報を含めない固定エラーです。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response { return Response.json({ error: { code }, ...details }, { status, headers: responseHeaders }); }

/**
 * 選択した購読の権限を確認し、入力文または検証済みのアップロード音声を1回だけAPIへ送ります。
 * @remarks 上流のTTSはWAV、Azure STTはWAV本文、OpenAI STTはfileだけのmultipartです。自動再試行はしません。
 */
export async function handleSpeech(request: Request, dependencies: SpeechDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  const incomingContentType = request.headers.get("content-type") ?? "";
  const multipart = incomingContentType.split(";", 1)[0].trim().toLowerCase() === "multipart/form-data";
  if (!multipart && incomingContentType.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  const maximumBody = multipart ? MAX_SPEECH_UPLOAD_BYTES + 32_768 : 65_536;
  if (Number(request.headers.get("content-length") ?? 0) > maximumBody) return failure("InvalidRequest", 413);
  let input: SpeechInput & { apiKeyId: string }; let uploadedAudio: Uint8Array | undefined;
  try {
    const bytes = await readSpeechBody(request.body, maximumBody, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]));
    if (multipart) {
      const form = await new Request("https://multipart.invalid", { method: "POST", headers: { "Content-Type": incomingContentType }, body: new Uint8Array(bytes).buffer }).formData();
      if ([...form.keys()].length !== 2 || form.getAll("input").length !== 1 || form.getAll("file").length !== 1) throw new Error("Invalid upload fields");
      const metadata = form.get("input"); const file = form.get("file");
      if (typeof metadata !== "string" || metadata.length > 16_384 || !(file instanceof File) || file.size < 46 || file.size > MAX_SPEECH_UPLOAD_BYTES) throw new Error("Invalid upload");
      input = speechExecutionSchema.parse(JSON.parse(metadata)); uploadedAudio = new Uint8Array(await file.arrayBuffer()); inspectSpeechWav(uploadedAudio);
    } else input = speechExecutionSchema.parse(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
  }
  catch { return request.signal.aborted ? failure("RequestCancelled", 499) : failure("InvalidRequest", 400); }
  let model: SpeechModel | undefined;
  try { model = (await (dependencies.readModels ?? getSpeechModels)()).find((entry) => entry.id === input.modelId); } catch { return failure("ModelUnavailable", 503); }
  if (!model) return failure("ModelUnavailable", 400);
  if (model.kind === "stt" ? !uploadedAudio : multipart) return failure("InvalidRequest", 400);
  let body: string | ArrayBuffer; let contentType: string | undefined;
  try {
    validateSpeechModel(model); const payload = buildSpeechRequest(model, input);
    if (model.kind === "tts") { body = payload as string; contentType = "application/ssml+xml"; }
    else {
      const audio = uploadedAudio!;
      if (model.apiId === "openai-audio-v1") {
        const form = new FormData(); form.append("file", new Blob([new Uint8Array(audio).buffer], { type: "audio/wav" }), "input.wav");
        // boundary込みの実際の送信サイズを検査し、multipartの上限を越えないようにします。
        const encoded = new Request("https://multipart.invalid", { method: "POST", body: form });
        contentType = encoded.headers.get("content-type") ?? undefined; body = await encoded.arrayBuffer();
      } else { contentType = "audio/wav"; body = new Uint8Array(audio).buffer; }
    }
    if ((typeof body === "string" ? Buffer.byteLength(body) : body.byteLength) > model.maxRequestBytes) return failure("InvalidRequest", 413);
  } catch { return failure("InvalidRequest", 400); }
  let endpoint: string;
  try { const environment = dependencies.readGatewayEnvironment ? await dependencies.readGatewayEnvironment() : { origin: getPublicModelGatewayOrigin() }; if (!environment.origin) throw new Error("Missing gateway origin"); endpoint = getSpeechEndpoint(model, environment.origin); }
  catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, model.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  const timeout = AbortSignal.timeout(dependencies.timeoutMs ?? 180_000); const signal = AbortSignal.any([request.signal, timeout]); const started = performance.now(); let requestId: string | undefined;
  try {
    const upstream = await (dependencies.fetch ?? globalThis.fetch)(endpoint, { method: "POST", redirect: "error", cache: "no-store", signal, body,
      headers: { ...getSpeechHeaders(model), ...(contentType ? { "Content-Type": contentType } : {}), "api-key": key } });
    const identifier = upstream.headers.get("apim-request-id"); if (identifier && /^[a-zA-Z0-9_-]{1,128}$/.test(identifier)) requestId = identifier;
    const details = requestId ? { requestId } : {};
    if (!upstream.ok) {
      void upstream.body?.cancel().catch(() => undefined);
      if (upstream.status === 429) { const seconds = Number(upstream.headers.get("retry-after")); return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(seconds) && seconds > 0 && seconds <= 3600 ? { retryAfterSeconds: seconds } : {}) }); }
      return failure([401, 403].includes(upstream.status) ? "GatewayUnauthorized" : "GenerationFailed", 502, details);
    }
    const mediaType = upstream.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
    const maximum = model.kind === "tts" ? MAX_SPEECH_AUDIO_BYTES : MAX_SPEECH_JSON_BYTES;
    if ((model.kind === "tts" ? !["audio/wav", "audio/x-wav"].includes(mediaType ?? "") : mediaType !== "application/json") || Number(upstream.headers.get("content-length") ?? 0) > maximum) { void upstream.body?.cancel().catch(() => undefined); return failure("GenerationFailed", 502, details); }
    const bytes = await readSpeechBody(upstream.body, maximum, signal);
    // WAVの付随チャンクやJSONの追加項目へ資格情報が反射されても、ブラウザーへ渡しません。
    if (Buffer.from(bytes).includes(Buffer.from(key))) return failure("GenerationFailed", 502, details);
    const durationMs = Math.round(performance.now() - started);
    const result = model.kind === "tts" ? parseSpeechAudio(bytes, Buffer.from(bytes).toString("base64"), model, durationMs, requestId)
      : parseSpeechTranscript(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), model, durationMs, requestId);
    return Response.json(result, { headers: responseHeaders });
  } catch { const details = requestId ? { requestId } : {}; return request.signal.aborted ? failure("RequestCancelled", 499, details) : timeout.aborted ? failure("GatewayTimeout", 504, details) : failure("GenerationFailed", 502, details); }
}
