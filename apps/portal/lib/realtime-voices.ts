import type { RealtimeModel } from "./model-types.ts";
import { AZURE_SPEECH_VOICES } from "./speech-voices.ts";

/** Azure Realtimeの公式仕様で確認した音声です。既定のMarinを先頭に表示します。 */
const realtimeVoices = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"].map((id) => {
  const name = id[0].toUpperCase() + id.slice(1);
  return { id, label: { ja: name, en: name } };
});

/** 開始時に選べる、音声サービスごとの公開音声名だけを返します。 */
export function getRealtimeVoiceOptions(model: Pick<RealtimeModel, "family">): readonly { id: string; label: { ja: string; en: string } }[] {
  if (model.family === "transcription") return [];
  if (model.family === "voice-live") return AZURE_SPEECH_VOICES;
  // Azure GPT-Liveで確認できた既定音声を使い、直販APIの音声一覧を流用しません。
  if (model.family === "live") return realtimeVoices.filter((voice) => voice.id === "marin");
  return realtimeVoices;
}

/** 選択音声を系列の許可一覧に照合し、文字起こしへの音声指定は拒否します。 */
export function resolveRealtimeVoice(model: RealtimeModel, input: { modelId: string; voice?: string }): string | undefined {
  if (input.modelId !== model.id) throw new Error("InvalidRequest");
  if (model.family === "transcription") {
    if (input.voice !== undefined) throw new Error("InvalidRequest");
    return undefined;
  }
  const voice = input.voice ?? model.voice;
  if (model.family === "live" ? !/^[A-Za-z0-9:._-]{1,150}$/.test(voice) : !getRealtimeVoiceOptions(model).some((entry) => entry.id === voice)) throw new Error("InvalidRequest");
  return voice;
}
