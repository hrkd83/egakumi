import { z } from "zod";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { TextGenerationModel } from "./model-types.ts";
import { MODEL_MAX_RESPONSE_BYTES, MODEL_MAX_EVENT_BYTES, MODEL_MAX_STREAM_EVENTS } from "./model-request.ts";

const maximumStreamBytes = MODEL_MAX_RESPONSE_BYTES;
const maximumEventBytes = MODEL_MAX_EVENT_BYTES;
const maximumEvents = MODEL_MAX_STREAM_EVENTS;
const encoder = new TextEncoder();
const tokenCount = z.number().int().nonnegative().safe();
const contentFilterResult = z.object({
  filtered: z.boolean().optional(),
  detected: z.boolean().optional(),
  severity: z.enum(["safe", "very_low", "low", "medium", "high"]).optional(),
}).strict().refine((value) => Object.keys(value).length > 0);
/** Azureは生成チャンクに先立ち、モデル名を持たない入力のフィルター判定を返します。 */
const azurePromptAnnotation = z.object({
  choices: z.array(z.never()).length(0),
  created: z.literal(0),
  id: z.literal(""),
  model: z.literal(""),
  object: z.literal(""),
  prompt_filter_results: z.array(z.object({
    prompt_index: tokenCount,
    content_filter_results: z.record(z.string().regex(/^[a-z_]{1,80}$/), contentFilterResult),
  }).strict()).min(1).max(128),
}).strict();
const chatChunk = z.object({
  object: z.literal("chat.completion.chunk"),
  model: z.string().min(1).max(200),
  choices: z.array(z.object({
    index: tokenCount,
    delta: z.object({}).passthrough(),
    finish_reason: z.string().max(100).nullable().optional(),
  }).passthrough()),
  usage: z.object({ prompt_tokens: tokenCount, completion_tokens: tokenCount, total_tokens: tokenCount }).nullable().optional(),
}).passthrough();
const responseTerminal = z.object({
  model: z.string().min(1).max(200),
  output: z.array(z.object({ type: z.string() }).passthrough()),
}).passthrough();

/** 改行やUTF-8文字が通信チャンクの途中で分割されても、SSEのイベント単位に復元します。 */
class EventFrames {
  private readonly decoder = new TextDecoder("utf-8", { fatal: true });
  private pending = "";
  private lines: string[] = [];

  /** 完結したイベントだけを返します。終端のないイベントを推測で補完しません。 */
  push(chunk?: Uint8Array): string[] {
    this.pending += this.decoder.decode(chunk, { stream: chunk !== undefined });
    const frames: string[] = [];
    let offset = 0;
    while (offset < this.pending.length) {
      const match = /[\r\n]/.exec(this.pending.slice(offset));
      if (!match) break;
      const end = offset + match.index;
      if (this.pending[end] === "\r" && end + 1 === this.pending.length && chunk !== undefined) break;
      const width = this.pending[end] === "\r" && this.pending[end + 1] === "\n" ? 2 : 1;
      const line = this.pending.slice(offset, end);
      this.lines.push(this.pending.slice(offset, end + width));
      offset = end + width;
      if (!line) {
        const frame = this.lines.join("");
        if (encoder.encode(frame).byteLength > maximumEventBytes) throw new Error("SSE event limit exceeded");
        frames.push(frame); this.lines = [];
      }
    }
    this.pending = this.pending.slice(offset);
    if (encoder.encode(this.pending + this.lines.join("")).byteLength > maximumEventBytes) throw new Error("SSE event limit exceeded");
    return frames;
  }
}

/** SSE本文のdata行を連結し、標準のeventフィールドだけを検証に利用します。 */
function eventFields(frame: string): { event?: string; data: string } | null {
  const lines = frame.split(/\r\n|\r|\n/);
  const data: string[] = [];
  let event: string | undefined;
  for (const line of lines) {
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    else if (line.startsWith("event:")) event = line.slice(6).replace(/^ /, "");
  }
  return data.length ? { event, data: data.join("\n") } : null;
}

/** APIごとの正常な終端を記録し、エラー本文をブラウザーへ転送しないための検証状態です。 */
class StreamValidation {
  private seen = false;
  private chatFinished = false;
  private eventCount = 0;

