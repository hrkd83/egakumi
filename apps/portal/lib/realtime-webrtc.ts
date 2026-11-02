import "server-only";

import WebSocket, { type RawData } from "ws";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getAuthEnvironment } from "./auth-environment.ts";
import { getRealtimeModels } from "./model-catalog.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";
import { getTextGenerationEndpoint } from "./model-integration.ts";
import type { RealtimeModel } from "./model-types.ts";
import { openRealtimeCallHandle, sealRealtimeCallHandle, type RealtimeCallHandle } from "./realtime-call-handle.ts";
import { REALTIME_MAX_DURATION_MS, REALTIME_MAX_EVENTS, REALTIME_MAX_STREAM_BYTES, realtimeStopSchema, realtimeStreamEventSchema, realtimeWebRtcExecutionSchema, resolveRealtimeOutputTokens, type RealtimeInput, type RealtimeStreamEvent } from "./realtime-request.ts";
import { readSpeechBody } from "./speech-response.ts";
import { REALTIME_CONVERSATION_INSTRUCTIONS as conversationInstructions } from "./realtime-protocol.ts";

const privateHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const eventNames = new Set(["session.created", "session.updated", "rtc.call.sdp.created", "response.created", "response.done", "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped", "input_audio_buffer.committed", "conversation.item.created", "conversation.item.input_audio_transcription.completed", "response.output_audio_transcript.done", "response.audio_transcript.done", "error", "rtc.call.error"]);
const usageNames = new Set(["input_tokens", "output_tokens", "total_tokens", "input_audio_tokens", "output_audio_tokens"]);

/** 固定の上流WSSだけへ接続する、テストで差し替え可能な制御チャネルです。 */
export type RealtimeControlSocket = { send: (value: unknown) => Promise<void>; receive: (signal: AbortSignal) => Promise<unknown>; close: () => Promise<void> };
/** WebRTC開始・停止の依存です。本人確認・購読権限・本文検査を保ち、上流通信だけを差し替えられます。 */
export type RealtimeWebRtcDependencies = {
  readSession: () => Promise<ApiKeyActor | null>; readPortalOrigin: () => string;
  readModels?: () => readonly RealtimeModel[] | Promise<readonly RealtimeModel[]>;
  readGatewayEnvironment?: () => Promise<{ origin: string }>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, keyId: string, apiId: string) => Promise<string>;
  readHandleSecret?: () => string; fetch?: typeof globalThis.fetch;
  connectWebSocket?: (url: string, headers: Record<string, string>, signal: AbortSignal) => Promise<RealtimeControlSocket>;
  timeoutMs?: number;
};

/** 失敗理由を固定コードへ絞り、上流の本文や認証情報を公開しません。 */
function failure(code: string, status: number): Response { return Response.json({ error: { code } }, { status, headers: privateHeaders }); }

