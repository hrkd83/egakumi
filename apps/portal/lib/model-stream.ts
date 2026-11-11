import { z } from "zod";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { TextGenerationModel } from "./model-types.ts";
import { MODEL_MAX_EVENT_CHARACTERS } from "./model-request.ts";
import { containsModelCredential } from "./model-credential.ts";

const maximumEventCharacters = MODEL_MAX_EVENT_CHARACTERS;
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
    finish_reason: z.enum(["stop", "length", "content_filter", "tool_calls", "function_call"]).nullable().optional(),
  }).passthrough()),
  usage: z.object({ prompt_tokens: tokenCount, completion_tokens: tokenCount, total_tokens: tokenCount }).nullable().optional(),
}).passthrough();
const responseTerminal = z.object({
  model: z.string().min(1).max(200),
  status: z.enum(["completed", "incomplete"]),
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
        if (frame.length > maximumEventCharacters) throw new Error("SSE event limit exceeded");
        frames.push(frame); this.lines = [];
      }
    }
    this.pending = this.pending.slice(offset);
    if (this.pending.length + this.lines.reduce((size, line) => size + line.length, 0) > maximumEventCharacters) throw new Error("SSE event limit exceeded");
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

  /** 正常な終端イベントならtrueを返します。それ以外の正常イベントはそのまま中継できます。 */
  accept(frame: string, operation: TextGenerationModel["operationId"]): { forward: boolean; terminal: boolean } {
    const event = eventFields(frame);
    if (!event) return { forward: false, terminal: false };
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
      const response = responseTerminal.parse((value as Record<string, unknown>).response);
      if (response.status !== type.slice("response.".length)) throw new Error("Mismatched response terminal");
      return { forward: true, terminal: true };
    }
    return { forward: true, terminal: false };
  }
}

/**
 * 購読キーの反射を、イベント単体と本文の連結後の両方で中継前に遮断します。
 * @remarks キーの接頭辞に一致する末尾の長さだけを保持し、原イベントの保留も既存の単一イベント上限内に制限します。
 */
class StreamCredentialGuard {
  private readonly credential: string;
  private readonly prefix: number[];
  private readonly tails = new Map<string, number>();
  private pending: string[] = [];
  private pendingCharacters = 0;

  constructor(credential: string) {
    if (!/^[\x21-\x7e]{16,512}$/.test(credential)) throw new Error("Invalid stream credential");
    this.credential = credential;
    this.prefix = Array(credential.length).fill(0);
    for (let index = 1, matched = 0; index < credential.length; index++) {
      while (matched && credential[index] !== credential[matched]) matched = this.prefix[matched - 1];
      if (credential[index] === credential[matched]) matched++;
      this.prefix[index] = matched;
    }
  }

  /** 連結された本文の末尾を線形時間で検査し、本文自体は蓄積しません。 */
  private append(lane: string, text: string): void {
    let matched = this.tails.get(lane) ?? 0;
    for (const character of text) {
      while (matched && character !== this.credential[matched]) matched = this.prefix[matched - 1];
      if (character === this.credential[matched]) matched++;
      if (matched === this.credential.length) throw new Error("Unexpected credential reflection");
    }
    if (matched) this.tails.set(lane, matched); else this.tails.delete(lane);
    if (this.tails.size > 65) throw new Error("Stream credential channel limit");
  }

  /** 省略された座標は従来どおり先頭とし、壊れた座標は別の本文へ推測しません。 */
  private coordinate(value: unknown): number {
    if (value === undefined) return 0;
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid stream coordinates");
    return value;
  }

  /** 安全が確定した原イベントだけを順序どおり解放します。終端の妥当性は呼出側が先に検証します。 */
  accept(frame: string, operation: TextGenerationModel["operationId"], terminal: boolean): string[] {
    const event = eventFields(frame);
    if (frame.includes(this.credential)) throw new Error("Unexpected credential reflection");
    if (event && event.data !== "[DONE]") {
      const value = JSON.parse(event.data) as Record<string, unknown>;
      if (containsModelCredential(value, this.credential)) throw new Error("Unexpected credential reflection");
      if (operation === "chat-completions") {
        for (const choice of (value.choices as { index: number; delta: Record<string, unknown> }[])) {
          if (!choice.delta) continue;
          for (const field of ["content", "refusal"]) {
            const text = choice.delta[field];
            if (typeof text !== "string") continue;
            this.append(`chat:${choice.index}:${field}`, text);
            if (choice.index === 0) this.append("browser-text", text);
          }
        }
      } else if (["response.output_text.delta", "response.refusal.delta"].includes(String(value.type)) && typeof value.delta === "string") {
        const output = this.coordinate(value.output_index), content = this.coordinate(value.content_index);
        this.append(`responses:${output}:${content}:${value.type}`, value.delta);
        this.append("browser-text", value.delta);
      }
    }
    this.pending.push(frame);
    this.pendingCharacters += frame.length;
    if (this.pendingCharacters > maximumEventCharacters) throw new Error("Stream credential buffer limit");
    if (this.tails.size && !terminal) return [];
    const safe = this.pending;
    this.pending = []; this.pendingCharacters = 0;
    if (terminal) this.tails.clear();
    return safe;
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
  credential: string;
  requestId?: string;
}): ReadableStream<Uint8Array> {
  const reader = options.body.getReader();
  let cancelled = false;
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      const frames = new EventFrames();
      const validation = new StreamValidation();
      const credentials = new StreamCredentialGuard(options.credential);
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
          for (const frame of frames.push(part.done ? undefined : part.value)) {
            const result = validation.accept(frame, options.operation);
            if (result.forward) {
              for (const safe of credentials.accept(frame, options.operation, result.terminal)) controller.enqueue(encoder.encode(safe));
            }
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
