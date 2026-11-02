import { z } from "zod";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";
import type { RealtimeModel } from "./model-types.ts";

/** 開始時のモデル・音声・応答上限だけを受け取り、接続先や上流イベントの任意入力を拒否します。 */
export const realtimeInputSchema = z.object({ modelId: z.string().min(1).max(200), voice: z.string().regex(/^[A-Za-z0-9:._-]{1,150}$/).optional(), maxOutputTokens: z.union([z.number().int().min(1).max(1_000_000), z.literal("model-max")]).optional() }).strict();
/** リアルタイム音声の表示設定です。認証情報は保持しません。 */
export type RealtimeInput = z.infer<typeof realtimeInputSchema>;

/** モデル能力とAPIの数値指定範囲を照合し、上流に指定できる値へ変換します。 */
export function resolveRealtimeOutputTokens(model: RealtimeModel, input: RealtimeInput): number | "inf" | undefined {
  if (input.modelId !== model.id) throw new Error("InvalidRequest");
  if (model.family === "live" || model.family === "transcription") {
    if (input.maxOutputTokens !== undefined) throw new Error("InvalidRequest");
    return undefined;
  }
  if (input.maxOutputTokens === "model-max") {
    if (model.family !== "voice-live" || !model.supportsMaximumOutput || !model.maxOutputTokens) throw new Error("InvalidRequest");
    return "inf";
  }
  const upper = model.numericMaxOutputTokens ?? model.maxOutputTokens;
  if (!upper || typeof input.maxOutputTokens !== "number" || !Number.isInteger(input.maxOutputTokens) || input.maxOutputTokens < 1 || input.maxOutputTokens > upper) throw new Error("InvalidRequest");
  return input.maxOutputTokens;
}
/** 実行時には選択した購読の台帳IDを付け、キーの実値はBFFで解決します。 */
export const realtimeExecutionSchema = realtimeInputSchema.extend({ apiKeyId: apiKeyIdSchema });
/** WebRTCはブラウザーが生成した音声専用SDPを追加します。SDPはログへ出しません。 */
export const realtimeWebRtcExecutionSchema = realtimeExecutionSchema.extend({ sdp: z.string().min(20).max(120_000) });
/** 終了の再確認には、本人に束縛された暗号化参照だけを受け取ります。 */
export const realtimeStopSchema = z.object({ handle: z.string().min(40).max(16_000) }).strict();

/** APIMの10分の所有チケット内で停止を確認するため、会話は最大9分で解放します。 */
export const REALTIME_MAX_DURATION_MS = 540_000;
/** PCM24kHz・16bit・モノラルを9分流せる累積上限です。音声を全量保持するための値ではありません。 */
export const REALTIME_MAX_AUDIO_BYTES = 25_920_000;
/** 双方向の会話で使う累積転送量とイベント数の上限です。個別フレームには別の小さい上限を設けます。 */
export const REALTIME_MAX_STREAM_BYTES = 100_663_296;
export const REALTIME_MAX_EVENTS = 50_000;

const usageSchema = z.record(z.string().regex(/^[a-zA-Z0-9_.-]{1,80}$/), z.number().finite().nonnegative());
const errorCodeSchema = z.enum(["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"]);
/** BFFとブラウザーの共通ストリーム契約です。上流の任意イベント・キー・チケットを直接転送しません。 */
export const realtimeStreamEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("status"), stage: z.enum(["connecting", "connected", "sending", "receiving", "stopping"]), elapsedMs: z.number().nonnegative(), maximumDurationMs: z.number().int().positive().max(REALTIME_MAX_DURATION_MS).optional() }).strict(),
  z.object({ type: z.literal("event"), eventType: z.string().regex(/^[a-zA-Z0-9_.-]{1,100}$/), elapsedMs: z.number().nonnegative() }).strict(),
  z.object({ type: z.literal("transcript"), role: z.enum(["input", "output"]), text: z.string().max(16_000), final: z.boolean() }).strict(),
  z.object({ type: z.literal("usage"), values: usageSchema }).strict(),
  z.object({ type: z.literal("audio"), delta: z.string().max(350_000), sampleRate: z.literal(24000) }).strict(),
  z.object({ type: z.literal("answer"), sdp: z.string().min(20).max(120_000), handle: z.string().max(16_000).optional() }).strict(),
  z.object({ type: z.literal("complete"), durationMs: z.number().nonnegative(), inputAudioBytes: z.number().int().nonnegative(), outputAudioBytes: z.number().int().nonnegative(), stopStatus: z.enum(["confirmed", "transport-closed", "unconfirmed"]), handle: z.string().max(16_000).optional() }).strict(),
  z.object({ type: z.literal("error"), error: z.object({ code: errorCodeSchema }).strict(), requestId: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/).optional(), handle: z.string().max(16_000).optional() }).strict(),
]);
/** 画面へ公開できる進捗・音声・終了結果のいずれかです。 */
export type RealtimeStreamEvent = z.infer<typeof realtimeStreamEventSchema>;