/** ブラウザーのCookie本人確認と、状態変更のOrigin照合を共通化します。 */
async function authorize(request: Request, dependencies: RealtimeWebRtcDependencies): Promise<ApiKeyActor | Response> {
  let origin: string;
  try { origin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== origin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  try { const actor = await dependencies.readSession(); return actor ?? failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
}

/** 登録済みの音声専用SDPだけを受け付け、サイズ・NULL・映像trackを検査します。 */
export function validateRealtimeSdp(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("v=0\r\n") || Buffer.byteLength(value) > 65_536 || value.includes("\0")
    || !/(?:\r\n)m=audio \d+ [^\r\n]+/.test(value) || /(?:\r\n)m=video /.test(value)) throw new Error("Invalid audio session description");
  return value;
}

/** 定義と異なるパス・モデル・チケット契約を、キーの取得より先に拒否します。 */
function validateModel(model: RealtimeModel): void {
  if (model.transport !== "webrtc") throw new Error("A WebRTC operation is required");
  const expected = model.family === "realtime" ? ["openai-realtime-control-v1", "/models/openai-realtime/v1/calls", "gpt-realtime-2.1"]
    : model.family === "live" ? ["openai-live-control-v1", "/models/openai-live/v1/sessions", "gpt-live-1"]
      : model.family === "voice-live" ? ["azure-voice-live-webrtc-v1", "/models/voice-live/v1/webrtc/", "gpt-4.1"] : null;
  if (!expected || model.apiId !== expected[0] || model.path !== expected[1] || model.modelId !== expected[2]) throw new Error("Unregistered WebRTC operation");
  if (model.family === "realtime" && (model.observer?.apiId !== "openai-realtime-observer-v1" || model.observer.path !== "/models/openai-realtime/v1/observer/"
    || model.hangupPath !== "/models/openai-realtime/v1/calls/{callId}/hangup")) throw new Error("Incomplete call ownership contract");
  if (model.family === "live" && model.voice !== "marin" || model.family === "voice-live" && model.voice !== "ja-JP-NanamiNeural") throw new Error("Unregistered voice");
}

/** WSS接続先は登録済みHTTPSオリジンと公開パスだけで作ります。queryへ秘密は入れません。 */
function websocketEndpoint(path: string, origin: string): string { const url = new URL(getTextGenerationEndpoint({ path }, origin)); url.protocol = "wss:"; return url.href; }

/** Voice Liveの制御イベントを公式契約で組み立て、通常の音声WSS用イベントと混同させません。 */
function voiceLiveOffer(model: RealtimeModel, sdp: string, input: RealtimeInput): Record<string, unknown> {
  return { type: "rtc.call.sdp.create", sdp_offer: sdp, session: {
    modalities: ["text", "audio"], instructions: conversationInstructions, voice: { type: "azure-standard", name: model.voice },
    turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 500 }, max_response_output_tokens: resolveRealtimeOutputTokens(model, input), tools: [],
  } };
}

/** TLS・転送禁止・フレーム上限を固定し、サーバーだけでキーを付けてWSSを開きます。 */
async function openControlSocket(url: string, headers: Record<string, string>, signal: AbortSignal): Promise<RealtimeControlSocket> {
  // 公開APIMはSec-WebSocket-Protocolを返しません。必須でないsubprotocolを要求するとwsが正しい101接続を拒否します。
  const socket = new WebSocket(url, { headers, followRedirects: false, handshakeTimeout: 15_000, maxPayload: 1_048_576, perMessageDeflate: false });
  const queue: unknown[] = []; let ended = false; let fault = false; let wake: (() => void) | undefined;
  socket.on("message", (data: RawData, binary: boolean) => {
    try { if (binary || queue.length >= 32) throw new Error("Invalid control frame"); queue.push(JSON.parse(data.toString())); }
    catch { fault = true; socket.terminate(); }
    wake?.();
  });
  socket.on("close", () => { ended = true; wake?.(); }); socket.on("error", () => { fault = true; wake?.(); });
  await new Promise<void>((resolve, reject) => {
    const abort = () => { socket.terminate(); finish(new Error("Control connection aborted")); };
    const finish = (error?: Error) => { signal.removeEventListener("abort", abort); socket.off("open", opened); socket.off("error", failed); if (error) reject(error); else resolve(); };
    const opened = () => finish();
    const failed = (error: Error) => {
      console.warn("Realtime control handshake failed", { reason: error.message === "Server sent no subprotocol" ? "subprotocol-not-returned" : error.message === "Server sent a subprotocol but none was requested" ? "unexpected-subprotocol" : "handshake-rejected" });
      finish(new Error("Control connection failed"));
    };
    socket.once("open", opened); socket.once("error", failed); signal.addEventListener("abort", abort, { once: true }); if (signal.aborted) abort();
  });
  return {
    send: async (value) => { if (signal.aborted || socket.readyState !== WebSocket.OPEN) throw new Error("Control connection is closed"); await new Promise<void>((resolve, reject) => socket.send(JSON.stringify(value), (error) => error ? reject(new Error("Control write failed")) : resolve())); },
    receive: async (receiveSignal) => {
      while (!queue.length) {
        if (fault || ended || receiveSignal.aborted) throw new Error("Control connection ended");
        await new Promise<void>((resolve) => { const ready = () => { receiveSignal.removeEventListener("abort", ready); wake = undefined; resolve(); }; wake = ready; receiveSignal.addEventListener("abort", ready, { once: true }); if (receiveSignal.aborted || ended || fault) ready(); });
      }
      if (receiveSignal.aborted || fault) throw new Error("Control connection ended"); return queue.shift();
    },
    close: async () => { if (socket.readyState === WebSocket.CLOSED) return; await new Promise<void>((resolve) => { const timer = setTimeout(() => { socket.terminate(); resolve(); }, 1000); socket.once("close", () => { clearTimeout(timer); resolve(); }); socket.close(1000); }); },
  };
}

