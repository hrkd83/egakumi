import type { Locale } from "./config.ts";

const ja = {
  guideTitle: "音声APIをアプリに組み込む", guideSelect: "上の選択欄で操作とモデルを選び、利用するAPIの送信形式を確認します。", guideTts: "読み上げはテキストをSSMLへ変換して送信し、返ったWAVを再生または保存します。入力文はXMLとして安全にエスケープします。", guideStt: "文字起こしは対応するWAVを用意し、Azure Speechには音声バイナリ、OpenAIにはfileフィールドのmultipartを送ります。応答のJSONから認識文を読み取ります。", guideTry: "「試す」では利用できるAPIキーと入力を選び、右側で実際の応答を確認できます。読み上げ（TTS）では音声名も選べます。以下のコード例は入力設定に合わせて更新されます。",
  introduction: "選択した音声ファイルの文字起こし（STT）と、入力文の読み上げ（TTS）を試し、モデルや設定ごとの応答を確認します。",
  parameters: "音声APIの設定", operation: "操作・モデル", stt: "文字起こし（STT）", tts: "読み上げ（TTS）",
  voice: "読み上げる声", voiceHelp: "このPortalでは、日本語・英語などの代表的な標準Neural音声12種を選べます。Azure Speechの全音声は公式資料で確認してください。利用できる音声はリージョンによって異なります。声を変えても入力文は保持し、前の生成音声は消えます。", voiceListDocs: "音声の全一覧（Microsoft公式）", rate: "読み上げ速度", slower: "ゆっくり（−20%）", normal: "標準", faster: "速く（＋20%）",
  inputLanguage: "入力言語", japanese: "日本語（ja-JP）", languageNotice: "Azure Speechの認識言語はAPI定義側で日本語に設定されています。", openaiLanguageNotice: "言語パラメーターは送らず、モデルに音声の言語を認識させます。",
  text: "読み上げるテキスト", file: "文字起こしする音声ファイル", fileHelp: "WAV・PCM・16 kHz・16 bit・モノラル、最大{size} MiB。ファイルの内容も検証します。", fileChecking: "音声ファイルを確認しています…", fileInvalid: "対応するWAV形式か、ファイルサイズを確認してください。", sampleDownload: "サンプルWAVをダウンロード", sampleNotice: "サンプルが必要な場合はWAVをダウンロードし、上の欄で選択してください。実行時には選択したファイルを送ります。",
  sampleFormat: "WAV · PCM · 16 kHz · 16 bit · モノラル", fixedNotice: "入力文・音声・話速の変更は、SSMLと各言語の組み込み例にも反映されます。声やモデルを変えても入力文は保持します。",
  formatNotice: "出力形式はAPI定義側で固定されたWAV（16 kHz・16 bit・モノラル）です。速度はSSMLのprosodyで指定します。",
  runStt: "音声を文字起こしして確認", runTts: "音声を生成して確認", runningStt: "文字起こししています…", runningTts: "音声を生成しています…",
  wait: "最大180秒待機します。中止しても、送信済みの処理と課金が取り消されるとは限りません。自動再試行は行いません。",
  responseEmpty: "設定とAPIキーを選んで実行すると、文字起こし結果や生成音声と、実際の応答形式を確認できます。",
  body: "送信内容", ssml: "SSML本文", binary: "音声バイナリ／フォーム", headers: "リクエストヘッダー", integrationLanguage: "組み込み例の言語",
  backend: "各コード例はアプリケーションのバックエンドで実行します。GATEWAY_API_KEYを環境変数に設定し、ブラウザーへ渡さないでください。",
  binaryNotice: "送信内容の表示は、音声バイナリやmultipart/form-dataを説明するための記述です。このJSON自体をAPIへ送るものではありません。",
  multipartNotice: "OpenAIには音声ファイルをmultipart/form-dataで送ります。境界文字を含むContent-TypeはHTTPライブラリに生成させ、手書きしません。",
  schema: "パネルが利用する部分スキーマです。TTSの実際の応答は音声バイナリです。メタデータはPortalが検証済みWAVから読み取った値で、上流のJSON応答ではありません。",
  requestSchema: "リクエストの形式・スキーマ", responseSchema: "応答の形式・スキーマ", rawResponse: "実際の応答JSON", audioMetadata: "生成音声のメタデータ", transcript: "文字起こし結果",
  noTranscript: "この音声では認識できた発話がありませんでした。詳細は応答JSONを確認してください。", generatedAudio: "生成された音声", download: "WAVをダウンロード",
  duration: "API処理時間", audioDuration: "音声の長さ", bytes: "音声サイズ", binaryResponse: "上流の応答: audio/wav（バイナリ）", noAutoplay: "再生ボタンを押すと音声を確認できます。自動再生は行いません。",
  sources: "Microsoft公式のAPI仕様", ttsDocs: "Speech テキスト読み上げ", sttDocs: "Speech 短い音声の認識", openaiDocs: "Azure OpenAI 音声文字起こし",
};

