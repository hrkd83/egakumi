import { z } from "zod";
import { ModelResponseError } from "./model-response.ts";
import { realtimeStreamEventSchema, type RealtimeStreamEvent } from "./realtime-request.ts";
import { MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";

/** 会話の終了時だけ返す、BFFの停止確認です。応答一件の完了とは区別します。 */
export type RealtimeCompletion = Extract<RealtimeStreamEvent, { type: "complete" }>;
/** 画面内だけで保持する会話字幕です。接続IDや認証情報は含めません。 */
export type RealtimeTurn = { id: number; role: "input" | "output"; text: string; final: boolean; pendingFinal?: boolean; segmentIndex?: number };
const maximumFrameCharacters = 400_000;

/** 任意の通信エラー本文を公開せず、固定の失敗へ揃えます。 */
function invalid(): ModelResponseError { return new ModelResponseError({ error: { code: "GenerationFailed" } }); }

/** 同じPortalの認証Cookieで接続し、キーやチケットをURLへ含めません。 */
export function getRealtimeSocketUrl(origin: string): string {
  const url = new URL(origin);
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash || !["http:", "https:"].includes(url.protocol) || url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) throw invalid();
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"; url.pathname = "/api/models/realtime/socket";
  return url.href;
}

/** 選別済みのJSONフレームだけを受け入れ、上流の任意イベントを直接表示しません。 */
export function readRealtimeSocketMessage(raw: unknown): RealtimeStreamEvent {
  if (typeof raw !== "string" || !raw.length || raw.length > maximumFrameCharacters) throw invalid();
  try { return realtimeStreamEventSchema.parse(JSON.parse(raw)); } catch { throw invalid(); }
}

/** 100msのPCMを送るための符号化です。WAVヘッダーや大きな録音ファイルは送りません。 */
export function encodeRealtimePcmChunk(bytes: Uint8Array): string {
  if (!bytes.length || bytes.length % 2 || bytes.length > 9_600) throw invalid();
  let value = ""; for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value);
}

const microphoneFrameSchema = z.object({ pcm: z.instanceof(ArrayBuffer).refine((value) => value.byteLength === 4_800), capturedAt: z.number().finite().nonnegative() }).strict();
/**
 * main threadが遅れた後もマイク音声を一括送信せず、100ms間隔へ整えます。
 * @remarks 収録時刻はAudioContextの秒数です。最大300ms・3フレームだけを保持し、古い音声と接続準備中の音声は破棄します。
 */
export class RealtimeMicrophoneQueue {
  private frames: z.infer<typeof microphoneFrameSchema>[] = [];
  private minimumCaptureAt: number | null = null;
  private lastCaptureAt = -Infinity;
  private lastSentAt = -Infinity;
  private nextDeliveryAt = -Infinity;

  /** 接続完了またはミュート変更後に、ここから先に収録した音声だけを扱います。 */
  activate(captureTime: number): void {
    if (!Number.isFinite(captureTime) || captureTime < 0) throw invalid();
    this.frames = []; this.minimumCaptureAt = captureTime + 0.1; this.lastCaptureAt = captureTime;
  }
  /** 有限個の新しいフレームだけを残し、UIの処理停止中に溜まった音声を後追いしません。 */
  push(value: unknown, captureTime: number): void {
    if (this.minimumCaptureAt === null) return;
    const result = microphoneFrameSchema.safeParse(value);
    if (!result.success || !Number.isFinite(captureTime) || result.data.capturedAt > captureTime + 0.05) throw invalid();
    const frame = result.data;
    if (frame.capturedAt < this.minimumCaptureAt - 0.000_001 || frame.capturedAt <= this.lastCaptureAt || captureTime - frame.capturedAt > 0.3) return;
    this.lastCaptureAt = frame.capturedAt;
    this.frames = [...this.frames.filter((entry) => captureTime - entry.capturedAt <= 0.3), frame].slice(-3);
  }
  /** 100msの予定時刻を維持し、5ms以内の揺らぎを累積遅延に変えずに吸収します。 */
  take(captureTime: number, deliveryTimeMs: number): ArrayBuffer | null {
    if (!Number.isFinite(captureTime) || !Number.isFinite(deliveryTimeMs)) throw invalid();
    this.frames = this.frames.filter((entry) => captureTime - entry.capturedAt <= 0.3);
    if (deliveryTimeMs - this.lastSentAt < 95 || deliveryTimeMs + 5 < this.nextDeliveryAt || !this.frames.length) return null;
    // 長い停止後だけ時計を合わせ直し、溜まった音声の一括追い付き送信は行いません。
    this.nextDeliveryAt = deliveryTimeMs - this.nextDeliveryAt > 100 ? deliveryTimeMs + 100 : this.nextDeliveryAt + 100;
    this.lastSentAt = deliveryTimeMs; return this.frames.shift()!.pcm;
  }
  /** 終了時に未送信音声を手放し、過去の会話へ送信しません。 */
  clear(): void { this.frames = []; this.minimumCaptureAt = null; }
}

