import WebSocket, { type ClientOptions, type RawData } from "ws";
import { getRealtimeEndpoint, validateRealtimeModel } from "./realtime-integration.ts";
import { decodeRealtimeAudio } from "./realtime-audio.ts";
import { buildRealtimeSessionEvent, RealtimeProtocol } from "./realtime-protocol.ts";
import { realtimeInputSchema, realtimeStreamEventSchema, resolveRealtimeOutputTokens, REALTIME_MAX_AUDIO_BYTES, REALTIME_MAX_DURATION_MS, REALTIME_MAX_STREAM_BYTES, type RealtimeInput, type RealtimeStreamEvent } from "./realtime-request.ts";
import type { ModelGatewayErrorCode } from "./model-request.ts";
import type { RealtimeModel } from "./model-types.ts";
import { resolveRealtimeVoice } from "./realtime-voices.ts";

/** 認可を終えた接続が受け付ける操作です。上流のイベント・URL・キー実値は受け取りません。 */
export type RealtimeConversationCommand = { type: "input_audio"; delta: string; sampleRate?: 24000 } | { type: "stop" } | { type: "cancel_response" };
/** browser接続の入力を登録し、終了時に購読解除するための境界です。 */
export type RegisterRealtimeInput = (receive: (command: RealtimeConversationCommand) => void) => () => void;
/** 音声や利用者を含めず、入力拒否の原因と量だけをサーバーログへ残します。 */
export type RealtimeConversationDiagnostic = {
  reason: "audio-before-ready" | "audio-command-invalid" | "audio-frame-invalid" | "audio-frame-too-large" | "audio-total-exceeded" | "audio-rate-exceeded" | "upstream-send-backpressure";
  family: RealtimeModel["family"]; transport: "websocket"; elapsedMs: number; inputAudioBytes: number;
  frameBytes?: number; availableBytes?: number; bufferedBytes?: number;
};
/** 上流接続・単調増加時計・試験用期限を差し替えます。本人・購読認可はbridgeの接続開始時に行います。 */
export type RealtimeConversationDependencies = { connect?: (url: string, options: ClientOptions) => WebSocket; now?: () => number; timeoutMs?: number; stopTimeoutMs?: number; diagnose?: (value: RealtimeConversationDiagnostic) => void };

const PCM_BYTES_PER_SECOND = 24_000 * 2;
// 再生処理やネットワークでまとめ配送された音声を2秒分まで受け入れ、長期の送信速度は別に制限します。
const INPUT_BURST_BYTES = PCM_BYTES_PER_SECOND * 2;
const INPUT_REFILL_BYTES_PER_MS = PCM_BYTES_PER_SECOND * 1.2 / 1000;

/** 例外本文へ上流の秘密情報を含めず、固定コードだけを公開します。 */
export class RealtimeConversationError extends Error { readonly code: ModelGatewayErrorCode; constructor(code: ModelGatewayErrorCode) { super(code); this.code = code; } }

/**
 * 認可済みのPortal接続とGateway WSSを中継し、利用者の停止まで複数の発話を扱います。
 * @remarks Nextの内部認可routeで検証したmodel・endpoint・キーだけを受け取るサーバー専用処理です。
 * 応答完了ではソケットを閉じません。音声・イベント・時間・送信待ちには明示的な上限を設けます。
 */
