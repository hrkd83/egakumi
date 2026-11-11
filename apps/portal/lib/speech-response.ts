import { z } from "zod";
import { MAX_SPEECH_AUDIO_BYTES, MAX_SPEECH_RESPONSE_BYTES, speechResultSchema, type SpeechResult } from "./speech-request.ts";
import { ModelResponseError } from "./model-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { SpeechModel } from "./model-types.ts";

/** RIFFチャンクを最後まで検査した、固定PCM形式の情報です。 */
export type SpeechWavInfo = { sampleRate: 16000; bitsPerSample: 16; channels: 1; audioDurationMs: number; byteLength: number };

/** 拡張子やContent-Typeだけに頼らず、PCM形式・RIFF長・音声チャンクの整合性を確認します。 */
export function inspectSpeechWav(bytes: Uint8Array): SpeechWavInfo {
  if (bytes.byteLength < 46 || bytes.byteLength > MAX_SPEECH_AUDIO_BYTES) throw new Error("Invalid WAV size");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (offset: number) => String.fromCharCode(...bytes.subarray(offset, offset + 4));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE" || view.getUint32(4, true) + 8 !== bytes.byteLength) throw new Error("Invalid WAV envelope");
  let offset = 12; let format = false; let dataLength = 0;
  while (offset + 8 <= bytes.byteLength) {
    const name = tag(offset); const length = view.getUint32(offset + 4, true); const end = offset + 8 + length;
    if (end > bytes.byteLength) throw new Error("Invalid WAV chunk");
    if (name === "fmt ") {
      if (format || length < 16 || length > 40 || view.getUint16(offset + 8, true) !== 1
        || view.getUint16(offset + 10, true) !== 1 || view.getUint32(offset + 12, true) !== 16000
        || view.getUint32(offset + 16, true) !== 32000 || view.getUint16(offset + 20, true) !== 2 || view.getUint16(offset + 22, true) !== 16) throw new Error("Unsupported WAV format");
      format = true;
    } else if (name === "data") {
      if (!format || dataLength || length < 2 || length % 2 !== 0) throw new Error("Invalid WAV audio");
      dataLength = length;
    }
    offset = end + length % 2;
  }
  if (!format || !dataLength || offset !== bytes.byteLength) throw new Error("Incomplete WAV");
  return { sampleRate: 16000, bitsPerSample: 16, channels: 1, audioDurationMs: dataLength / 32, byteLength: bytes.byteLength };
}

/** 大きなバイナリにも再帰的な正規表現を使わず、Base64をブラウザーで検証して復元します。 */
export function speechAudioBytes(value: string): Uint8Array {
  if (value.length % 4 !== 0 || value.length > Math.ceil(MAX_SPEECH_AUDIO_BYTES / 3) * 4 || /[^A-Za-z0-9+/=]/.test(value)) throw new Error("Invalid audio encoding");
  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  if (value.slice(0, value.length - padding).includes("=")) throw new Error("Invalid audio padding");
  const decoded = atob(value); const bytes = Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  inspectSpeechWav(bytes);
  return bytes;
}

/** 上流WAVを、音声本文を重複させないPortalの表示契約へ変換します。 */
export function parseSpeechAudio(bytes: Uint8Array, audioBase64: string, model: SpeechModel, durationMs: number, requestId?: string): SpeechResult {
  const info = inspectSpeechWav(bytes);
  return speechResultSchema.parse({ kind: "tts", model: model.id, audioBase64, mimeType: "audio/wav", ...info, durationMs,
    rawResponse: { format: "riff-16khz-16bit-mono-pcm", mimeType: "audio/wav", ...info }, ...(requestId ? { requestId } : {}) });
}