/** 呼び出しごとに所属・製品・有効状態を確認し、キー実値をサーバー内だけで使用します。 */
async function resolveKey(dependencies: RealtimeWebRtcDependencies, actor: ApiKeyActor, keyId: string, apiId: string): Promise<string> {
  const key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, api) => getApiKeyService().executionKey(owner, id, api)))(actor, keyId, apiId);
  if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid subscription credential"); return key;
}

/** 上流の所有者情報を、期限より長く使える復旧参照にしないための検査です。 */
function captureCall(response: Response, actor: ApiKeyActor, apiKeyId: string, modelId: string): RealtimeCallHandle | undefined {
  const callId = response.headers.get("X-Core-Call-Id") ?? ""; const ticket = response.headers.get("X-Core-Call-Ticket") ?? "";
  if (!/^[A-Za-z0-9_-]{1,160}$/.test(callId) || !/^[A-Za-z0-9_.-]{1,4096}$/.test(ticket)) return;
  try {
    const payload = JSON.parse(Buffer.from(ticket.split(".")[0], "base64url").toString("utf8")); const now = Date.now();
    // APIMとアプリの時計差を許容しても、暗号化参照自体はローカルの590秒以内に制限します。
    // 実際の所有者・期限は、observer／hangupを受けるAPIMが署名とともに検証します。
    if (payload.v !== 3 || payload.callId !== callId || !Number.isInteger(payload.exp) || payload.exp * 1000 <= now || payload.exp * 1000 > now + 660_000) return;
    return { version: 1, tenantId: actor.tenantId, objectId: actor.objectId, apiKeyId, modelId, callId, ticket, expiresAt: Math.min(payload.exp * 1000, now + 590_000) };
  } catch { return; }
}

/** 利用者のURLやLocationを追跡せず、固定登録されたhangupへ一度だけ送ります。 */
async function stopCall(call: RealtimeCallHandle, model: RealtimeModel, origin: string, key: string, fetcher: typeof globalThis.fetch): Promise<boolean> {
  try {
    validateModel(model); if (model.family !== "realtime" || !model.hangupPath || call.expiresAt <= Date.now()) return false;
    const endpoint = getTextGenerationEndpoint({ path: model.hangupPath.replace("{callId}", encodeURIComponent(call.callId)) }, origin);
    const response = await fetcher(endpoint, { method: "POST", headers: { "Content-Type": "application/json", "api-key": key, "X-Core-Call-Ticket": call.ticket }, body: "{}", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(12_000) });
    void response.body?.cancel().catch(() => undefined); return response.status === 200 || response.status === 204;
  } catch { return false; }
}

