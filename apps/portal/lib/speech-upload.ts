import type { SpeechModel } from "./model-types.ts";
import { inspectSpeechWav } from "./speech-response.ts";

/** Azure OpenAI文字起こしのネイティブなファイル容量です。multipartの境界部分は含みません。 */
export const MAX_TRANSCRIPTION_FILE_BYTES = 25_000_000;
/** OpenAI文字起こしが受け付ける音声・動画コンテナーです。 */
export const TRANSCRIPTION_FILE_ACCEPT = ".wav,.mp3,.mp4,.mpeg,.mpga,.m4a,.webm,audio/*,video/mp4,video/webm";

/** サービスごとに形式を確認し、アップロードされた名前を上流の任意パスへ使いません。 */
export function inspectSpeechUpload(bytes: Uint8Array, model: Pick<SpeechModel, "apiId">): { mediaType: string; filename: string } {
  if (model.apiId !== "openai-audio-v1") { inspectSpeechWav(bytes); return { mediaType: "audio/wav", filename: "input.wav" }; }
  if (!bytes.length || bytes.length > MAX_TRANSCRIPTION_FILE_BYTES) throw new Error("Invalid transcription file size");
  const tag = (offset: number, length: number) => String.fromCharCode(...bytes.subarray(offset, offset + length));
  if (tag(0, 4) === "RIFF" && tag(8, 4) === "WAVE") return { mediaType: "audio/wav", filename: "input.wav" };
  if (tag(0, 3) === "ID3" || bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0) return { mediaType: "audio/mpeg", filename: "input.mp3" };
  if (tag(4, 4) === "ftyp") return { mediaType: "audio/mp4", filename: "input.m4a" };
  if (bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3) return { mediaType: "audio/webm", filename: "input.webm" };
  if (bytes[0] === 0 && bytes[1] === 0 && bytes[2] === 1 && [0xba, 0xb3].includes(bytes[3])) return { mediaType: "audio/mpeg", filename: "input.mpeg" };
  throw new Error("Unsupported transcription file");
}
