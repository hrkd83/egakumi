import { REALTIME_MAX_AUDIO_BYTES } from "./realtime-request.ts";

/** 音声フレームのBase64・サイズ・PCM境界を線形時間で検査し、即時再生用のバイト列へ戻します。 */
export function decodeRealtimeAudio(value: string): Uint8Array {
  if (!value || value.length % 4 || value.length > Math.ceil(REALTIME_MAX_AUDIO_BYTES / 3) * 4 || /[^A-Za-z0-9+/=]/.test(value)) throw new Error("Invalid audio encoding");
  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  if (value.slice(0, value.length - padding).includes("=")) throw new Error("Invalid audio padding");
  const decoded = atob(value); const bytes = Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  if (!bytes.length || bytes.length % 2 || bytes.length > REALTIME_MAX_AUDIO_BYTES) throw new Error("Invalid PCM frame");
  return bytes;
}