/** 実行環境で表現できる本文だけを連結し、字幕を途中で切り捨てません。 */
function concatenateTranscript(left: string, right: string): string {
  if (left.length + right.length > MAX_RUNTIME_STRING_CHARACTERS) throw invalid();
  return left + right;
}

/** 発話ごとの字幕を更新し、確定順が前後しても本文と会話履歴を保持します。 */
export function appendRealtimeTranscript(turns: readonly RealtimeTurn[], event: Extract<RealtimeStreamEvent, { type: "transcript" }>): RealtimeTurn[] {
  if (event.text.length > MAX_RUNTIME_STRING_CHARACTERS) throw invalid();
  const result = turns.filter((turn) => event.segmentIndex !== undefined ? turn.text.length > 0 : turn.text.trim().length > 0).map((turn) => ({ ...turn }));
  if (event.segmentIndex !== undefined) {
    if (event.role !== "input" || !Number.isSafeInteger(event.segmentIndex) || event.segmentIndex < 0) throw invalid();
    const current = result.find((turn) => turn.segmentIndex === event.segmentIndex);
    if (current) {
      if (event.final) { if (event.text.length) current.text = event.text; current.final = true; }
      else if (!current.final) current.text = concatenateTranscript(current.text, event.text);
    } else if (event.text.length) {
      const id = result.reduce((maximum, turn) => Math.max(maximum, turn.id), 0) + 1;
      if (!Number.isSafeInteger(id)) throw invalid();
      result.push({ id, role: "input", text: event.text, final: event.final, segmentIndex: event.segmentIndex });
    }
    result.sort((left, right) => (left.segmentIndex ?? -1) - (right.segmentIndex ?? -1));
    return result;
  }
  let active = result.findLastIndex((turn) => turn.role === event.role && !turn.final);
  if (!event.text.trim()) {
    // 無音の確定通知では行を作りません。空のfinalは進行中の字幕だけを確定し、本文を消しません。
    if (active < 0) return result;
    const current = result[active];
    if (event.final) { current.final = true; current.pendingFinal = false; }
    else current.text = concatenateTranscript(current.text, event.text);
  } else {
    // 別話者の開始後に届く確定字幕を、同じ発話の部分字幕へ反映します。
    if (active < 0 && event.final) active = result.findLastIndex((turn) => turn.role === event.role && turn.pendingFinal && (event.text.startsWith(turn.text) || turn.text.startsWith(event.text)));
    if (active >= 0) { const current = result[active]; current.text = event.final ? event.text : concatenateTranscript(current.text, event.text); current.final = event.final; current.pendingFinal = false; }
    else { for (const turn of result) if (turn.role !== event.role && !turn.final) { turn.final = true; turn.pendingFinal = true; } const id = (result.at(-1)?.id ?? 0) + 1; if (!Number.isSafeInteger(id)) throw invalid(); result.push({ id, role: event.role, text: event.text, final: event.final }); }
  }
  return result;
}

/**
 * 文字起こしの区間データを維持したまま、表示用の一つの段落へ連結します。
 * @remarks 区間の境界でも元の空白と文字をそのまま保持します。単語途中の区切りを推測せず、空白の挿入・本文の補完・重複除去は行いません。
 */
export function joinRealtimeTranscriptionParagraph(turns: readonly RealtimeTurn[]): string {
  let paragraph = "";
  for (const turn of turns.filter((item) => item.role === "input").toSorted((left, right) => (left.segmentIndex ?? left.id) - (right.segmentIndex ?? right.id))) {
    if (!turn.text.length) continue;
    paragraph = concatenateTranscript(paragraph, turn.text);
  }
  return paragraph.trim() ? paragraph : "";
}

