import type { ModelGatewayErrorCode, TextGenerationFailure, TextGenerationResult } from "./model-request.ts";
import type { TextGenerationModel } from "./model-types.ts";
import { MODEL_MAX_RESPONSE_BYTES, MODEL_MAX_EVENT_BYTES, MODEL_MAX_STREAM_EVENTS } from "./model-request.ts";

const maximumStreamBytes = MODEL_MAX_RESPONSE_BYTES;
const maximumFrameCharacters = MODEL_MAX_EVENT_BYTES;
const maximumEvents = MODEL_MAX_STREAM_EVENTS;
const errorCodes = new Set<ModelGatewayErrorCode>([
  "InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable",
  "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout",
  "GenerationFailed", "OutputLimitReached", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable",
]);

/** 応答の読取り失敗を、画面で翻訳可能な公開エラーに統一します。 */
export class ModelResponseError extends Error {
  readonly failure: TextGenerationFailure;

  constructor(failure: TextGenerationFailure) {
    super(failure.error.code);
    this.name = "ModelResponseError";
    this.failure = failure;
  }
}

/** SSEの途中経過も、確定時と同じ表示用の形で受け取ります。 */
export type ModelResponseOptions = {
  onStreamUpdate?: (result: TextGenerationResult) => void;
};

/** 実際に到着したSSEイベントです。本文はJSONオブジェクトか、終端を表す文字列です。 */
type StreamEvent = { event?: string; data: Record<string, unknown> | string };

/** プロトタイプに依存せず、JSONオブジェクトを判定します。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 表示へ出してよい要求IDだけを採用します。 */
function safeRequestId(value: unknown): string | undefined {
  return typeof value === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value) ? value : undefined;
}

/** 上流本文や例外メッセージをそのまま表示しないための、固定エラー変換です。 */
function responseFailure(value: unknown, fallbackRequestId?: string): TextGenerationFailure {
  const error = isRecord(value) && isRecord(value.error) ? value.error : undefined;
  const code = typeof error?.code === "string" && errorCodes.has(error.code as ModelGatewayErrorCode)
    ? error.code as ModelGatewayErrorCode : "GenerationFailed";
  const requestId = isRecord(value) ? safeRequestId(value.requestId) ?? fallbackRequestId : fallbackRequestId;
  const retryAfterSeconds = isRecord(value) && Number.isInteger(value.retryAfterSeconds)
    && Number(value.retryAfterSeconds) > 0 && Number(value.retryAfterSeconds) <= 3600
    ? Number(value.retryAfterSeconds) : undefined;
  return { error: { code }, ...(requestId ? { requestId } : {}), ...(retryAfterSeconds ? { retryAfterSeconds } : {}) };
}

/** トークン数は欠損を0で補わず、3項目とも有効な場合だけ表示します。 */
function readUsage(value: unknown, responses = false): TextGenerationResult["usage"] {
  if (!isRecord(value)) return undefined;
  const inputTokens = value[responses ? "input_tokens" : "prompt_tokens"];
  const outputTokens = value[responses ? "output_tokens" : "completion_tokens"];
  const totalTokens = value.total_tokens;
  if (![inputTokens, outputTokens, totalTokens].every((count) => Number.isSafeInteger(count) && Number(count) >= 0)) return undefined;
  return { inputTokens: Number(inputTokens), outputTokens: Number(outputTokens), totalTokens: Number(totalTokens) };
}

/** Responsesの完了イベントから、モデルが返した最終テキストを取り出します。 */
function readResponseText(value: unknown): string | undefined {
  if (!isRecord(value) || !Array.isArray(value.output)) return undefined;
  const parts = value.output.filter(isRecord).filter((item) => item.type === "message")
    .flatMap((item) => Array.isArray(item.content) ? item.content : [])
    .filter(isRecord).filter((part) => part.type === "output_text" || part.type === "refusal")
    .map((part) => typeof part.text === "string" ? part.text : typeof part.refusal === "string" ? part.refusal : "");
  return parts.length ? parts.join("\n") : undefined;
}

