import { randomBytes } from "node:crypto";
import { REALTIME_MAX_DURATION_MS } from "./realtime-request.ts";

type PrivateOutput = { itemId: string; responseId: string; playbackId: string; samples: number; reported: boolean };
type PendingCorrection = { kind: "truncate" | "delete"; itemId: string; playedMs: number; sent: boolean };
/** 公開する世代と、BFFだけで扱う履歴補正/次応答の制御を分離します。 */
export type RealtimeHistoryObservation = { playbackId?: string; interruptedPlaybackId?: string; discardOutput?: boolean };

/**
 * WSの実再生位置で履歴を補正し、その確認前に次の推論を開始しないための接続内状態です。
 * @remarks 上流item IDは返却せず、他接続・古い世代の入力から補正先を選ばせません。
 * @experimental 作業中の実装です。まだ音声経路へ接続しておらず、統合・検証は未完了です。
 */
export class RealtimeOutputHistory {
  private output?: PrivateOutput;
  private responseId?: string;
  private active = false;
  private interrupted = false;
  private waitingForPosition = false;
  private pending?: PendingCorrection;
  private userTurnPending = false;
  private responseRequested = false;
  private retiredItems = new Set<string>();
  private readonly issueId: () => string;

  constructor(issueId: () => string = () => `p_${randomBytes(16).toString("hex")}`) { this.issueId = issueId; }

  /** 未着の再生位置/上流確認を有限の期限で監視するための、秘密を含まない待機種別です。 */
  get waiting(): "position" | "correction" | undefined { return this.waitingForPosition ? "position" : this.pending ? "correction" : undefined; }

  /** 次の応答が返る前に補正が完了したことを、利用者発話のcommitと合わせて確認します。 */
  takeResponseRequest(): Record<string, unknown> | undefined {
    if (!this.userTurnPending || this.active || this.waiting || this.responseRequested) return;
    this.userTurnPending = false; this.responseRequested = true;
    return { type: "response.create" };
  }

  /** 補正は現在のassistant itemにだけ発行し、音声未生成の中断は確定後にitemを削除します。 */
  takeCorrection(): Record<string, unknown> | undefined {
    const pending = this.pending;
    if (!pending || pending.sent || pending.kind === "delete" && this.active) return;
    pending.sent = true;
    return pending.kind === "delete" ? { type: "conversation.item.delete", item_id: pending.itemId }
      : { type: "conversation.item.truncate", item_id: pending.itemId, content_index: 0, audio_end_ms: pending.playedMs };
  }

  /**
   * opaque世代をこの接続の現在値と照合し、未来の再生位置や重複/古い報告を拒否します。
   * @returns VADによる取消を重ねず、利用者の明示取消時だけresponse.cancelを必要とするか。
   */
  reportPosition(playbackId: string | undefined, playedAudioMs: number | undefined): boolean {
    const output = this.output;
    if (!output) {
      if (playbackId !== undefined || playedAudioMs !== undefined) throw new Error("Invalid playback position");
      this.interrupted = true;
      return this.active;
    }
    if (output.reported || playbackId !== output.playbackId || !Number.isSafeInteger(playedAudioMs)
      || playedAudioMs! < 0 || playedAudioMs! > Math.min(REALTIME_MAX_DURATION_MS, Math.floor(output.samples / 24))) throw new Error("Invalid playback position");
    const cancel = this.active && !this.interrupted;
    this.interrupted = true; this.waitingForPosition = false; output.reported = true;
    this.pending = { kind: output.samples ? "truncate" : "delete", itemId: output.itemId, playedMs: playedAudioMs!, sent: false };
    this.retire(output.itemId);
    return cancel;
  }

  /** 上流の既知イベントを接続内で照合し、破棄すべき古い音声と公開世代だけを返します。 */
  observe(raw: Record<string, unknown>, audioSamples?: number): RealtimeHistoryObservation {
    const type = raw.type;
    if (type === "response.created") {
      const response = raw.response as Record<string, unknown> | undefined;
      const id = this.id(response?.id);
      if (this.waiting || this.active || this.responseId === id) throw new Error("Response started before history correction");
      if (this.output) this.retire(this.output.itemId);
      this.responseId = id; this.output = undefined; this.active = true; this.interrupted = false; this.responseRequested = false;
      return {};
    }
    if (type === "input_audio_buffer.speech_started") {
      this.interrupted = true;
      if (this.output && !this.output.reported) {
        if (this.output.samples) { this.waitingForPosition = true; return { interruptedPlaybackId: this.output.playbackId }; }
        this.output.reported = true; this.pending = { kind: "delete", itemId: this.output.itemId, playedMs: 0, sent: false }; this.retire(this.output.itemId);
      }
      return {};
    }
    if (type === "input_audio_buffer.committed") { this.userTurnPending = true; return {}; }
    if (type === "conversation.item.truncated" || type === "conversation.item.deleted") {
      const pending = this.pending;
      if (!pending?.sent || this.id(raw.item_id) !== pending.itemId || type !== `conversation.item.${pending.kind === "delete" ? "deleted" : "truncated"}`
        || pending.kind === "truncate" && (raw.content_index !== 0 || raw.audio_end_ms !== pending.playedMs)) throw new Error("Unexpected history correction");
      this.pending = undefined; return {};
    }
    if (type === "response.done") {
      const response = raw.response as Record<string, unknown> | undefined;
      if (this.id(response?.id) !== this.responseId) return { discardOutput: true };
      this.active = false;
      if (this.interrupted && !this.output) { this.waitingForPosition = false; }
      return {};
    }
    if (type === "response.output_item.added") {
      if (this.id(raw.response_id) !== this.responseId) return { discardOutput: true };
      const item = raw.item as Record<string, unknown> | undefined;
      if (item?.type !== "message" || item.role !== "assistant") throw new Error("Unexpected assistant item");
      this.ensureOutput(this.id(item.id));
      return {};
    }
    if (typeof type === "string" && /^response\.(?:audio|output_audio|audio_transcript|output_audio_transcript|text|output_text)\./.test(type)) {
      if (this.id(raw.response_id) !== this.responseId) return { discardOutput: true };
      const itemId = this.id(raw.item_id);
      if (this.retiredItems.has(itemId)) return { discardOutput: true };
      const output = this.ensureOutput(itemId);
      if (this.interrupted && !output.reported && !output.samples) {
        output.reported = true; this.pending = { kind: audioSamples ? "truncate" : "delete", itemId, playedMs: 0, sent: false }; this.retire(itemId);
        return { discardOutput: true };
      }
      if (audioSamples !== undefined) {
        if (raw.content_index !== 0 || !Number.isSafeInteger(audioSamples) || audioSamples < 1) throw new Error("Invalid output audio identity");
        output.samples += audioSamples;
        return { playbackId: output.playbackId };
      }
    }
    return {};
  }

  /** 想定したcancelの競合だけを扱うため、現在応答が既に確定した事実を反映します。 */
  cancelAlreadyFinished(): void { this.active = false; }

  private ensureOutput(itemId: string): PrivateOutput {
    if (!this.responseId) throw new Error("Output before response");
    if (this.output && this.output.itemId !== itemId) throw new Error("Unexpected concurrent assistant item");
    if (!this.output) this.output = { itemId, responseId: this.responseId, playbackId: this.issueId(), samples: 0, reported: false };
    return this.output;
  }
  private retire(itemId: string): void { this.retiredItems.add(itemId); if (this.retiredItems.size > 32) this.retiredItems.delete(this.retiredItems.values().next().value!); }
  private id(value: unknown): string { if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(value)) throw new Error("Invalid upstream identity"); return value; }
}