/** 本人に束縛された停止参照を復号し、現在も使える同じ購読で通話の停止を再確認します。 */
export async function handleRealtimeStop(request: Request, dependencies: RealtimeWebRtcDependencies): Promise<Response> {
  const actor = await authorize(request, dependencies); if (actor instanceof Response) return actor;
  if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") return failure("InvalidRequest", 415);
  let call: RealtimeCallHandle; let model: RealtimeModel; let origin: string;
  try {
    const input = realtimeStopSchema.parse(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readSpeechBody(request.body, 17_000, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)])))));
    call = openRealtimeCallHandle(input.handle, (dependencies.readHandleSecret ?? (() => getAuthEnvironment().secret))(), actor);
    const selected = (await (dependencies.readModels ?? getRealtimeModels)()).find((entry) => entry.id === call.modelId); if (!selected) throw new Error("Missing model"); model = selected; validateModel(model);
    origin = (dependencies.readGatewayEnvironment ? await dependencies.readGatewayEnvironment() : { origin: getPublicModelGatewayOrigin() ?? "" }).origin;
    getTextGenerationEndpoint(model, origin);
  } catch { return failure("InvalidRequest", 400); }
  let key: string; try { key = await resolveKey(dependencies, actor, call.apiKeyId, model.apiId); } catch (error) { return failure(error instanceof ApiKeyError && error.status < 500 ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable", error instanceof ApiKeyError && error.status < 500 ? 403 : 503); }
  const confirmed = await stopCall(call, model, origin, key, dependencies.fetch ?? globalThis.fetch);
  return Response.json({ stopStatus: confirmed ? "confirmed" : "unconfirmed" }, { status: confirmed ? 200 : 502, headers: privateHeaders });
}

/**
 * WebRTCの制御をGatewayへ中継し、内部の双方向リレーへイベントをストリームで返します。
 * @remarks 音声trackはブラウザーとサービス間のICE経路です。このHTTP通信を音声の中継や最終課金量の証明にはしません。
 */
export async function handleRealtimeWebRtc(request: Request, dependencies: RealtimeWebRtcDependencies): Promise<Response> {
  const actor = await authorize(request, dependencies); if (actor instanceof Response) return actor;
  if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") return failure("InvalidRequest", 415);
  let input: RealtimeInput & { apiKeyId: string; sdp: string }; let model: RealtimeModel; let origin: string;
  try {
    if (Number(request.headers.get("content-length") ?? 0) > 135_000) return failure("InvalidRequest", 413);
    input = realtimeWebRtcExecutionSchema.parse(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readSpeechBody(request.body, 135_000, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]))))); validateRealtimeSdp(input.sdp);
    const selected = (await (dependencies.readModels ?? getRealtimeModels)()).find((entry) => entry.id === input.modelId); if (!selected) throw new Error("Missing model"); model = selected; validateModel(model);
    resolveRealtimeOutputTokens(model, input);
    const payload = model.family === "voice-live" ? voiceLiveOffer(model, input.sdp, input)
      : model.family === "live" ? { sdp: input.sdp, instructions: conversationInstructions, voice: model.voice } : { sdp: input.sdp };
    if (Buffer.byteLength(JSON.stringify(payload)) > model.maxRequestBytes) throw new Error("Invalid request bounds");
  } catch { return failure("InvalidRequest", 400); }
  try { origin = (dependencies.readGatewayEnvironment ? await dependencies.readGatewayEnvironment() : { origin: getPublicModelGatewayOrigin() ?? "" }).origin; getTextGenerationEndpoint(model, origin); }
  catch { return failure("GatewayNotConfigured", 503); }
  let key: string; let secret: string | undefined;
  try {
    key = await resolveKey(dependencies, actor, input.apiKeyId, model.apiId);
    if (model.observer) { const observerKey = await resolveKey(dependencies, actor, input.apiKeyId, model.observer.apiId); if (observerKey !== key) throw new Error("Inconsistent subscription"); }
    if (model.family === "realtime") { secret = (dependencies.readHandleSecret ?? (() => getAuthEnvironment().secret))(); if (secret.length < 32) return failure("GatewayNotConfigured", 503); }
  } catch (error) { return failure(error instanceof ApiKeyError && error.status < 500 ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable", error instanceof ApiKeyError && error.status < 500 ? 403 : 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  const cancelled = new AbortController(); const deadline = AbortSignal.timeout(Math.min(dependencies.timeoutMs ?? REALTIME_MAX_DURATION_MS, REALTIME_MAX_DURATION_MS));
  const signal = AbortSignal.any([request.signal, cancelled.signal, deadline]); const fetcher = dependencies.fetch ?? globalThis.fetch; const connector = dependencies.connectWebSocket ?? openControlSocket;
  const encoder = new TextEncoder(); const started = performance.now(); let consumerClosed = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let events = 0; let outputBytes = 0;
      const emit = (event: RealtimeStreamEvent) => {
        if (consumerClosed) return;
        const encoded = encoder.encode(`data: ${JSON.stringify(realtimeStreamEventSchema.parse(event))}\n\n`);
        if (++events > REALTIME_MAX_EVENTS || (outputBytes += encoded.byteLength) > REALTIME_MAX_STREAM_BYTES || (controller.desiredSize ?? 1) < -1_048_576) { cancelled.abort(); throw new Error("Stream output limit"); }
        try { controller.enqueue(encoded); } catch { consumerClosed = true; cancelled.abort(); }
      };
      void (async () => {
        let socket: RealtimeControlSocket | undefined; let call: RealtimeCallHandle | undefined; let handle: string | undefined; let answerSent = false; let createAttempted = false; let failed = false;
        // 障害の位置だけを診断します。本文・SDP・本人・購読・通話ID・秘密値は記録しません。
        let stage = "opening-control"; let upstreamStatus: number | undefined;
        const heartbeat = setInterval(() => { try { emit({ type: "status", stage: answerSent ? "receiving" : "connecting", elapsedMs: Math.round(performance.now() - started) }); } catch { cancelled.abort(); } }, 5_000);
        try {
          emit({ type: "status", stage: "connecting", elapsedMs: 0, maximumDurationMs: REALTIME_MAX_DURATION_MS });
          if (model.family === "voice-live") {
            socket = await connector(websocketEndpoint(model.path, origin), { "api-key": key }, signal);
            await socket.send(voiceLiveOffer(model, input.sdp, input));
          } else {
            createAttempted = true;
            const upstream = await fetcher(getTextGenerationEndpoint(model, origin), { method: "POST", headers: { "api-key": key, "Content-Type": "application/json", Accept: model.family === "realtime" ? "application/sdp" : "application/json" },
              body: JSON.stringify(model.family === "realtime" ? { sdp: input.sdp } : { sdp: input.sdp, instructions: conversationInstructions, voice: model.voice }), redirect: "error", cache: "no-store", signal });
            upstreamStatus = upstream.status; stage = "validating-answer";
            if (model.family === "realtime") { call = captureCall(upstream, actor, input.apiKeyId, model.id); if (call) handle = sealRealtimeCallHandle(call, secret!); }
            if (model.family === "realtime" ? upstream.status !== 201 : !upstream.ok) { void upstream.body?.cancel().catch(() => undefined); throw new Error("Gateway session creation failed"); }
            const bytes = await readSpeechBody(upstream.body, 100_000, signal); const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
            if (text.includes(key) || call && text.includes(call.ticket)) throw new Error("Credential reflected in response");
            let answer: string;
            if (model.family === "realtime") {
              if (!call || upstream.headers.get("content-type")?.split(";", 1)[0] !== "application/sdp") throw new Error("Invalid owned session answer"); answer = validateRealtimeSdp(text);
              const observerUrl = new URL(websocketEndpoint(model.observer!.path, origin)); observerUrl.searchParams.set("call_id", call.callId);
              stage = "opening-observer";
              socket = await connector(observerUrl.href, { "api-key": key, "X-Core-Call-Ticket": call.ticket }, signal);
            } else {
              if (upstream.headers.get("content-type")?.split(";", 1)[0] !== "application/json") throw new Error("Invalid session answer");
              const value: unknown = JSON.parse(text); if (!value || typeof value !== "object" || Array.isArray(value)
                || !/^[A-Za-z0-9_-]{1,160}$/.test(String((value as { sessionId?: unknown }).sessionId ?? ""))) throw new Error("Invalid session answer");
              answer = validateRealtimeSdp((value as { sdp?: unknown }).sdp);
            }
            emit({ type: "answer", sdp: answer, ...(handle ? { handle } : {}) }); answerSent = true;
          }
          stage = "observing-control";
          if (!socket) {
            await new Promise<void>((resolve) => { if (signal.aborted) resolve(); else signal.addEventListener("abort", () => resolve(), { once: true }); });
          } else { let receivedEvents = 0; let receivedBytes = 0; while (!signal.aborted) {
            const value = await socket.receive(signal);
            if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid control event");
            if (++receivedEvents > REALTIME_MAX_EVENTS || (receivedBytes += Buffer.byteLength(JSON.stringify(value))) > REALTIME_MAX_STREAM_BYTES) throw new Error("Control event limit");
            const event = value as Record<string, unknown>; const kind = typeof event.type === "string" && eventNames.has(event.type) ? event.type : undefined;
            if (!kind) continue;
            if (kind === "error" || kind === "rtc.call.error") throw new Error("Service control error");
            if (model.family === "voice-live" && kind === "rtc.call.sdp.created") {
              if (answerSent) throw new Error("Duplicate session answer"); const answer = validateRealtimeSdp(event.sdp_answer); if (answer.includes(key)) throw new Error("Credential reflected in response");
              emit({ type: "answer", sdp: answer }); answerSent = true;
            }
            emit({ type: "event", eventType: kind, elapsedMs: Math.round(performance.now() - started) });
            if (kind === "response.done") {
              const response = event.response; if (!response || typeof response !== "object" || Array.isArray(response)) throw new Error("Invalid terminal event");
              const terminal = response as Record<string, unknown>; const usage = terminal.usage;
              if (usage && typeof usage === "object" && !Array.isArray(usage)) { const numbers = Object.fromEntries(Object.entries(usage).filter(([name, number]) => usageNames.has(name) && typeof number === "number" && Number.isFinite(number) && number >= 0 && number <= 1e15)); if (Object.keys(numbers).length) emit({ type: "usage", values: numbers as Record<string, number> }); }
              // 応答の完了・割込み・出力上限は会話の1ターンの結果です。利用者の停止まで次の発話を受け付けます。
              if (!["completed", "cancelled", "incomplete", "failed"].includes(String(terminal.status))) throw new Error("Invalid turn completion");
            }
          } }
        } catch { failed = !signal.aborted || !answerSent; }
        finally {
          clearInterval(heartbeat); let stopStatus: "confirmed" | "unconfirmed" | "transport-closed" = "transport-closed";
          if (model.family === "realtime") stopStatus = call && await stopCall(call, model, origin, key, fetcher) ? "confirmed" : createAttempted ? "unconfirmed" : "transport-closed";
          try { await socket?.close(); } catch { if (model.family !== "realtime") stopStatus = "unconfirmed"; }
          if (failed || stopStatus === "unconfirmed") console.warn("Realtime control failed", { family: model.family, stage, upstreamStatus, ownershipCaptured: Boolean(call), answerSent, stopStatus });
          try {
            if (failed) emit({ type: "error", error: { code: deadline.aborted ? "GatewayTimeout" : "GenerationFailed" }, ...(handle && stopStatus !== "confirmed" ? { handle } : {}) });
            emit({ type: "complete", durationMs: Math.round(performance.now() - started), inputAudioBytes: 0, outputAudioBytes: 0, stopStatus, ...(handle && stopStatus !== "confirmed" ? { handle } : {}) });
          } catch { /* 閉じられたブラウザーへ通知できなくても、上の停止要求は完了させます。 */ }
          if (!consumerClosed) { consumerClosed = true; try { controller.close(); } catch { /* 切断済みストリームの再終了は不要です。 */ } }
        }
      })();
    },
    cancel() { consumerClosed = true; cancelled.abort(); },
  });
  return new Response(stream, { headers: { ...privateHeaders, "Content-Type": "text/event-stream; charset=utf-8", "X-Accel-Buffering": "no" } });
}
