import type { Locale } from "./config.ts";
import type { RealtimeModel } from "../model-types.ts";

const ja = {
  guideTitle: "接続から会話の終了まで", guideConversation: "接続後は発話検知に合わせて応答を受信し、同じ接続で会話を続けます。終了時はマイクと音声接続を解放し、対応するAPIではサービス側の停止も確認します。", guideTry: "「試す」ではAPIキーと出力設定を選び、マイクで会話できます。利用ガイドを表示しただけではマイクは起動しません。接続中は同じ画面に停止操作を表示します。",
  introduction: "マイクでAIとリアルタイムに会話し、アプリに組み込む際の接続・イベント・字幕を確認します。応答が終わっても接続は続き、利用者の終了操作でマイクと接続を閉じます。",
  parameters: "リアルタイム音声の設定", model: "モデル", transport: "接続方式", transportHelp: "選択したモデルが対応する接続方式だけを表示します。", websocket: "WebSocket", webrtc: "WebRTC", realtime: "GPT Realtime", live: "GPT-Live", transcription: "GPT Live Transcribe", voiceLive: "Voice Live + GPT",
  voice: "応答の声", voiceHelp: "開始前に声を選べます。接続中は変更できません。別の声を試すときは、接続を終了してから選び直してください。", fixedVoice: "このシリーズでは{voice}を使用します。",
  maxOutputTokens: "1応答の出力トークン上限", tokenRange: "数値の入力範囲: 1〜{maximum}", tokenHelp: "この値は1回の応答に適用されます。", modelMaximum: "モデル最大（{count}トークン）", modelMaximumHelp: "数値指定の上限とモデル本来の上限が異なるため、モデル最大は専用の指定で送信します。", transcriptionTokens: "文字起こし専用APIです。マイクからの発話を継続して文字起こしし、AIの音声応答は返しません。", liveTokens: "このLive APIの接続契約には出力トークンの設定がないため、変更できません。",
  start: "マイクで会話を開始", startTranscription: "マイクの文字起こしを開始", starting: "マイクと接続を準備中…", stop: "接続を終了", stopping: "マイクと接続を終了中…", mute: "マイクをミュート", unmute: "ミュートを解除", muted: "マイクはミュート中です。", interrupt: "応答を止める", play: "音声を再生する", playbackBlocked: "ブラウザーが音声再生を保留しています。「音声を再生する」を押してください。",
  permission: "開始するとブラウザーがマイクの許可を求めます。接続後は発話を音声サービスへ送り、AIの応答をスピーカーで再生します。ヘッドセットの使用をおすすめします。",
  privacy: "会話音声の録音・保存は行いません。字幕とイベント履歴はこの画面のメモリー内だけに保持します。タブを閉じる際も、先に「会話を終了」を押してください。",
  limit: "接続上限はサーバー設定に従います（現在最大{minutes}分）。応答ごとの自動終了や自動再接続は行いません。準備は最大90秒待機します。",
  mediaNotice: "WebRTCの認証・制御はPortalのバックエンドを通り、音声はブラウザーと音声サービスの間を流れます。サブスクキーはバックエンドで保管します。",
  wsNotice: "WebSocketはマイクの音声を100ms単位の24kHz PCMへ変換し、PortalのバックエンドからGatewayへ中継します。返った音声は短い再生キューだけに保持します。",
  connecting: "Gatewayへ接続中", connected: "接続中です。マイクに話しかけてください。", sending: "マイクの音声を送信中", receiving: "AIの応答を受信中", conversation: "会話の字幕", noConversation: "接続して話しかけると、利用できる字幕がここに表示されます。", user: "あなた", assistant: "AI", partial: "発話中", partialEnded: "途中までの字幕", transcriptNotice: "字幕の提供範囲はAPIによって異なります。認識されていない発話や、APIが字幕を返さない音声は表示されません。",
  ended: "会話を終了しました", confirmed: "正常終了／停止を確認", transportClosed: "音声通信・制御接続を終了", unconfirmed: "サービス側の停止は未確認", stopHelp: "端末のマイク停止とサービス側の停止確認は別々に扱います。停止状態は最終課金の確定を示しません。", retryStop: "サービス側の停止を再確認", retryFailed: "停止を確認できませんでした。時間をおいて再確認してください。",
  elapsed: "経過時間", events: "イベント数", timeline: "接続・会話イベント", timelineHelp: "直近200件の種類と経過時間だけを表示します。SDP・トークン・セッションIDは表示しません。", usage: "直近の応答で返った使用量", usageHelp: "APIが返した項目だけを表示します。会話全体の確定請求額ではありません。", turnFailed: "この応答は生成できませんでした。接続は継続しています。もう一度話しかけてください。",
  MicrophoneDenied: "マイクへのアクセスが許可されませんでした。ブラウザーのサイト設定でマイクを許可してから開始してください。", MicrophoneUnavailable: "利用できるマイクがありません。マイクの接続と、ほかのアプリによる使用状況を確認してください。", BrowserUnsupported: "このブラウザーでは必要な音声機能を利用できません。HTTPSまたはlocalhostで、現在のChrome／Edgeなどを使用してください。",
  InvalidRequest: "リアルタイム音声の接続設定または音声データを受け付けられませんでした。選択したモデルと入力設定を確認し、もう一度開始してください。",
  body: "接続・イベントの例", language: "組み込み例の言語", backend: "APIキーと接続チケットはアプリのバックエンドで管理します。ブラウザーへ購読キーを渡しません。", protocol: "接続と終了の手順", schema: "通常のHTTP JSONに加えて、WebSocketイベントまたはWebRTCの制御・音声メディアを使います。下は各APIを組み込むための部分スキーマです。", requestSchema: "送信形式・スキーマ", responseSchema: "応答イベントの形式・スキーマ", source: "Microsoft公式の仕様", rtcDocs: "Realtime WebRTC", wsDocs: "Realtime WebSocket", voiceDocs: "Voice Live WebRTC", voiceWsDocs: "Voice Live WebSocket", liveDocs: "GPT-Live", transcriptionDocs: "Realtime transcription",
};
/** 会話画面の翻訳項目です。日本語と英語で同じキーを維持します。 */
export type RealtimeAudioDictionary = { [K in keyof typeof ja]: string };
const en: RealtimeAudioDictionary = {
  guideTitle: "From connection to conversation shutdown", guideConversation: "After connecting, receive replies as speech is detected and continue talking over the same connection. On exit, release the microphone and media connection and confirm service shutdown where the API supports it.", guideTry: "Open Try it to select an API key and output settings, then talk through your microphone. Reading this guide never starts the microphone. Stop controls stay visible while connected.",
  introduction: "Talk to the AI through your microphone and inspect connection events and captions for application integration. The connection remains open across replies until you end the conversation.",
  parameters: "Realtime audio parameters", model: "Model", transport: "Connection method", transportHelp: "Only connection methods supported by the selected model appear.", websocket: "WebSocket", webrtc: "WebRTC", realtime: "GPT Realtime", live: "GPT-Live", transcription: "GPT Live Transcribe", voiceLive: "Voice Live + GPT",
  voice: "Response voice", voiceHelp: "Choose a voice before starting. To try another voice, end the connection and select it before reconnecting.", fixedVoice: "This series uses {voice}.",
  maxOutputTokens: "Output token limit per reply", tokenRange: "Numeric range: 1–{maximum}", tokenHelp: "This value applies to one reply.", modelMaximum: "Model maximum ({count} tokens)", modelMaximumHelp: "The numeric limit differs from the model's capacity. Model maximum uses the API's dedicated maximum setting.", transcriptionTokens: "This transcription-only API continuously transcribes microphone input. It does not generate AI speech.", liveTokens: "The Live connection contract has no output-token parameter, so it cannot be changed here.",
  start: "Start microphone conversation", startTranscription: "Start microphone transcription", starting: "Preparing microphone and connection…", stop: "End connection", stopping: "Closing microphone and connection…", mute: "Mute microphone", unmute: "Unmute microphone", muted: "Your microphone is muted.", interrupt: "Stop this reply", play: "Play audio", playbackBlocked: "Your browser is holding audio playback. Press Play audio to continue.",
  permission: "Starting requests microphone permission. After connecting, speech is sent to the audio service and AI replies play through your speakers. A headset is recommended.", privacy: "Conversation audio is not recorded or saved. Captions and event history remain in this page's memory only. End the conversation before closing the tab.", limit: "The connection follows the server limit (currently up to {minutes} minutes). Replies do not end the session, and connections are never restarted automatically. Setup waits for up to 90 seconds.",
  mediaNotice: "WebRTC signaling and authentication pass through the portal backend. Audio travels between your browser and the audio service. Subscription keys remain in the backend.", wsNotice: "WebSocket converts microphone input to 100 ms frames of 24 kHz PCM and relays them through the portal backend to the gateway. Returned audio stays only in a short playback queue.",
  connecting: "Connecting to the gateway", connected: "Connected. Speak into your microphone.", sending: "Sending microphone audio", receiving: "Receiving the AI reply", conversation: "Conversation captions", noConversation: "Connect and speak to see available captions here.", user: "You", assistant: "AI", partial: "Speaking", partialEnded: "Partial transcript", transcriptNotice: "Caption support varies by API. Speech that has not been recognized, or audio without captions, is not shown.",
  ended: "Conversation ended", confirmed: "Normal completion / shutdown confirmed", transportClosed: "Audio and control connections closed", unconfirmed: "Service shutdown unconfirmed", stopHelp: "Stopping your microphone and confirming service shutdown are separate checks. Shutdown status does not determine final billing.", retryStop: "Retry service shutdown check", retryFailed: "Shutdown could not be confirmed. Retry later.",
  elapsed: "Elapsed time", events: "Events", timeline: "Connection and conversation events", timelineHelp: "Only types and elapsed times for the latest 200 events appear. SDP, tokens, and session IDs never appear.", usage: "Usage returned for the latest reply", usageHelp: "Only measurements returned by the API appear. This is not the final bill for the conversation.", turnFailed: "This reply could not be generated. The connection remains open; try speaking again.",
  MicrophoneDenied: "Microphone access was denied. Allow microphone access in your browser's site settings, then start again.", MicrophoneUnavailable: "No microphone is available. Check its connection and whether another application is using it.", BrowserUnsupported: "This browser does not support the required audio features. Use HTTPS or localhost with a current browser such as Chrome or Edge.",
  InvalidRequest: "The realtime connection settings or audio data could not be accepted. Check the selected model and input settings, then start again.",
  body: "Connection / event example", language: "Integration example language", backend: "Keep API keys and connection tickets in your application backend. Never expose subscription keys to the browser.", protocol: "Connection and shutdown steps", schema: "These APIs use WebSocket events or WebRTC control and media in addition to ordinary HTTP JSON. Partial integration schemas follow.", requestSchema: "Request format / schema", responseSchema: "Response event format / schema", source: "Microsoft API documentation", rtcDocs: "Realtime WebRTC", wsDocs: "Realtime WebSocket", voiceDocs: "Voice Live WebRTC", voiceWsDocs: "Voice Live WebSocket", liveDocs: "GPT-Live", transcriptionDocs: "Realtime transcription",
};

