/** Voice Liveで選べる、日本語の標準Neural音声です。REST TTSの代表選択肢とは分けます。 */
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

/** REST TTSで試せる代表的な標準Neural音声です。Azure Speechの全音声一覧ではありません。 */
export const AZURE_SPEECH_TTS_VOICES = [
  ...AZURE_SPEECH_VOICES.map((voice) => ({ ...voice, locale: "ja-JP" as const, label: { ja: `日本語 · ${voice.label.ja}`, en: `Japanese · ${voice.label.en}` } })),
  { id: "en-US-JennyNeural", locale: "en-US", label: { ja: "英語（米国） · Jenny（女性）", en: "English (US) · Jenny (female)" } },
  { id: "en-US-GuyNeural", locale: "en-US", label: { ja: "英語（米国） · Guy（男性）", en: "English (US) · Guy (male)" } },
  { id: "zh-CN-XiaoyanNeural", locale: "zh-CN", label: { ja: "中国語（標準・簡体字） · Xiaoyan（女性）", en: "Chinese (Mandarin) · Xiaoyan (female)" } },
  { id: "ko-KR-SunHiNeural", locale: "ko-KR", label: { ja: "韓国語 · SunHi（女性）", en: "Korean · SunHi (female)" } },
  { id: "es-ES-ElviraNeural", locale: "es-ES", label: { ja: "スペイン語（スペイン） · Elvira（女性）", en: "Spanish (Spain) · Elvira (female)" } },
] as const;

/** REST TTSの入力検査と送信で共通に使う、代表音声の識別子です。 */
export type AzureSpeechTtsVoiceId = (typeof AZURE_SPEECH_TTS_VOICES)[number]["id"];
/** 表示メタデータと入力の許可一覧を一致させます。 */
export const AZURE_SPEECH_TTS_VOICE_IDS: readonly AzureSpeechTtsVoiceId[] = AZURE_SPEECH_TTS_VOICES.map((voice) => voice.id);