  /** 正常な終端イベントならtrueを返します。それ以外の正常イベントはそのまま中継できます。 */
  accept(frame: string, operation: TextGenerationModel["operationId"]): { forward: boolean; terminal: boolean } {
    const event = eventFields(frame);
    if (!event) return { forward: false, terminal: false };
    if (++this.eventCount > maximumEvents) throw new Error("SSE count limit exceeded");
    if (operation === "chat-completions" && event.data === "[DONE]") {
      if (!this.seen || !this.chatFinished) throw new Error("SSE completed before a result");
      return { forward: true, terminal: true };
    }
    const value: unknown = JSON.parse(event.data);
    if (!value || typeof value !== "object" || Array.isArray(value) || ("error" in value && value.error !== null && value.error !== undefined)) throw new Error("Invalid model event");
    if (operation === "chat-completions") {
      if (event.event && event.event !== "message") throw new Error("Unexpected chat event");
      // 入力のフィルター判定は表示用に残しますが、生成開始・完了の根拠にはしません。
      if (azurePromptAnnotation.safeParse(value).success) return { forward: true, terminal: false };
      const parsed = chatChunk.parse(value);
      this.seen = true;
      this.chatFinished ||= parsed.choices.some((choice) => choice.finish_reason !== null && choice.finish_reason !== undefined);
      return { forward: true, terminal: false };
    }
    const type = (value as Record<string, unknown>).type;
    if (typeof type !== "string" || !/^response\.[a-z0-9_.]+$/.test(type) || (event.event && event.event !== type)) throw new Error("Unexpected response event");
    if (type === "response.failed" || type.endsWith(".error")) throw new Error("Model stream failed");
    this.seen = true;
    if (type === "response.completed" || type === "response.incomplete") {
      responseTerminal.parse((value as Record<string, unknown>).response);
      return { forward: true, terminal: true };
    }
    return { forward: true, terminal: false };
  }
}

/** ストリーム中の失敗を、上流の本文を含めないポータル専用イベントで通知します。 */
function failureFrame(code: ModelGatewayErrorCode, identifier?: string): Uint8Array {
  const failure: TextGenerationFailure = { error: { code }, ...(identifier ? { requestId: identifier } : {}) };
  return encoder.encode(`event: portal.error\ndata: ${JSON.stringify(failure)}\n\n`);
}

/**
 * APIMのSSEをイベントごとに検証して逐次中継します。応答全体のバッファリングはしません。
 * @remarks 正常な終端がない切断、時間超過、サイズ超過は成功として扱いません。購読解除時は上流も中止します。
 */
export function relayModelStream(options: {
  body: ReadableStream<Uint8Array>;
  operation: TextGenerationModel["operationId"];
  signal: AbortSignal;
  timeoutSignal: AbortSignal;
  requestSignal: AbortSignal;
  abortUpstream: () => void;
  requestId?: string;
}): ReadableStream<Uint8Array> {
  const reader = options.body.getReader();
  let cancelled = false;
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      const frames = new EventFrames();
      const validation = new StreamValidation();
      let bytes = 0;
      let terminal = false;
      let abortListener: (() => void) | undefined;
      const aborted = new Promise<never>((_resolve, reject) => {
        abortListener = () => reject(new Error("Model stream aborted"));
        if (options.signal.aborted) abortListener();
        else options.signal.addEventListener("abort", abortListener, { once: true });
      });
      try {
        while (!terminal) {
          const part = await Promise.race([reader.read(), aborted]);
          if (!part.done) {
            bytes += part.value.byteLength;
            if (bytes > maximumStreamBytes) throw new Error("Model stream limit exceeded");
          }
          for (const frame of frames.push(part.done ? undefined : part.value)) {
            const result = validation.accept(frame, options.operation);
            if (result.forward) controller.enqueue(encoder.encode(frame));
            if (result.terminal) { terminal = true; break; }
          }
          if (part.done && !terminal) throw new Error("Model stream ended without terminal event");
        }
      } catch {
        if (!cancelled) controller.enqueue(failureFrame(options.timeoutSignal.aborted ? "GatewayTimeout" : options.requestSignal.aborted ? "RequestCancelled" : "GenerationFailed", options.requestId));
      } finally {
        if (abortListener) options.signal.removeEventListener("abort", abortListener);
        options.abortUpstream();
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
        if (!cancelled) controller.close();
      }
    },
    cancel() {
      cancelled = true;
      options.abortUpstream();
      return reader.cancel();
    },
  });
}