/** JSON応答にも読取り上限を設け、異常な本文を画面のメモリーへ取り込みません。 */
async function readJsonResponse(response: Response): Promise<unknown> {
  if (!response.body) throw new Error("Missing response body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let content = "";
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > MODEL_MAX_RESPONSE_BYTES) throw new Error("Response is too large");
      content += decoder.decode(part.value, { stream: true });
    }
    content += decoder.decode();
    return JSON.parse(content);
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/** BFFが返す非ストリーム応答を確認します。実際のJSON本文は加工せず参照用に保持します。 */
function readGenerationResult(value: unknown): TextGenerationResult {
  if (!isRecord(value) || typeof value.text !== "string" || typeof value.model !== "string"
      || !Number.isFinite(value.durationMs) || Number(value.durationMs) < 0 || !isRecord(value.rawResponse)) {
    throw new Error("Invalid generation response");
  }
  const usage = isRecord(value.usage) && [value.usage.inputTokens, value.usage.outputTokens, value.usage.totalTokens]
    .every((count) => Number.isSafeInteger(count) && Number(count) >= 0)
    ? { inputTokens: Number(value.usage.inputTokens), outputTokens: Number(value.usage.outputTokens), totalTokens: Number(value.usage.totalTokens) } : undefined;
  const requestId = safeRequestId(value.requestId);
  return {
    text: value.text, model: value.model, durationMs: Number(value.durationMs), rawResponse: value.rawResponse,
    ...(usage ? { usage } : {}), ...(requestId ? { requestId } : {}),
    ...(typeof value.finishReason === "string" ? { finishReason: value.finishReason } : {}),
  };
}

/**
 * Chat CompletionsとResponsesのSSEを読み、実際のイベントと生成文を並行して表示します。
 * @remarks 正式な終端前に切断された応答は成功にしません。コメント・改行・UTF-8の分割を吸収します。
 */
