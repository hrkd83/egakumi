import type { RealtimeModel } from "./model-types.ts";
import { decodeRealtimeAudio } from "./realtime-audio.ts";
import { REALTIME_MAX_AUDIO_BYTES, REALTIME_MAX_EVENTS, resolveRealtimeOutputTokens, type RealtimeInput, type RealtimeStreamEvent } from "./realtime-request.ts";

/** マイクからの自然な会話に応じるための共通指示です。外部操作や固定の返答を要求しません。 */
export const REALTIME_CONVERSATION_INSTRUCTIONS = "日本語で自然に会話してください。利用者の発話を聞き、簡潔で分かりやすく応答してください。ツールの呼び出しや外部の操作は行わないでください。";

/** 発話区間をサービス側で検知し、次の発話まで接続を保つための設定です。 */
const voiceActivityDetection = { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 500 };

/** 製品固有の開始設定です。WSSのPCMとWebRTCのSDP交渉を混同させません。 */
export function buildRealtimeSessionEvent(model: RealtimeModel, input: RealtimeInput): Record<string, unknown> | null {
  const outputTokens = resolveRealtimeOutputTokens(model, input);
  if (model.transport === "webrtc" && model.family !== "realtime") return null;
  if (model.family === "live") return { type: "session.start", session: { model: model.modelId, instructions: REALTIME_CONVERSATION_INSTRUCTIONS, audio: { output: { voice: model.voice } }, delegation: { type: "client" } } };
  if (model.family === "voice-live") return { type: "session.update", session: { modalities: ["text", "audio"], instructions: REALTIME_CONVERSATION_INSTRUCTIONS, input_audio_format: "pcm16", output_audio_format: "pcm16", input_audio_sampling_rate: 24000, voice: { type: "azure-standard", name: model.voice }, turn_detection: voiceActivityDetection, max_response_output_tokens: outputTokens, tools: [] } };
  // gpt-live-transcribeはturn detectionをサポートしません。公式例に合わせ、
  // 継続入力をbridgeが約300msずつcommitし、認識確定後も接続を維持します。
  if (model.family === "transcription") return { type: "session.update", session: { type: "transcription", audio: { input: { format: { type: "audio/pcm", rate: 24000 }, turn_detection: null, transcription: { model: model.modelId, language: "ja", delay: "medium" } } } } };
  // WebRTCのコーデックはSDPに任せ、WSSにだけヘッダーなしPCMを指定します。
  const format = model.transport === "websocket" ? { format: { type: "audio/pcm", rate: 24000 } } : {};
  return { type: "session.update", session: { type: "realtime", output_modalities: ["audio"], instructions: REALTIME_CONVERSATION_INSTRUCTIONS, max_output_tokens: outputTokens, tools: [], audio: { input: { ...format, turn_detection: { ...voiceActivityDetection, create_response: true, interrupt_response: true } }, output: { ...format, voice: model.voice } } } };
}

/** 任意のオブジェクトを転送せず、既知の形式の非負数だけを利用量として公開します。 */
export function realtimeUsage(value: unknown): Record<string, number> {
  const result: Record<string, number> = {};
  const visit = (item: unknown, prefix = "", depth = 0) => {
    if (!item || typeof item !== "object" || Array.isArray(item) || depth > 3) return;
    for (const [key, child] of Object.entries(item)) {
      const name = prefix ? `${prefix}.${key}` : key;
      if (!/^[a-zA-Z0-9_.-]{1,80}$/.test(name) || Object.keys(result).length >= 60) continue;
      if (typeof child === "number" && Number.isFinite(child) && child >= 0) result[name] = child;
      else visit(child, name, depth + 1);
    }
  };
  visit(value); return result;
}

/** UIへ公開できるイベント名です。ID・セッション全体・生のエラーは含めません。 */
const publicEventTypes = new Set(["session.created", "session.updated", "session.started", "session.closed", "session.usage.updated", "input_audio_buffer.committed", "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped", "response.created", "response.done", "response.audio.delta", "response.audio.done", "response.output_audio.delta", "response.output_audio.done", "response.audio_transcript.delta", "response.audio_transcript.done", "response.output_audio_transcript.delta", "response.output_audio_transcript.done", "conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed", "session.output_audio.delta", "session.input_transcript.delta", "session.output_transcript.delta"]);

/** 一つの発話が終わっても接続を維持する、継続会話のプロトコル検証です。 */
export class RealtimeProtocol {
  readonly model: RealtimeModel;
  ready = false; terminal = false; responseActive = false; outputAudioBytes = 0; eventCount = 0;
  private committedItems = new Set<string>();
  private completedItems = new Set<string>();
  private pendingTranscripts = new Map<string, { text: string; final: boolean }[]>();
  private pendingCharacters = 0;
  private outputCharacters = 0;
  constructor(model: RealtimeModel) { this.model = model; }