export async function runRealtimeConversation(model: RealtimeModel, input: RealtimeInput, endpoint: string, key: string,
  signal: AbortSignal, emit: (event: RealtimeStreamEvent) => void, registerInput: RegisterRealtimeInput, dependencies: RealtimeConversationDependencies = {}): Promise<void> {
  if (typeof window !== "undefined") throw new RealtimeConversationError("RequestNotAllowed");
  validateRealtimeModel(model); realtimeInputSchema.parse(input);
  const url = new URL(endpoint); const origin = `${url.protocol === "wss:" ? "https:" : "http:"}//${url.host}`;
  resolveRealtimeOutputTokens(model, input);
  resolveRealtimeVoice(model, input);
  if (model.transport !== "websocket" || input.modelId !== model.id || getRealtimeEndpoint(model, origin) !== endpoint || !/^[\x21-\x7e]{16,512}$/.test(key)) throw new RealtimeConversationError("InvalidRequest");
  const now = dependencies.now ?? (() => performance.now());
  const started = now(); const protocol = new RealtimeProtocol(model); const elapsed = () => Math.max(0, Math.round(now() - started));
  let socket: WebSocket | undefined; let settled = false; let stopping = false; let bytesRead = 0; let inputAudioBytes = 0;
  let transcriptionBufferedBytes = 0; let transcriptionPending = 0;
  let availableBytes = INPUT_BURST_BYTES; let rateTime = now();
  let unregister: (() => void) | undefined; let deadline: ReturnType<typeof setTimeout> | undefined; let stopTimer: ReturnType<typeof setTimeout> | undefined; let forceClose: ReturnType<typeof setTimeout> | undefined; let heartbeat: ReturnType<typeof setInterval> | undefined;
  let lastPong = now();
  const publish = (event: RealtimeStreamEvent) => emit(realtimeStreamEventSchema.parse(event));
  const diagnose = (reason: RealtimeConversationDiagnostic["reason"], counts: Pick<RealtimeConversationDiagnostic, "frameBytes" | "availableBytes" | "bufferedBytes"> = {}) => {
    const value: RealtimeConversationDiagnostic = { reason, family: model.family, transport: "websocket", elapsedMs: elapsed(), inputAudioBytes, ...counts };
    try { if (dependencies.diagnose) dependencies.diagnose(value); else console.warn("portal.realtime.input_rejected", JSON.stringify(value)); } catch { /* 診断先の障害で音声接続の回収を妨げません。 */ }
  };
  await new Promise<void>((resolve, reject) => {
    const cleanup = () => { if (deadline) clearTimeout(deadline); if (stopTimer) clearTimeout(stopTimer); if (heartbeat) clearInterval(heartbeat); signal.removeEventListener("abort", abort); unregister?.(); };
    const closeTransport = () => {
      if (!socket || socket.readyState === WebSocket.CLOSED) return;
      if (socket.readyState === WebSocket.OPEN) { socket.close(1000); forceClose = setTimeout(() => socket?.terminate(), 1000); forceClose.unref(); }
      else socket.terminate();
    };
    const finish = (error?: RealtimeConversationError, stopStatus: "confirmed" | "transport-closed" | "unconfirmed" = "transport-closed") => {
      if (settled) return; settled = true; cleanup(); closeTransport();
      if (error) reject(error);
      else { try { publish({ type: "complete", durationMs: elapsed(), inputAudioBytes, outputAudioBytes: protocol.outputAudioBytes, stopStatus }); resolve(); } catch { reject(new RealtimeConversationError("GenerationFailed")); } }
    };
    const abort = () => finish(new RealtimeConversationError("RequestCancelled"));
    const send = (value: Record<string, unknown>) => {
      if (socket && socket.bufferedAmount > 128_000) { diagnose("upstream-send-backpressure", { bufferedBytes: socket.bufferedAmount }); throw new RealtimeConversationError("GenerationFailed"); }
      if (settled || !socket || socket.readyState !== WebSocket.OPEN) throw new RealtimeConversationError("GenerationFailed");
      socket.send(JSON.stringify(value), (error?: Error) => { if (error) finish(new RealtimeConversationError("GenerationFailed")); });
    };
    const stop = () => {
      if (settled || stopping) return; stopping = true;
      publish({ type: "status", stage: "stopping", elapsedMs: elapsed() });
      if (!protocol.ready || !socket || socket.readyState !== WebSocket.OPEN) { finish(); return; }
      if (model.family === "live") { send({ type: "session.close" }); stopTimer = setTimeout(() => finish(undefined, "unconfirmed"), dependencies.stopTimeoutMs ?? 4000); }
      else if (model.family === "transcription") {
        if (transcriptionBufferedBytes >= 4800) { transcriptionPending++; transcriptionBufferedBytes = 0; send({ type: "input_audio_buffer.commit" }); }
        if (transcriptionPending) stopTimer = setTimeout(() => finish(), dependencies.stopTimeoutMs ?? 4000);
        else finish();
      }
      else if (protocol.responseActive) { send({ type: "response.cancel" }); stopTimer = setTimeout(() => finish(), dependencies.stopTimeoutMs ?? 2000); }
      else finish();
    };
    signal.addEventListener("abort", abort, { once: true }); if (signal.aborted) { abort(); return; }
    try {
      unregister = registerInput((command) => {
        if (settled) return;
        try {
          if (command.type === "stop") { stop(); return; }
          // 停止クリック直前にworkletから送られたフレームは、終了確認を壊さず破棄します。
          if (stopping) return;
          if (!protocol.ready) { diagnose("audio-before-ready"); throw new RealtimeConversationError("InvalidRequest"); }
          if (command.type === "cancel_response") {
            if (model.family === "live" || model.family === "transcription") publish({ type: "event", eventType: "response.cancel.unsupported", elapsedMs: elapsed() });
            else if (protocol.responseActive) send({ type: "response.cancel" });
            return;
          }
          if (command.type !== "input_audio" || command.sampleRate !== undefined && command.sampleRate !== 24000 || typeof command.delta !== "string" || command.delta.length > 12_800) { diagnose("audio-command-invalid"); throw new RealtimeConversationError("InvalidRequest"); }
          let pcm: Uint8Array;
          try { pcm = decodeRealtimeAudio(command.delta); } catch { diagnose("audio-frame-invalid"); throw new RealtimeConversationError("InvalidRequest"); }
          if (pcm.length > 9600) { diagnose("audio-frame-too-large", { frameBytes: pcm.length }); throw new RealtimeConversationError("InvalidRequest"); }
          if (inputAudioBytes + pcm.length > REALTIME_MAX_AUDIO_BYTES) { diagnose("audio-total-exceeded", { frameBytes: pcm.length }); throw new RealtimeConversationError("InvalidRequest"); }
          const receivedAt = now(); availableBytes = Math.min(INPUT_BURST_BYTES, availableBytes + (receivedAt - rateTime) * INPUT_REFILL_BYTES_PER_MS); rateTime = receivedAt;
          if (pcm.length > availableBytes) { diagnose("audio-rate-exceeded", { frameBytes: pcm.length, availableBytes: Math.max(0, Math.floor(availableBytes)) }); throw new RealtimeConversationError("InvalidRequest"); } availableBytes -= pcm.length; inputAudioBytes += pcm.length;
          send({ type: model.family === "live" ? "session.input_audio.append" : "input_audio_buffer.append", audio: command.delta });
          if (model.family === "transcription") {
            transcriptionBufferedBytes += pcm.length;
            if (transcriptionBufferedBytes >= 14_400) { transcriptionPending++; transcriptionBufferedBytes = 0; send({ type: "input_audio_buffer.commit" }); }
          }
        } catch (error) { finish(error instanceof RealtimeConversationError ? error : new RealtimeConversationError("GenerationFailed")); }
      });
      if (settled) { unregister?.(); return; }
      deadline = setTimeout(() => {
        try { publish({ type: "error", error: { code: "GatewayTimeout" } }); stop(); }
        catch { finish(new RealtimeConversationError("GatewayTimeout")); }
      }, Math.min(dependencies.timeoutMs ?? REALTIME_MAX_DURATION_MS, REALTIME_MAX_DURATION_MS));
      publish({ type: "status", stage: "connecting", elapsedMs: elapsed() });
      socket = (dependencies.connect ?? ((target, options) => new WebSocket(target, options)))(endpoint, { headers: { "api-key": key, "User-Agent": "egakumi-portal-realtime/1.0" }, maxPayload: 262144, followRedirects: false, handshakeTimeout: 10_000, perMessageDeflate: false });
      socket.on("open", () => {
        if (settled) { socket?.terminate(); return; }
        try {
          const event = buildRealtimeSessionEvent(model, input); if (!event) throw new Error("Missing start event"); send(event);
          lastPong = now();
          // マイクをミュート中もproxyのidle期限を越えず、応答不能な接続は回収します。
          heartbeat = setInterval(() => { if (settled) return; try { if (now() - lastPong > 45_000) throw new Error("Missing pong"); if (socket?.readyState === WebSocket.OPEN) socket.ping(); } catch { finish(new RealtimeConversationError("GenerationFailed")); } }, 20_000);
        } catch { finish(new RealtimeConversationError("GenerationFailed")); }
      });
      socket.on("pong", () => { lastPong = now(); });
      socket.on("message", (data: RawData, binary: boolean) => {
        if (settled) return;
        try {
          if (binary) throw new Error("Unexpected binary upstream message");
          const bytes = Array.isArray(data) ? Buffer.concat(data) : Buffer.from(data instanceof ArrayBuffer ? new Uint8Array(data) : data); bytesRead += bytes.length;
          if (bytes.length > 262144 || bytesRead > REALTIME_MAX_STREAM_BYTES || bytes.includes(Buffer.from(key))) throw new Error("Unsafe upstream message");
          const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); const wasReady = protocol.ready;
          for (const event of protocol.accept(value, elapsed())) {
            if (model.family === "transcription" && event.type === "transcript" && event.final) transcriptionPending = Math.max(0, transcriptionPending - 1);
            publish(event);
          }
          if (!wasReady && protocol.ready) publish({ type: "status", stage: "connected", elapsedMs: elapsed() });
          if (protocol.terminal) finish(undefined, "confirmed");
          else if (stopping && (model.family === "transcription" ? transcriptionPending === 0 : !protocol.responseActive && model.family !== "live")) finish();
        } catch { finish(new RealtimeConversationError("GenerationFailed")); }
      });
      socket.on("unexpected-response", (_request, response) => { const status = response.statusCode; response.destroy(); finish(new RealtimeConversationError(status === 429 ? "RateLimitExceeded" : status === 401 || status === 403 ? "GatewayUnauthorized" : "GenerationFailed")); });
      socket.on("error", () => finish(new RealtimeConversationError("GenerationFailed")));
      socket.on("close", () => { if (forceClose) clearTimeout(forceClose); if (!settled) finish(stopping ? undefined : new RealtimeConversationError("GenerationFailed")); });
    } catch { finish(new RealtimeConversationError("GenerationFailed")); }
  });
}