/** 音声画面の翻訳項目です。日本語と英語の項目を揃えます。 */
export type SpeechDictionary = { [K in keyof typeof ja]: string };

const en: SpeechDictionary = {
  guideTitle: "Integrate speech into your application", guideSelect: "Select an operation and model above, then review the request format for that API.", guideTts: "For synthesis, convert text to SSML, send it, and play or save the returned WAV. Escape user text safely as XML.", guideStt: "For transcription, prepare a supported WAV. Send audio binary to Azure Speech or a multipart file field to OpenAI, then read the transcript from the response JSON.", guideTry: "Open Try it to select an available API key and your input, then inspect the real response alongside the settings. Text to speech (TTS) also lets you choose a voice. The examples below update with your inputs.",
  introduction: "Transcribe a selected audio file or synthesize your text, and compare responses across models and settings.",
  parameters: "Speech API parameters", operation: "Operation / model", stt: "Speech to text (STT)", tts: "Text to speech (TTS)",
  voice: "Voice", voiceHelp: "This Portal offers a representative subset of 12 standard Neural voices across Japanese, English and other languages. See the official list for all Azure Speech voices. Availability varies by region. Changing voices preserves your text and clears the previous audio.", voiceListDocs: "Full voice list (Microsoft)", rate: "Speaking rate", slower: "Slower (−20%)", normal: "Normal", faster: "Faster (+20%)",
  inputLanguage: "Input language", japanese: "Japanese (ja-JP)", languageNotice: "The Azure Speech recognition language is configured as Japanese in the API definition.", openaiLanguageNotice: "No language parameter is sent; the model recognizes the audio language.",
  text: "Text to synthesize", file: "Audio file to transcribe", fileHelp: "WAV, PCM, 16 kHz, 16-bit mono, up to {size} MiB. File contents are also validated.", fileChecking: "Checking audio file…", fileInvalid: "Check the WAV format and file size.", sampleDownload: "Download sample WAV", sampleNotice: "Download the sample if needed, then select it above. Requests send your selected file.",
  sampleFormat: "WAV · PCM · 16 kHz · 16 bit · Mono", fixedNotice: "Changes to the text, voice, and rate update the SSML and integration examples. Your text is preserved when switching voices or models.",
  formatNotice: "The API definition fixes the output to a mono, 16 kHz, 16-bit WAV file. Speaking rate is sent through the SSML prosody element.",
  runStt: "Transcribe and inspect", runTts: "Generate and inspect speech", runningStt: "Transcribing…", runningTts: "Generating speech…",
  wait: "Requests wait for up to 180 seconds. Cancelling does not guarantee cancellation of processing or charges. Requests are never retried automatically.",
  responseEmpty: "Select parameters and an API key, then run to inspect the transcription or generated audio and its actual response format.",
  body: "Request body", ssml: "SSML body", binary: "Audio binary / form", headers: "Request headers", integrationLanguage: "Integration example language",
  backend: "Run each example in your application backend. Set GATEWAY_API_KEY as an environment variable and never expose it to the browser.",
  binaryNotice: "The body preview describes the binary or multipart/form-data request. Do not send this descriptive JSON itself to the API.",
  multipartNotice: "OpenAI receives the audio file as multipart/form-data. Let your HTTP library generate Content-Type and its boundary instead of writing them manually.",
  schema: "These are partial schemas used by this panel. TTS actually returns audio binary. Metadata is extracted from the validated WAV by the portal; it is not an upstream JSON response.",
  requestSchema: "Request format / schema", responseSchema: "Response format / schema", rawResponse: "Actual response JSON", audioMetadata: "Generated audio metadata", transcript: "Transcription",
  noTranscript: "No speech was recognized in this audio. Inspect the response JSON for details.", generatedAudio: "Generated speech", download: "Download WAV",
  duration: "API duration", audioDuration: "Audio duration", bytes: "Audio size", binaryResponse: "Upstream response: audio/wav (binary)", noAutoplay: "Press play to listen. Audio never plays automatically.",
  sources: "Microsoft API documentation", ttsDocs: "Speech text to speech", sttDocs: "Speech short audio recognition", openaiDocs: "Azure OpenAI transcription",
};

/** 公開環境値や秘密を含まない、音声パネル用の翻訳を選びます。 */
export function getSpeechDictionary(locale: Locale): SpeechDictionary { return locale === "en" ? en : ja; }
