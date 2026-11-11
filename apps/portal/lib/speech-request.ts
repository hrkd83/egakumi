import { z } from "zod";
import { MAX_BUFFERED_BODY_BYTES, MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";
import { AZURE_SPEECH_TTS_VOICE_IDS } from "./speech-voices.ts";

/** TTS本文の文字数上限です。SSML化後のバイト数もAPIごとに検査します。 */
export const MAX_SPEECH_TEXT_CHARACTERS = MAX_RUNTIME_STRING_CHARACTERS;
/** 音声アップロードの最大値です。API個別の上限が小さい場合はそちらを優先します。 */
export const MAX_SPEECH_UPLOAD_BYTES = MAX_BUFFERED_BODY_BYTES;
/** 読み上げ本文と、許可済みの声・話速を受け付けます。URLや任意SSMLは受け取りません。 */
export const speechInputSchema = z.object({
  modelId: z.string().min(1).max(160),
  text: z.string().max(MAX_SPEECH_TEXT_CHARACTERS).refine((value) => !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/.test(value)),
  voice: z.enum(AZURE_SPEECH_TTS_VOICE_IDS),
  rate: z.enum(["-20%", "0%", "+20%"]),
}).strict();

/** Portalの表示設定です。購読キーの実値は含みません。 */
export type SpeechInput = z.infer<typeof speechInputSchema>;

/** 実行時だけ、本人に認可された購読の台帳IDを指定します。 */
export const speechExecutionSchema = speechInputSchema.extend({ apiKeyId: apiKeyIdSchema });

/** 音声・文字起こしを中継する経路と実行環境の技術上限です。 */
export const MAX_SPEECH_AUDIO_BYTES = MAX_BUFFERED_BODY_BYTES;
export const MAX_SPEECH_JSON_BYTES = MAX_BUFFERED_BODY_BYTES;
export const MAX_SPEECH_RESPONSE_BYTES = MAX_RUNTIME_STRING_CHARACTERS;

const commonResult = {
  model: z.string().min(1).max(160),
  durationMs: z.number().finite().nonnegative(),
  requestId: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/).optional(),
};

/** BFFのJSON契約です。TTSの上流WAVは一度だけBase64にし、rawResponseには形式情報だけを載せます。 */
export const speechResultSchema = z.discriminatedUnion("kind", [
  z.object({
    ...commonResult, kind: z.literal("tts"), audioBase64: z.string().min(60).max(Math.ceil(MAX_SPEECH_AUDIO_BYTES / 3) * 4),
    mimeType: z.literal("audio/wav"), sampleRate: z.literal(16000), bitsPerSample: z.literal(16), channels: z.literal(1),
    audioDurationMs: z.number().finite().positive(), byteLength: z.number().int().positive().max(MAX_SPEECH_AUDIO_BYTES),
    rawResponse: z.object({ format: z.literal("riff-16khz-16bit-mono-pcm"), mimeType: z.literal("audio/wav"), sampleRate: z.literal(16000), bitsPerSample: z.literal(16), channels: z.literal(1), audioDurationMs: z.number().finite().positive(), byteLength: z.number().int().positive().max(MAX_SPEECH_AUDIO_BYTES) }).strict(),
  }).strict(),
  z.object({ ...commonResult, kind: z.literal("stt"), transcript: z.string(), rawResponse: z.record(z.string(), z.unknown()) }).strict(),
]);

/** 再生用の検証済み音声、または文字起こし本文と実JSONのどちらかです。 */
export type SpeechResult = z.infer<typeof speechResultSchema>;