  /** 会話の複数turnを検査し、サービス終了と発話完了を別の状態として扱います。 */
  accept(raw: unknown, elapsedMs: number): RealtimeStreamEvent[] {
    if (++this.eventCount > REALTIME_MAX_EVENTS || !raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Invalid service event");
    const event = raw as Record<string, unknown>; const type = event.type;
    if (typeof type !== "string" || type.length > 100) throw new Error("Missing service event type");
    if (type === "error" || type === "session.error" || type === "conversation.item.input_audio_transcription.failed") throw new Error("Service rejected operation");
    const events: RealtimeStreamEvent[] = publicEventTypes.has(type) ? [{ type: "event", eventType: type, elapsedMs }] : [];
    const ack = this.model.family === "live" ? "session.started" : "session.updated";
    if (type === ack) {
      if (!event.session || typeof event.session !== "object" || Array.isArray(event.session)) throw new Error("Invalid session acknowledgement");
      const resource = event.session as Record<string, unknown>;
      if (resource.model !== undefined && resource.model !== this.model.modelId) throw new Error("Unexpected model");
      this.ready = true;
    }
    if (type === "response.created") { this.responseActive = true; this.outputCharacters = 0; }
    if (type === "input_audio_buffer.committed" && this.model.family === "transcription") {
      const item = this.itemId(event);
      if (this.committedItems.has(item) || this.completedItems.has(item) || this.committedItems.size >= 100) throw new Error("Invalid committed item");
      this.committedItems.add(item);
      for (const pending of this.pendingTranscripts.get(item) ?? []) {
        events.push({ type: "transcript", role: "input", text: pending.text, final: pending.final });
        this.pendingCharacters -= pending.text.length;
        if (pending.final) this.completeTranscription(item, pending.text);
      }
      this.pendingTranscripts.delete(item);
    }
    const isAudio = this.model.family === "live" ? type === "session.output_audio.delta" : type === "response.audio.delta" || type === "response.output_audio.delta";
    if (isAudio) {
      if (!this.ready || this.model.family === "transcription" || typeof event.delta !== "string") throw new Error("Unexpected audio");
      const bytes = decodeRealtimeAudio(event.delta); this.outputAudioBytes += bytes.length;
      if (bytes.length > 250_000 || this.outputAudioBytes > REALTIME_MAX_AUDIO_BYTES) throw new Error("Output audio exceeds limit");
      events.push({ type: "audio", delta: event.delta, sampleRate: 24000 });
    }
    const input = type === "conversation.item.input_audio_transcription.delta" || type === "conversation.item.input_audio_transcription.completed" || type === "session.input_transcript.delta";
    const output = type === "session.output_transcript.delta" || type === "response.audio_transcript.delta" || type === "response.audio_transcript.done" || type === "response.output_audio_transcript.delta" || type === "response.output_audio_transcript.done";
    if (input || output) {
      if (!this.ready) throw new Error("Transcript before session ready");
      const final = type.endsWith(".completed") || type.endsWith(".done"); const text = final ? event.transcript : event.delta;
      if (typeof text !== "string" || text.length > 16_000) throw new Error("Invalid transcript");
      if (this.model.family === "transcription") {
        const item = this.itemId(event);
        if (event.content_index !== 0 || this.completedItems.has(item)) throw new Error("Invalid transcription identity");
        // VADのcommit受付より先の認識は小さく保留し、同じitemの受付後だけ公開します。
        if (!this.committedItems.has(item)) {
          this.pendingCharacters += text.length;
          if (this.pendingCharacters > 32_000 || !this.pendingTranscripts.has(item) && this.pendingTranscripts.size >= 8) throw new Error("Pending transcription exceeds limit");
          const pending = this.pendingTranscripts.get(item) ?? []; if (pending.length >= 100) throw new Error("Too many pending transcript events");
          pending.push({ text, final }); this.pendingTranscripts.set(item, pending); return events;
        }
        if (final) this.completeTranscription(item, text);
      }
      if (output && this.model.family !== "live") { this.outputCharacters = final ? text.length : this.outputCharacters + text.length; if (this.outputCharacters > 16_000) throw new Error("Output transcript exceeds limit"); }
      events.push({ type: "transcript", role: input ? "input" : "output", text, final });
    }
    if (type === "response.done" && this.model.family !== "live" && this.model.family !== "transcription") {
      const response = event.response as Record<string, unknown> | undefined;
      if (!response || !["completed", "cancelled", "incomplete", "failed"].includes(String(response.status))) throw new Error("Invalid turn completion");
      this.responseActive = false;
      // 取消・出力枠不足も一回の応答の状態です。セッション終了にはしません。
      events.push({ type: "event", eventType: `response.${String(response.status)}`, elapsedMs });
      const usage = realtimeUsage(response.usage); if (Object.keys(usage).length) events.push({ type: "usage", values: usage });
    }
    if (type === "session.usage.updated" && this.model.family === "live") { const usage = realtimeUsage(event.usage); if (Object.keys(usage).length) events.push({ type: "usage", values: usage }); }
    if (type === "session.closed" && this.model.family === "live") { if (!this.ready) throw new Error("Live closed before startup"); this.terminal = true; const usage = realtimeUsage(event.usage); if (Object.keys(usage).length) events.push({ type: "usage", values: usage }); }
    return events;
  }

  /** 本文とは別に保持する相関IDを検査し、クライアントへは公開しません。 */
  private itemId(event: Record<string, unknown>): string { if (typeof event.item_id !== "string" || !/^[a-zA-Z0-9_-]{1,200}$/.test(event.item_id)) throw new Error("Invalid input identity"); return event.item_id; }
  /** 確定した発話の状態だけを閉じます。認識完了後も次の発話を受け付けます。 */
  private completeTranscription(item: string, text: string): void { if (this.completedItems.has(item) || this.completedItems.size >= 3000 || text.length > 16000) throw new Error("Invalid completed transcription"); this.committedItems.delete(item); this.completedItems.add(item); }
}