/** 本人に束縛した停止参照だけを使い、制御接続を失った場合の停止を再確認します。 */
export async function stopRealtimeBrowser(handle: string, signal?: AbortSignal): Promise<"confirmed" | "unconfirmed"> {
  const bounded = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(8_000)]);
  const response = await fetch("/api/models/realtime/stop", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ handle }), signal: bounded, keepalive: true });
  if (!response.ok || !response.body) return "unconfirmed";
  const reader = response.body.getReader(); let body = "", bytes = 0;
  const decoder = new TextDecoder("utf-8", { fatal: true }); const abort = () => { void reader.cancel().catch(() => undefined); };
  bounded.addEventListener("abort", abort, { once: true });
  try {
    while (true) { const part = await reader.read(); bounded.throwIfAborted(); if (part.done) break; bytes += part.value.byteLength; if (bytes > 1_024) return "unconfirmed"; body += decoder.decode(part.value, { stream: true }); }
    const parsed = z.object({ stopStatus: z.enum(["confirmed", "unconfirmed"]) }).strict().safeParse(JSON.parse(body + decoder.decode()));
    return parsed.success ? parsed.data.stopStatus : "unconfirmed";
  } finally { bounded.removeEventListener("abort", abort); void reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

const knownBrowserEvents = new Set(["session.created", "session.updated", "session.closed", "conversation.item.created", "conversation.item.added", "conversation.item.done", "conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed", "conversation.item.input_audio_transcription.failed", "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped", "input_audio_buffer.committed", "input_audio_buffer.cleared", "response.created", "response.done", "response.output_item.added", "response.output_item.done", "response.content_part.added", "response.content_part.done", "response.audio.delta", "response.audio.done", "response.output_audio.delta", "response.output_audio.done", "response.audio_transcript.delta", "response.audio_transcript.done", "response.output_audio_transcript.delta", "response.output_audio_transcript.done", "response.text.delta", "response.text.done", "response.output_text.delta", "response.output_text.done", "session.input_transcript.delta", "session.output_transcript.delta", "session.output_audio.delta", "session.output_audio.done", "error", "rtc.call.error"]);
/** 配列とnullを、サービスイベントのオブジェクトとして扱いません。 */
function record(value: unknown): Record<string, unknown> | undefined { return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }

/** SDP・接続ID・任意エラー本文を除き、RTCの会話イベントだけを表示用に変換します。 */
export function normalizeRealtimeBrowserEvent(raw: string, elapsedMs: number): RealtimeStreamEvent[] {
  if (raw.length > maximumFrameCharacters) throw invalid();
  const event = record(JSON.parse(raw));
  if (!event || typeof event.type !== "string" || !knownBrowserEvents.has(event.type)) return [];
  const events: RealtimeStreamEvent[] = [{ type: "event", eventType: event.type, elapsedMs }];
  if (["error", "rtc.call.error"].includes(event.type)) return [...events, { type: "error", error: { code: "GenerationFailed" } }];
  const isInput = ["conversation.item.input_audio_transcription.completed", "conversation.item.input_audio_transcription.delta", "session.input_transcript.delta"].includes(event.type);
  const isOutput = /^(response\.(audio_transcript|output_audio_transcript|text|output_text)\.(delta|done)|session\.output_transcript\.delta)$/.test(event.type);
  const text = event.transcript ?? event.text ?? event.delta;
  if ((isInput || isOutput) && typeof text === "string") { if (text.length > MAX_RUNTIME_STRING_CHARACTERS) throw invalid(); events.push({ type: "transcript", role: isInput ? "input" : "output", text, final: event.type.endsWith(".done") || event.type.endsWith(".completed") }); }
  const response = record(event.response);
  if (event.type === "response.done") {
    if (response?.status === "failed") events.push({ type: "event", eventType: "response.failed", elapsedMs });
    const usage = record(response?.usage);
    if (usage) {
      const values: Record<string, number> = {};
      for (const key of ["input_tokens", "output_tokens", "total_tokens"]) if (Number.isFinite(usage[key]) && Number(usage[key]) >= 0) values[key] = Number(usage[key]);
      for (const key of ["input_token_details", "output_token_details"]) { const details = record(usage[key]); for (const field of ["audio_tokens", "text_tokens", "cached_tokens"]) if (details && Number.isFinite(details[field]) && Number(details[field]) >= 0) values[`${key}.${field}`] = Number(details[field]); }
      if (Object.keys(values).length) events.push({ type: "usage", values });
    }
  }
  return events;
}
