/** 日本語の標準Neural音声です。TTSとVoice Liveで同じ許可名・表示名を使います。 */
export const AZURE_SPEECH_VOICES = [
  { id: "ja-JP-NanamiNeural", label: { ja: "Nanami（女性）", en: "Nanami (female)" } },
  { id: "ja-JP-KeitaNeural", label: { ja: "Keita（男性）", en: "Keita (male)" } },
  { id: "ja-JP-AoiNeural", label: { ja: "Aoi（女性）", en: "Aoi (female)" } },
  { id: "ja-JP-DaichiNeural", label: { ja: "Daichi（男性）", en: "Daichi (male)" } },
  { id: "ja-JP-MayuNeural", label: { ja: "Mayu（女性）", en: "Mayu (female)" } },
  { id: "ja-JP-NaokiNeural", label: { ja: "Naoki（男性）", en: "Naoki (male)" } },
  { id: "ja-JP-ShioriNeural", label: { ja: "Shiori（女性）", en: "Shiori (female)" } },
] as const;

/** 入力検査と送信に使う、許可された標準Neural音声の識別子です。 */
export type AzureSpeechVoiceId = (typeof AZURE_SPEECH_VOICES)[number]["id"];

/** 表示用メタデータと異なる音声名を入力スキーマへ登録しないための共通一覧です。 */
export const AZURE_SPEECH_VOICE_IDS: readonly AzureSpeechVoiceId[] = AZURE_SPEECH_VOICES.map((voice) => voice.id);