/** 文字起こし専用の接続を、音声応答のある会話として案内しないための差分です。 */
const transcriptionOverrides = {
  ja: {
    guideTitle: "接続から文字起こしの終了まで",
    guideConversation: "接続後はマイクの発話を継続して文字起こしします。AIの音声応答は返しません。終了操作でマイクと文字起こし接続を閉じます。",
    guideTry: "「試す」でAPIキーを選ぶと、マイクの文字起こしを開始できます。利用ガイドを表示しただけではマイクは起動しません。接続中は同じ画面に停止操作を表示します。",
    introduction: "マイクの発話をリアルタイムに文字起こしし、アプリに組み込む際の接続・イベント・認識結果を確認します。文字起こし専用のため、AIの音声応答は返しません。終了操作まで接続を維持します。",
    parameters: "リアルタイム文字起こしの設定", start: "マイクの文字起こしを開始", stop: "文字起こしを終了", stopping: "マイクと文字起こし接続を終了中…",
    permission: "開始するとブラウザーがマイクの許可を求めます。接続後は発話を文字起こしサービスへ送り、認識結果をテキストで表示します。AIの音声応答は返しません。",
    privacy: "マイク音声の録音・保存は行いません。文字起こしとイベント履歴はこの画面のメモリー内だけに保持します。タブを閉じる際も、先に「文字起こしを終了」を押してください。",
    limit: "接続上限はサーバー設定に従います（現在最大{minutes}分）。発話ごとの自動終了や自動再接続は行いません。準備は最大90秒待機します。",
    wsNotice: "WebSocketはマイクの音声を100ms単位の24kHz PCMへ変換し、PortalのバックエンドからGatewayへ中継します。文字起こしイベントを受信し、認識結果をテキストで表示します。",
    connected: "文字起こし中です。マイクに話しかけてください。", receiving: "文字起こしを受信中", conversation: "マイクの文字起こし", noConversation: "文字起こしを開始して話すと、認識結果がここに表示されます。", user: "発話", assistant: "認識結果", partial: "認識中", partialEnded: "未確定の文字起こし",
    transcriptNotice: "認識結果は発話の途中で更新され、確定すると表示が揃います。認識されていない発話は表示されません。AIの音声応答は生成しません。",
    ended: "文字起こしを終了しました", transportClosed: "マイク音声と文字起こし接続を終了", stopHelp: "マイクの停止と接続の終了を確認します。通信終了だけでは、サービス側の停止が確認できたことにはなりません。",
    timeline: "接続・文字起こしイベント", usage: "文字起こしAPIが返した使用量", usageHelp: "APIが返した項目だけを表示します。", turnFailed: "文字起こしを処理できませんでした。接続は継続しています。もう一度話しかけてください。",
    InvalidRequest: "文字起こしの接続設定または音声データを受け付けられませんでした。選択したモデルと入力設定を確認し、もう一度開始してください。",
    body: "文字起こしの接続・イベント例", backend: "APIキーはアプリのバックエンドで管理し、文字起こし用WebSocketへ接続します。ブラウザーへ購読キーを渡しません。",
    schema: "マイク音声の送信と認識結果の受信には文字起こし用WebSocketイベントを使います。下は組み込みのための部分スキーマです。", responseSchema: "文字起こしイベントの形式・スキーマ",
  },
  en: {
    guideTitle: "From connection to transcription shutdown",
    guideConversation: "After connecting, continuously transcribe microphone input. No AI speech replies are generated. End transcription to close the microphone and transcription connection.",
    guideTry: "Open Try it to select an API key and start microphone transcription. Reading this guide never starts the microphone. Stop controls stay visible while connected.",
    introduction: "Transcribe microphone input in real time and inspect connection events and recognized text for application integration. This transcription-only connection does not generate AI speech replies. It remains open until you stop it.",
    parameters: "Realtime transcription parameters", start: "Start microphone transcription", stop: "End transcription", stopping: "Closing microphone and transcription connection…",
    permission: "Starting requests microphone permission. After connecting, speech is sent to the transcription service and recognized text appears here. No AI speech replies are generated.",
    privacy: "Microphone audio is not recorded or saved. Transcripts and event history remain in this page's memory only. End transcription before closing the tab.",
    limit: "The connection follows the server limit (currently up to {minutes} minutes). Utterances do not end the session, and connections are never restarted automatically. Setup waits for up to 90 seconds.",
    wsNotice: "WebSocket converts microphone input to 100 ms frames of 24 kHz PCM and relays them through the portal backend to the gateway. Transcription events return recognized text for display.",
    connected: "Transcribing. Speak into your microphone.", receiving: "Receiving transcription", conversation: "Microphone transcript", noConversation: "Start transcription and speak to see recognized text here.", user: "Speech", assistant: "Recognized text", partial: "Recognizing", partialEnded: "Partial transcript",
    transcriptNotice: "Recognized text updates during an utterance and settles when finalized. Speech that has not been recognized is not shown. No AI speech replies are generated.",
    ended: "Transcription ended", transportClosed: "Microphone audio and transcription connection closed", stopHelp: "Check microphone shutdown and connection closure. Transport closure alone does not confirm service shutdown.",
    timeline: "Connection and transcription events", usage: "Usage returned by the transcription API", usageHelp: "Only measurements returned by the API appear.", turnFailed: "This transcription could not be processed. The connection remains open; try speaking again.",
    InvalidRequest: "The transcription connection settings or audio data could not be accepted. Check the selected model and input settings, then start again.",
    body: "Transcription connection / event example", backend: "Keep API keys in your application backend and connect to the transcription WebSocket there. Never expose subscription keys to the browser.",
    schema: "Transcription WebSocket events send microphone audio and return recognized text. Partial integration schemas follow.", responseSchema: "Transcription event format / schema",
  },
} satisfies Record<Locale, Partial<RealtimeAudioDictionary>>;

/** 秘密値や環境依存値を含めず、表示言語と音声の接続契約に合う文言を選びます。 */
export function getRealtimeAudioDictionary(locale: Locale, family?: RealtimeModel["family"]): RealtimeAudioDictionary {
  const dictionary = locale === "ja" ? ja : en;
  return family === "transcription" ? { ...dictionary, ...transcriptionOverrides[locale] } : dictionary;
}