/** Azureの認識状態やOpenAIの追加項目を保ち、確認できた文字起こしだけを取り出します。 */
export function parseSpeechTranscript(value: unknown, model: SpeechModel, durationMs: number, requestId?: string): SpeechResult {
  const raw = z.record(z.string(), z.unknown()).parse(value);
  if (raw.error !== undefined && raw.error !== null) throw new Error("Speech provider error");
  let transcript: string;
  if (model.apiId === "azure-speech-v1") {
    const { RecognitionStatus } = z.object({ RecognitionStatus: z.string().min(1).max(100) }).parse(raw);
    transcript = "";
    if (RecognitionStatus === "Success") {
      const response = z.object({ DisplayText: z.string().optional(), NBest: z.array(z.object({ Display: z.string() })).optional() }).parse(raw);
      const recognized = response.NBest?.[0]?.Display ?? response.DisplayText;
      if (recognized === undefined) throw new Error("Missing recognized speech");
      // 有効な無音で空文字が返る場合も、欠損した応答とは区別して保持します。
      transcript = recognized;
    }
  } else transcript = z.object({ text: z.string() }).parse(raw).text;
  return speechResultSchema.parse({ kind: "stt", model: model.id, transcript, rawResponse: raw, durationMs, ...(requestId ? { requestId } : {}) });
}

/** バイト数・中断・期限を監視し、Content-Lengthがなくても入力・上流本文の読み取りを止めます。 */
export async function readSpeechBody(body: ReadableStream<Uint8Array> | null, maximum: number, signal?: AbortSignal): Promise<Uint8Array> {
  if (!body) throw new Error("Missing speech body");
  const reader = body.getReader(); const chunks: Uint8Array[] = []; let length = 0; let listener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => { if (!signal) return; listener = () => reject(new Error("Speech body aborted")); if (signal.aborted) listener(); else signal.addEventListener("abort", listener, { once: true }); });
  try {
    while (true) { const part = await Promise.race([reader.read(), aborted]); if (part.done) break; length += part.value.byteLength; if (length > maximum) throw new Error("Speech body exceeds limit"); chunks.push(part.value); }
  } finally { if (listener) signal?.removeEventListener("abort", listener); void reader.cancel().catch(() => undefined); reader.releaseLock(); }
  const bytes = new Uint8Array(length); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; } return bytes;
}

const publicErrors = new Set<ModelGatewayErrorCode>(["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"]);
/** 上流の任意メッセージを表示せず、固定された失敗種別だけをUIへ渡します。 */
function publicFailure(value: unknown): TextGenerationFailure {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {}; const error = data.error && typeof data.error === "object" ? data.error as Record<string, unknown> : {};
  return { error: { code: typeof error.code === "string" && publicErrors.has(error.code as ModelGatewayErrorCode) ? error.code as ModelGatewayErrorCode : "GenerationFailed" },
    ...(typeof data.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(data.requestId) ? { requestId: data.requestId } : {}),
    ...(Number.isInteger(data.retryAfterSeconds) && Number(data.retryAfterSeconds) > 0 && Number(data.retryAfterSeconds) <= 3600 ? { retryAfterSeconds: Number(data.retryAfterSeconds) } : {}) };
}

/** BFFの応答もサイズとPCM内容を再検証し、壊れたデータを音声として再生しません。 */
export async function readSpeechResponse(response: Response, signal?: AbortSignal): Promise<SpeechResult> {
  try {
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new Error("Invalid speech content type");
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readSpeechBody(response.body, MAX_SPEECH_RESPONSE_BYTES, signal)));
    if (!response.ok) throw new ModelResponseError(publicFailure(value));
    const result = speechResultSchema.parse(value);
    if (result.kind === "tts") { const info = inspectSpeechWav(speechAudioBytes(result.audioBase64)); if (info.byteLength !== result.byteLength || info.audioDurationMs !== result.audioDurationMs || result.rawResponse.byteLength !== info.byteLength || result.rawResponse.audioDurationMs !== info.audioDurationMs) throw new Error("Inconsistent audio metadata"); }
    return result;
  } catch (error) { if (error instanceof ModelResponseError) throw error; throw new ModelResponseError({ error: { code: "GenerationFailed" } }); }
}