async function readStream(response: Response, model: TextGenerationModel, options: ModelResponseOptions): Promise<TextGenerationResult> {
  if (!response.body) throw new Error("Missing stream body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const events: StreamEvent[] = [];
  const started = performance.now();
  const requestId = safeRequestId(response.headers.get("apim-request-id"));
  let text = "";
  let selectedModel = model.id;
  let usage: TextGenerationResult["usage"];
  let finishReason: string | undefined;
  let terminal = false;
  let size = 0;
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];
  let frameCharacters = 0;
  let lastNotification = 0;

  /** 変更後に参照が書き換わらないスナップショットを表示側へ渡します。 */
  function snapshot(): TextGenerationResult {
    return {
      text, model: selectedModel, durationMs: Math.round(performance.now() - started),
      rawResponse: { events: [...events] }, ...(requestId ? { requestId } : {}),
      ...(usage ? { usage: { ...usage } } : {}), ...(finishReason ? { finishReason } : {}),
    };
  }

  /** 大量の細かなイベントで全JSONの再描画が連続しないよう、途中表示の頻度だけを抑えます。 */
  function notify(force = false) {
    if (!options.onStreamUpdate) return;
    const now = performance.now();
    if (force || lastNotification === 0 || now - lastNotification >= 50) {
      lastNotification = now;
      options.onStreamUpdate(snapshot());
    }
  }

  /** 1フレームを処理し、プロトコルの終端と上流の失敗を区別します。 */
  function dispatch() {
    const data = dataLines.join("\n");
    dataLines = [];
    frameCharacters = 0;
    const name = eventName;
    eventName = "";
    if (!data) return;
    if (events.length >= maximumEvents || terminal) throw new Error("Unexpected stream event");
    if (data === "[DONE]") {
      if (model.operationId !== "chat-completions" || !finishReason) throw new Error("Invalid stream terminator");
      events.push({ ...(name ? { event: name } : {}), data });
      terminal = true;
      notify(true);
      return;
    }
    const value: unknown = JSON.parse(data);
    if (!isRecord(value)) throw new Error("Invalid stream object");
    if (name === "portal.error") throw new ModelResponseError(responseFailure(value, requestId));
    if (name === "error" || value.error !== undefined && value.error !== null) throw new Error("Generation stream failed");
    events.push({ ...(name ? { event: name } : {}), data: value });
    if (model.operationId === "chat-completions") {
      if (!Array.isArray(value.choices)) throw new Error("Invalid chat chunk");
      if (typeof value.model === "string" && value.model.trim()) selectedModel = value.model;
      for (const choice of value.choices.filter(isRecord)) {
        if (choice.index !== undefined && choice.index !== 0) continue;
        if (isRecord(choice.delta)) {
          if (typeof choice.delta.content === "string") text += choice.delta.content;
          if (typeof choice.delta.refusal === "string") text += choice.delta.refusal;
        }
        if (typeof choice.finish_reason === "string") finishReason = choice.finish_reason;
      }
      usage = readUsage(value.usage) ?? usage;
    } else {
      const type = typeof value.type === "string" ? value.type : name;
      if (type === "response.failed" || type === "error") throw new Error("Response generation failed");
      if (["response.output_text.delta", "response.refusal.delta"].includes(type) && typeof value.delta === "string") text += value.delta;
      if (isRecord(value.response)) {
        if (typeof value.response.model === "string" && value.response.model.trim()) selectedModel = value.response.model;
        usage = readUsage(value.response.usage, true) ?? usage;
      }
      if (type === "response.completed" || type === "response.incomplete") {
        if (!isRecord(value.response)) throw new Error("Missing final response");
        finishReason = type === "response.completed" ? "completed" : "incomplete";
        text = readResponseText(value.response) ?? text;
        terminal = true;
      }
    }
    notify(terminal);
  }

  /** SSEの行を分解します。任意のイベント名をコードとして実行せず、データとして扱います。 */
  function line(value: string) {
    if (!value) { dispatch(); return; }
    frameCharacters += value.length;
    if (frameCharacters > maximumFrameCharacters) throw new Error("Stream frame is too large");
    if (value.startsWith(":")) return;
    const colon = value.indexOf(":");
    const field = colon < 0 ? value : value.slice(0, colon);
    const raw = colon < 0 ? "" : value.slice(colon + 1);
    const data = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "data") dataLines.push(data);
    if (field === "event") eventName = data;
  }

  /** CR・LF・CRLFを扱い、チャンク末尾のCRは次のチャンクまで待ちます。 */
  function drain(ended = false) {
    let offset = 0;
    for (let index = 0; index < buffer.length; index++) {
      const character = buffer[index];
      if (character !== "\n" && character !== "\r") continue;
      if (character === "\r" && index === buffer.length - 1 && !ended) break;
      line(buffer.slice(offset, index));
      if (character === "\r" && buffer[index + 1] === "\n") index++;
      offset = index + 1;
    }
    buffer = buffer.slice(offset);
    if (buffer.length + frameCharacters > maximumFrameCharacters) throw new Error("Stream frame is too large");
    if (ended && buffer) { line(buffer); buffer = ""; }
  }

  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > maximumStreamBytes) throw new Error("Stream is too large");
      buffer += decoder.decode(part.value, { stream: true });
      drain();
    }
    buffer += decoder.decode();
    drain(true);
    if (dataLines.length) throw new Error("Unterminated stream event");
    if (!terminal) throw new Error("Stream ended before completion");
    return snapshot();
  } catch (error) {
    if (events.length) notify(true);
    throw error;
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/**
 * JSONとSSEの実応答を共通の表示形式へ変換し、元の応答も組み込み確認用に保持します。
 * @param response ポータルの認証済みBFFから受け取った応答。
 * @param model 呼び出したAPI形式を識別する、カタログのモデル情報。
 * @param options ストリームを逐次表示する場合の通知先。
 */
export async function readModelResponse(response: Response, model: TextGenerationModel, options: ModelResponseOptions = {}): Promise<TextGenerationResult> {
  const identifier = safeRequestId(response.headers.get("apim-request-id"));
  try {
    if (!response.ok) throw new ModelResponseError(responseFailure(await readJsonResponse(response), identifier));
    const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
    if (contentType === "text/event-stream") return await readStream(response, model, options);
    if (contentType !== "application/json") throw new Error("Unexpected response type");
    return readGenerationResult(await readJsonResponse(response));
  } catch (error) {
    if (error instanceof ModelResponseError) throw error;
    throw new ModelResponseError(responseFailure(undefined, identifier));
  }
}
