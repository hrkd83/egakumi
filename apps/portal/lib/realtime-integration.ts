import { getRealtimeVoiceOptions, resolveRealtimeVoice } from "./realtime-voices.ts";
import type { RealtimeModel } from "./model-types.ts";
import { resolveRealtimeOutputTokens, type RealtimeInput } from "./realtime-request.ts";
import type { IntegrationLanguage } from "./model-integration.ts";
import { getTextGenerationEndpoint } from "./model-integration.ts";
import { buildRealtimeSessionEvent, REALTIME_CONVERSATION_INSTRUCTIONS } from "./realtime-protocol.ts";

/** 画面とバックエンドで同じモデル別の出力指定条件を適用します。 */
export function validateRealtimeInput(model: RealtimeModel, input: RealtimeInput): void { resolveRealtimeOutputTokens(model, input); resolveRealtimeVoice(model, input); }

/** API正本が公開する製品・パスと、選択モデルの組合せを確認します。 */
export function validateRealtimeModel(model: RealtimeModel): void {
  const targets: Record<RealtimeModel["family"], { apiId: string; path: string; models?: string[] }> = {
    realtime: { apiId: "openai-realtime-ws-v1", path: "/models/openai-realtime/v1/ws", models: ["gpt-realtime-2.1", "gpt-realtime-2.1-mini"] },
    live: { apiId: "openai-live-ws-v1", path: "/models/openai-live/v1/ws", models: ["gpt-live-1"] },
    transcription: { apiId: "openai-transcription-ws-v1", path: "/models/openai-transcription/v1/ws", models: ["gpt-live-transcribe"] },
    "voice-live": { apiId: "azure-voice-live-ws-v1", path: "/models/voice-live/v1/ws" },
  };
  if (model.transport !== "websocket") return;
  const target = targets[model.family];
  if (!target || model.apiId !== target.apiId || model.path.replace(/\/$/, "") !== target.path || model.operationId !== "invoke" || target.models && !target.models.includes(model.modelId)
    || !/^[a-zA-Z0-9._-]{1,160}$/.test(model.modelId) || !/^[A-Za-z0-9:._-]{1,150}$/.test(model.voice)
    || !Number.isInteger(model.maxRequestBytes) || model.maxRequestBytes < 4096 || model.maxRequestBytes > 65536
    || model.maxOutputTokens !== null && (!Number.isInteger(model.maxOutputTokens) || model.maxOutputTokens < 1 || model.maxOutputTokens > 1_000_000)) throw new Error("Invalid realtime model");
}

/** キーをURLへ含めず、登録済みの公開Gatewayだけを接続先にします。 */
export function getRealtimeEndpoint(model: RealtimeModel, gatewayOrigin: string): string {
  validateRealtimeModel(model);
  const target = new URL(getTextGenerationEndpoint({ path: model.path }, gatewayOrigin));
  if (model.transport === "websocket" || model.family === "voice-live") { target.protocol = target.protocol === "http:" ? "ws:" : "wss:"; if (model.family === "realtime") target.searchParams.set("model", model.modelId); }
  return target.href;
}

/** 初回とモデル変更時はモデル最大を選び、数値指定がないAPIには項目を追加しません。 */
export function defaultRealtimeInput(model: RealtimeModel): RealtimeInput {
  return maximumRealtimeInput(model);
}

/** 仕様表示にはモデルの最大設定を使い、APIが対応しない項目は含めません。 */
function maximumRealtimeInput(model: RealtimeModel): RealtimeInput {
  if (model.family === "live" || model.family === "transcription") return { modelId: model.id };
  if (model.family === "voice-live" && model.supportsMaximumOutput) return { modelId: model.id, maxOutputTokens: "model-max" };
  const maximum = model.numericMaxOutputTokens ?? model.maxOutputTokens;
  return { modelId: model.id, ...(maximum === null ? {} : { maxOutputTokens: maximum }) };
}

/** マイク由来の音声入力と、利用者による停止を製品別に示します。 */
export function getRealtimeRequestExample(model: RealtimeModel, input: RealtimeInput): string {
  if (model.transport === "webrtc") return JSON.stringify({ method: model.family === "voice-live" ? "GET / WSS upgrade" : "POST", contentType: "application/json", body: model.family === "voice-live" ? voiceLiveRtcOffer(model, input, "<browser-generated audio-only SDP offer>") : { sdp: "<browser-generated audio-only SDP offer>", ...(model.family === "live" ? { instructions: REALTIME_CONVERSATION_INSTRUCTIONS, voice: resolveRealtimeVoice(model, input) } : {}) }, media: "WebRTC audio track; api-key remains on the application backend", afterConnect: buildRealtimeSessionEvent(model, input) }, null, 2);
  return JSON.stringify({ startEvent: buildRealtimeSessionEvent(model, input), repeatWhileMicrophoneIsActive: { type: model.family === "live" ? "session.input_audio.append" : "input_audio_buffer.append", audio: "<base64: microphone PCM16LE mono at 24000 Hz, 100 ms / 4800 bytes per chunk>" },
    turnDetection: model.family === "live" ? "Live full-duplex conversation" : model.family === "transcription" ? "This model has no VAD; the backend commits about 300 ms of microphone PCM at a time while keeping the recognition session open" : "server_vad detects utterances and starts responses automatically; no per-utterance commit or response.create from the caller",
    onUtteranceComplete: "Keep the microphone, connection, and playback active for the next utterance", onUserStop: model.family === "live" ? { send: { type: "session.close" }, waitFor: "session.closed", then: "Close WebSocket and stop microphone / playback" } : { action: "Cancel an active response if needed, close WebSocket, and stop microphone / playback" } }, null, 2);
}

/** 音声本文と接続設定を区別し、WAVヘッダーを直接送れない契約を明示します。 */
export function getRealtimeRequestSchema(model: RealtimeModel): Record<string, unknown> {
  if (model.transport === "webrtc") {
    const sdp = { type: "string", minLength: 20, maxLength: 65536, pattern: "^v=0\\r\\n", description: "Browser-generated audio-only SDP offer; media codec is negotiated by WebRTC." };
    const common = { $schema: "https://json-schema.org/draft/2020-12/schema", title: `${model.label} WebRTC signaling`, transport: "webrtc", contentMediaType: "application/json", voiceOptions: getRealtimeVoiceOptions(model).map((voice) => voice.id), authentication: { header: "api-key", storage: "application backend" },
      media: { transport: "WebRTC audio track", route: "browser ↔ service over the negotiated ICE path", encoding: "negotiated in SDP; no PCM append events in this signaling request" } };
    if (model.family === "voice-live") return { ...common, signaling: "WSS GET upgrade, then JSON control events", type: "object", required: ["type", "sdp_offer", "session"], additionalProperties: false,
      properties: { type: { const: "rtc.call.sdp.create" }, sdp_offer: sdp, session: { type: "object", description: "Voice Live session configuration", properties: { modalities: { const: ["text", "audio"] }, voice: { type: "object", required: ["type", "name"], properties: { type: { const: "azure-standard" }, name: { enum: getRealtimeVoiceOptions(model).map((voice) => voice.id) } } }, max_response_output_tokens: { oneOf: [{ type: "integer", minimum: 1, maximum: model.numericMaxOutputTokens ?? model.maxOutputTokens }, { const: "inf", description: `Use the model maximum (${model.maxOutputTokens} tokens).` }] }, tools: { const: [] } } } } };
    return { ...common, signaling: "HTTPS POST", type: "object", required: ["sdp"], additionalProperties: false, properties: { sdp,
      ...(model.family === "live" ? { instructions: { type: "string", maxLength: 8192 }, voice: { enum: getRealtimeVoiceOptions(model).map((voice) => voice.id) } } : {}) },
      modelBinding: { model: model.modelId, source: "Gateway operation definition; caller cannot override the model in the request body" },
      ...(model.family === "realtime" ? { afterConnectDataChannel: buildRealtimeSessionEvent(model, maximumRealtimeInput(model)) } : {}) };
  }
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: `${model.label} / ${model.transport}`, transport: model.transport,
    contentMediaType: "application/json", audio: { encoding: "PCM signed 16-bit little-endian", sampleRate: 24000, channels: 1, container: "none", chunkMs: 100 },
    authentication: { header: "api-key", storage: "application backend" }, ...(model.family === "transcription" ? {} : { voiceOptions: getRealtimeVoiceOptions(model).map((voice) => voice.id) }), sessionConfiguration: buildRealtimeSessionEvent(model, maximumRealtimeInput(model)),
    ...(model.family === "live" ? { outputTokenLimit: "GPT-Live session.start does not accept max_output_tokens; this Portal bounds connection duration and audio bytes." } : {}) };
}

/** 接続・途中イベントだけを成功扱いしないため、製品ごとの完了条件を示します。 */
export function getRealtimeResponseSchema(model: RealtimeModel): Record<string, unknown> {
  if (model.transport === "webrtc") {
    const sdp = { type: "string", minLength: 20, maxLength: 65536, pattern: "^v=0\\r\\n", description: "Apply this SDP answer to the browser RTCPeerConnection." };
    const common = { $schema: "https://json-schema.org/draft/2020-12/schema", title: `${model.label} WebRTC signaling response`, transport: "webrtc", media: { transport: "WebRTC audio track", audioReceivedOverSignaling: false, verification: "Verify the peer connection, received audio track, and media statistics separately from signaling success." } };
    if (model.family === "realtime") return { ...common, httpStatus: 201, contentMediaType: "application/sdp", ...sdp,
      ownershipHeaders: { "X-Core-Call-Id": "Server-only call identifier", "X-Core-Call-Ticket": "Server-only ownership proof; use with the same subscription key" },
      observer: { transport: "WSS", path: model.observer?.path ?? "/models/openai-realtime/v1/observer/", query: { call_id: "{callId}" }, headers: ["api-key", "X-Core-Call-Ticket"], purpose: "Observe response.done and usage without proxying the browser audio track" },
      stop: { method: "POST", path: model.hangupPath ?? "/models/openai-realtime/v1/calls/{callId}/hangup", headers: ["api-key", "X-Core-Call-Ticket"], body: {}, confirmation: "Successful hangup response, then close local audio tracks and RTCPeerConnection" },
      answerAloneIsAudioSuccess: false };
    if (model.family === "live") return { ...common, contentMediaType: "application/json", type: "object", required: ["sessionId", "sdp"], additionalProperties: false, properties: { sessionId: { type: "string", pattern: "^[A-Za-z0-9_-]{1,160}$" }, sdp },
      stop: { gatewayHangupAvailable: false, action: "Stop local audio tracks and close RTCPeerConnection", finalUsage: "The current released Gateway API has no Live attach/close operation; transport closure does not prove final service usage." }, answerAloneIsAudioSuccess: false };
    return { ...common, contentMediaType: "application/json", signaling: "JSON event over the retained control WSS", type: "object", required: ["type", "sdp_answer"], properties: { type: { const: "rtc.call.sdp.created" }, sdp_answer: sdp }, errorEvent: "rtc.call.error / error",
      stop: { gatewayHangupAvailable: false, action: "Stop audio tracks, close RTCPeerConnection, and close the retained Voice Live control WSS", finalUsage: "A closed transport alone does not confirm final service usage." }, answerAloneIsAudioSuccess: false };
  }
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: `${model.label} server events`, type: "object", required: ["type"], properties: { type: { type: "string" }, delta: { type: "string", description: model.family === "transcription" ? "Partial transcript" : "Base64 PCM24k audio or transcript fragment, according to event type" } },
    readyEvent: model.family === "live" ? "session.started" : "session.updated", audioEvent: model.family === "live" ? "session.output_audio.delta" : model.family === "transcription" ? null : "response.output_audio.delta / response.audio.delta",
    utteranceCompleteEvent: model.family === "live" ? null : model.family === "transcription" ? "conversation.item.input_audio_transcription.completed for each committed item" : "response.done (completed / cancelled / incomplete / failed describes one response)",
    utteranceCompletionClosesSession: false, sessionEnd: model.family === "live" ? "User stops: session.close → session.closed with final cumulative usage" : "User stops: close the WebSocket and release microphone / playback; VAD continues across utterances until then",
    transportCloseAloneIsSuccess: false };
}

/** 利用者に見せるイベント順序と、停止・利用量の確認方法です。 */
export function getRealtimeProtocolGuide(model: RealtimeModel, locale = "ja"): string {
  const ja = locale === "ja";
  if (model.transport === "webrtc") {
    if (model.family === "realtime") return ja
      ? "ブラウザーで音声専用SDP offerを作成 → アプリのバックエンドから購読キー付きPOST JSON {sdp} → Gatewayが201 application/sdpでanswerを返す → ブラウザーへanswerだけを渡してWebRTC接続。X-Core-Call-Id／X-Core-Call-Ticketはサーバーで保持します。音声はWebRTC trackで端末からサービスへ直接流れ、WSSのPCM appendは使いません。サーバーは同じ購読キーとticketでobserverへ接続し、response.doneとusageを観測します。終了時は所有者を確認してhangup APIを呼び、音声trackとRTCPeerConnectionも閉じます。SDP成功だけを音声疎通成功にはしません。"
      : "Browser creates an audio-only SDP offer → application backend POSTs JSON {sdp} with its subscription key → Gateway returns 201 application/sdp → browser applies only the answer. Keep X-Core-Call-Id and X-Core-Call-Ticket server-side. Audio travels directly over the WebRTC track, not PCM append events. The server uses the same key and ticket for the observer WSS to inspect response.done and usage. On stop, verify the owner, call the hangup API, and close audio tracks and RTCPeerConnection. An SDP answer alone does not prove audio success.";
    if (model.family === "live") return ja
      ? "ブラウザーの音声専用SDP offerを、アプリのバックエンドから購読キー付きPOST JSON {sdp,instructions?,voice?}として送信 → GatewayがJSON {sessionId,sdp}を返す → ブラウザーへSDP answerを設定。モデルはGateway定義で固定され、音声はWebRTC trackで端末とサービスの間を流れます。これはLive WSSのsession.start／PCM appendの手順とは別です。現在公開しているLive APIにはhangup・attach操作がないため、終了時は音声trackとRTCPeerConnectionを閉じます。この経路ではサービス側の最終usageを確認したとは扱いません。"
      : "Backend POSTs JSON {sdp,instructions?,voice?} with its subscription key → Gateway returns JSON {sessionId,sdp} → browser applies the SDP answer. The model is bound by the Gateway definition; audio uses the WebRTC track between browser and service. This differs from Live WSS session.start and PCM append. The current released Live API has no hangup or attach operation: stop audio tracks and close RTCPeerConnection. This transport closure does not confirm final service usage.";
    return ja
      ? "アプリのバックエンドが購読キー付きでVoice Live専用WSSを開く → rtc.call.sdp.create {sdp_offer,session}を送信 → rtc.call.sdp.createdのsdp_answerをブラウザーへ渡してWebRTC接続。HTTP POSTではありません。音声メディアはWebRTC trackを使い、制御WSSは利用者の停止まで保持します。終了時は音声track・RTCPeerConnection・制御WSSをすべて閉じます。SDP交換だけを音声成功にはせず、受信音声とメディア統計を別途確認します。通信終了だけでは最終usageを確認したことにはなりません。"
      : "Backend opens the dedicated Voice Live WSS using its subscription key → sends rtc.call.sdp.create {sdp_offer,session} → forwards rtc.call.sdp.created.sdp_answer to the authenticated browser. This uses no HTTP POST. Audio uses the WebRTC track; retain the control WSS until the user stops. Stop audio tracks, RTCPeerConnection, and control WSS together. Verify received audio and media statistics separately; SDP exchange and transport closure alone do not prove audio success or final service usage.";
  }
  if (model.family === "live") return ja ? "開始操作でマイク許可を取得し、session.start → session.startedの後、マイクのPCMをsession.input_audio.appendで送り続けます。応答音声session.output_audio.deltaと字幕は受信の都度、短い再生キューで処理します。Liveは複数発話のfull duplex会話で、応答ごとの自動終了は行いません。利用者が停止したときだけsession.close → session.closedを確認し、最終usageを受け取ってマイク・再生・接続を解放します。最大出力トークンはLiveの開始契約にはなく、接続時間・転送量の上限を使います。" : "On Start, obtain microphone permission, send session.start, and wait for session.started. Continuously send microphone PCM through session.input_audio.append and play session.output_audio.delta through a bounded queue. Full-duplex conversation continues across utterances. Only when the user stops, send session.close, wait for session.closed and final usage, and release microphone, playback, and socket. Live exposes no max-output-token field; duration and transfer size are bounded.";
  if (model.family === "transcription") return ja ? "開始操作でマイクを取得 → session.update（type=transcription、turn_detection=null）→ session.updated → input_audio_buffer.appendでマイクを継続送信します。このモデルはVAD非対応のため、バックエンドが約300ms分の音声ごとにinput_audio_buffer.commitを生成し、対応するitem_idの認識delta／completedを返します。認識が一回確定しても接続を閉じず、連続した入力を処理します。音声応答とresponse.createは使わず、停止時は残りの音声を確定し、マイクと接続を解放します。" : "On Start, obtain the microphone, configure a transcription session with turn_detection=null, and continuously send input_audio_buffer.append. This model does not support VAD: the backend sends input_audio_buffer.commit about every 300 ms of audio and correlates transcription delta/completed with its item_id. Keep the connection open after each final transcript. No response.create or output audio is used; flush pending audio and release microphone and socket when the user stops.";
  return ja ? "開始操作でマイクを取得 → session.update（server_vad）→ session.updated → input_audio_buffer.appendでマイクを継続送信します。発話区間の検出と応答開始はサービスへ任せ、呼び出し側から発話ごとのcommit・response.createを送りません。受信音声・字幕を随時再生・表示し、response.doneは一回の応答の区切りとして扱います。次の発話のために接続を維持し、応答取消でも会話を終了させません。利用者の停止時に音声・マイク・接続を解放します。" : "On Start, obtain microphone permission, configure server_vad, and continuously send input_audio_buffer.append after session.updated. Let the service detect utterances and start responses; do not send per-turn commit or response.create. Play audio and update transcripts incrementally. response.done ends one response, not the conversation. Continue across utterances and response cancellation until the user stops, then release microphone, playback, and socket.";
}

/** 5言語の会話アダプター例です。マイク入力と停止通知は呼び出しアプリから渡します。 */
export function getRealtimeIntegrationCode(model: RealtimeModel, input: RealtimeInput, origin: string, language: IntegrationLanguage, locale = "ja"): string {
  const endpoint = getRealtimeEndpoint(model, origin);
  if (model.transport === "webrtc") return webRtcExample(model, input, endpoint, language);
  const start = JSON.stringify(buildRealtimeSessionEvent(model, input));
  const ready = model.family === "live" ? "session.started" : "session.updated";
  const append = model.family === "live" ? "session.input_audio.append" : "input_audio_buffer.append";
  const note = locale === "ja" ? "マイクはブラウザーで許可を取り、本人に紐づく自アプリのバックエンドへ送ります。PCMは24kHz・16bit・mono、100msごと4800bytes。下記のaudioFramesはマイク由来の非同期入力、stopは利用者の停止操作です。キーはバックエンド環境変数だけで保持します。" : "Obtain microphone permission in the browser and stream it to the authenticated application backend. audioFrames provides microphone PCM24kHz/16-bit/mono, 4800 bytes every 100 ms; stop is the user stop signal. Keep the key only on the backend.";
  if (language === "typescript") return typescriptConversationExample(model, start, ready, append, endpoint, note);
  if (language === "python") return pythonConversationExample(model, start, ready, append, endpoint, note);
  if (language === "php") return `<?php\n// Requires Swoole coroutine runtime. ${note}\nuse Swoole\\Coroutine\\Http\\Client;\nuse Swoole\\Coroutine\\Channel;\nfunction converse(Channel $audioFrames, Channel $stop, callable $consume): void {\n  $url = parse_url(${JSON.stringify(endpoint)});\n  $socket = new Client($url["host"], $url["port"] ?? 443, true);\n  $socket->set(["ssl_verify_peer" => true, "ssl_host_name" => $url["host"], "ssl_allow_self_signed" => false]);\n  $socket->setHeaders(["api-key" => getenv("GATEWAY_API_KEY")]);\n  if (!$socket->upgrade($url["path"] . (isset($url["query"]) ? "?" . $url["query"] : ""))) throw new RuntimeException("Gateway connection failed");\n  $socket->push(${JSON.stringify(start)});\n  do { $frame = $socket->recv(10); if (!$frame) throw new RuntimeException("Session configuration failed"); $event = json_decode($frame->data, true, 32, JSON_THROW_ON_ERROR); } while (($event["type"] ?? "") !== ${JSON.stringify(ready)});\n  $closed = new Channel(1);\n  $sender = go(function () use ($socket, $audioFrames) {${model.family === "transcription" ? "\n    $bufferedBytes = 0;" : ""}\n    while (($pcm = $audioFrames->pop()) !== false) {\n      if (!strlen($pcm) || strlen($pcm) % 2 || strlen($pcm) > 9600) break;\n      if (!$socket->push(json_encode(["type" => ${JSON.stringify(append)}, "audio" => base64_encode($pcm)], JSON_THROW_ON_ERROR))) break;${model.family === "transcription" ? '\n      $bufferedBytes += strlen($pcm); if ($bufferedBytes >= 14400) { $socket->push(\'{"type":"input_audio_buffer.commit"}\'); $bufferedBytes = 0; }' : ""}\n    }\n  });\n  $receiver = go(function () use ($socket, $consume, $closed) {\n    while ($frame = $socket->recv()) {\n      $event = json_decode($frame->data, true, 32, JSON_THROW_ON_ERROR);\n      $consume($event); // response.done finishes one utterance only.\n      if (($event["type"] ?? "") === "session.closed") { $closed->push(true); return; }\n    }\n  });\n  try {\n    $stop->pop(540);\n${model.family === "live" ? '    $socket->push(\'{"type":"session.close"}\'); $closed->pop(4);' : ''}\n  } finally { $audioFrames->close(); $socket->close(); }\n}\n// Call inside Swoole\\Coroutine\\run(); bind Channels to this authenticated user's microphone and Stop action.`;
  if (language === "csharp") return `// ${note}\nusing System.Net.WebSockets;\nusing System.Text;\nusing System.Text.Json;\n\nstatic async Task Converse(IAsyncEnumerable<byte[]> audioFrames, CancellationToken userStopped, Func<JsonElement, Task> consume) {\n  using var lifetime = new CancellationTokenSource(TimeSpan.FromMinutes(9));\n  using var ws = new ClientWebSocket();\n  ws.Options.SetRequestHeader("api-key", Environment.GetEnvironmentVariable("GATEWAY_API_KEY"));\n  await ws.ConnectAsync(new Uri(${JSON.stringify(endpoint)}), lifetime.Token);\n  async Task Send(string json) => await ws.SendAsync(Encoding.UTF8.GetBytes(json), WebSocketMessageType.Text, true, lifetime.Token);\n  async Task<JsonDocument> Receive() {\n    using var message = new MemoryStream(); var buffer = new byte[16384]; ValueWebSocketReceiveResult part;\n    do { part = await ws.ReceiveAsync(buffer.AsMemory(), lifetime.Token); if (part.MessageType != WebSocketMessageType.Text || message.Length + part.Count > 262144) throw new InvalidDataException(); message.Write(buffer, 0, part.Count); } while (!part.EndOfMessage);\n    return JsonDocument.Parse(message.ToArray());\n  }\n  await Send(${JSON.stringify(start)});\n  while (true) { using var evt = await Receive(); if (evt.RootElement.GetProperty("type").GetString() == ${JSON.stringify(ready)}) break; }\n  var closed = new TaskCompletionSource();\n  var receiver = Task.Run(async () => {\n    while (ws.State == WebSocketState.Open) { using var evt = await Receive(); await consume(evt.RootElement); if (evt.RootElement.GetProperty("type").GetString() == "session.closed") { closed.TrySetResult(); break; } }\n  });\n  ${model.family === "transcription" ? "var bufferedAudioBytes = 0;" : ""}\n  try {\n    await foreach (var pcm in audioFrames.WithCancellation(userStopped)) {\n      if (pcm.Length == 0 || pcm.Length % 2 != 0 || pcm.Length > 9600) throw new InvalidDataException();\n      await Send(JsonSerializer.Serialize(new { type = ${JSON.stringify(append)}, audio = Convert.ToBase64String(pcm) }));${model.family === "transcription" ? '\n      bufferedAudioBytes += pcm.Length; if (bufferedAudioBytes >= 14400) { await Send(JsonSerializer.Serialize(new { type = "input_audio_buffer.commit" })); bufferedAudioBytes = 0; }' : ""}\n    }\n  } catch (OperationCanceledException) when (userStopped.IsCancellationRequested) { }\n  finally {\n${model.family === "live" ? '    if (ws.State == WebSocketState.Open) { await Send("{\\"type\\":\\"session.close\\"}"); await closed.Task.WaitAsync(TimeSpan.FromSeconds(4)); }' : '    if (ws.State == WebSocketState.Open) await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "", CancellationToken.None);'}\n    lifetime.Cancel(); ws.Abort(); try { await receiver; } catch (OperationCanceledException) { }\n  }\n}\n// An utterance-level response.done does not stop receiver. audioFrames and userStopped belong to the authenticated application user.`;
  return `# curl can check only the handshake, not capture microphones or exchange application WebSocket frames.\n# Use the TypeScript/Python/PHP/C# adapter to stream microphone PCM until the user stops.\n# A WebSocket application sends this session configuration after the handshake: ${start}\ncurl --http1.1 --include --no-buffer --max-time 10 ${JSON.stringify(endpoint.replace(/^wss:/, "https:").replace(/^ws:/, "http:"))} \\\n  -H "api-key: $GATEWAY_API_KEY" -H 'Connection: Upgrade' -H 'Upgrade: websocket' \\\n  -H 'Sec-WebSocket-Version: 13' -H "Sec-WebSocket-Key: $(openssl rand -base64 16)"\n# 101 is handshake only; successful conversations require actual microphone input and received audio.`;
}

/** 入力フレームが届かない場合も、停止・切断で入力待ちを解除するTypeScript例です。 */
function typescriptConversationExample(model: RealtimeModel, start: string, ready: string, append: string, endpoint: string, note: string): string {
  return `// npm install ws
// ${note}
import WebSocket from "ws";
export async function converse(audioFrames: AsyncIterable<Uint8Array>, stop: AbortSignal, consume: (event: unknown) => void) {
  if (stop.aborted) return;
  const ws = new WebSocket(${JSON.stringify(endpoint)}, { headers: { "api-key": process.env.GATEWAY_API_KEY! }, maxPayload: 262144, followRedirects: false });
  let activate!: () => void; const ready = new Promise<void>(resolve => { activate = resolve; });
  let closed!: () => void; const ended = new Promise<void>(resolve => { closed = resolve; });
  const reading = new AbortController();
  const iterator = audioFrames[Symbol.asyncIterator]();
  let stopping = false;${model.family === "transcription" ? " let bufferedAudioBytes = 0;" : ""}
  const finish = () => {
    if (stopping) return; stopping = true; reading.abort();
    if (ws.readyState !== WebSocket.OPEN) { ws.terminate(); return; }
${model.family === "live" ? '    ws.send(JSON.stringify({ type: "session.close" }));\n    setTimeout(() => ws.terminate(), 4000).unref();' : '    ws.close(1000);'}
  };
  // next() may be waiting for a muted/stalled microphone; do not await it after stop.
  const nextFrame = () => new Promise<IteratorResult<Uint8Array>>((resolve, reject) => {
    const cleanup = () => reading.signal.removeEventListener("abort", interrupted);
    const interrupted = () => { cleanup(); resolve({ done: true, value: undefined }); };
    if (reading.signal.aborted) { interrupted(); return; }
    reading.signal.addEventListener("abort", interrupted, { once: true });
    Promise.resolve(iterator.next()).then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
  });
  stop.addEventListener("abort", finish, { once: true });
  const limit = setTimeout(finish, 540000);
  ws.on("open", () => ws.send(${JSON.stringify(start)}));
  ws.on("message", raw => {
    const event = JSON.parse(raw.toString());
    if (event.type === ${JSON.stringify(ready)}) activate();
    // response.done ends one utterance, not this conversation.
    if (event.type === "session.closed" || ["error", "session.error"].includes(event.type)) ws.close(1000);
    consume(event);
  });
  ws.on("close", () => { reading.abort(); closed(); });
  ws.on("error", () => { reading.abort(); ws.terminate(); closed(); });
  try {
    await Promise.race([ready, ended]);
    while (!reading.signal.aborted && ws.readyState === WebSocket.OPEN) {
      const frame = await nextFrame();
      if (frame.done || stopping || stop.aborted || ws.readyState !== WebSocket.OPEN) break;
      const pcm = frame.value;
      if (!pcm.length || pcm.length % 2 || pcm.length > 9600 || ws.bufferedAmount > 128000) throw new Error("Invalid or congested microphone stream");
      ws.send(JSON.stringify({ type: ${JSON.stringify(append)}, audio: Buffer.from(pcm).toString("base64") }));${model.family === "transcription" ? '\n      bufferedAudioBytes += pcm.length; if (bufferedAudioBytes >= 14400) { ws.send(JSON.stringify({type:"input_audio_buffer.commit"})); bufferedAudioBytes = 0; }' : ""}
    }
    finish(); await ended;
  } finally {
    clearTimeout(limit); stop.removeEventListener("abort", finish); reading.abort(); ws.terminate();
    // The producer owns microphone cleanup and must also observe the user's stop signal.
    void Promise.resolve(iterator.return?.()).catch(() => undefined);
  }
}`;
}

/**
 * Python例でも送信・受信・利用者停止を同時に監視し、上流失敗で停止待ちを残しません。
 * @remarks 受付前の拒否と途中のエラーを区別し、例外本文へ上流の秘密を含めません。
 */
function pythonConversationExample(model: RealtimeModel, start: string, ready: string, append: string, endpoint: string, note: string): string {
  return `# ${note}
# pip install websockets
import asyncio, base64, json, os
from websockets.asyncio.client import connect

async def converse(audio_frames, stop: asyncio.Event, consume):
    if stop.is_set(): return
    async with connect(${JSON.stringify(endpoint)}, additional_headers={"api-key": os.environ["GATEWAY_API_KEY"]}, max_size=262144, open_timeout=10) as ws:
        await ws.send(${JSON.stringify(start)})
        async with asyncio.timeout(10):
            async for raw in ws:
                event = json.loads(raw)
                if event.get("type") in ("error", "session.error"): raise RuntimeError("Gateway configuration failed")
                if event.get("type") == ${JSON.stringify(ready)}: break
            else: raise RuntimeError("Gateway closed before configuration")
        closed = asyncio.Event()
        async def send_microphone():${model.family === "transcription" ? "\n            buffered_bytes = 0" : ""}
            async for pcm in audio_frames:
                if stop.is_set(): break
                if not pcm or len(pcm) % 2 or len(pcm) > 9600: raise ValueError("Invalid microphone frame")
                await ws.send(json.dumps({"type":${JSON.stringify(append)}, "audio":base64.b64encode(pcm).decode()}))${model.family === "transcription" ? '\n                buffered_bytes += len(pcm)\n                if buffered_bytes >= 14400:\n                    await ws.send(json.dumps({"type":"input_audio_buffer.commit"}))\n                    buffered_bytes = 0' : ""}
        async def receive():
            async for raw in ws:
                event = json.loads(raw)
                if event.get("type") in ("error", "session.error"): raise RuntimeError("Gateway error")
                await consume(event) # response.done ends one utterance, not the connection.
                if event.get("type") == "session.closed": closed.set(); return
        sender = asyncio.create_task(send_microphone())
        receiver = asyncio.create_task(receive())
        stopped = asyncio.create_task(stop.wait())
        tasks = (sender, receiver, stopped)
        try:
            done, _ = await asyncio.wait(tasks, timeout=540, return_when=asyncio.FIRST_COMPLETED)
            for task in (sender, receiver):
                if task in done: await task # Propagate transport/input failures immediately.
            if receiver in done and not stop.is_set(): raise RuntimeError("Gateway closed unexpectedly")
${model.family === "live" ? '            await ws.send(json.dumps({"type":"session.close"}))\n            await asyncio.wait_for(closed.wait(), 4)' : '            await ws.close()'}
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
# Connect audio_frames to the authenticated browser microphone stream; set stop when its user clicks Stop.`;
}

/** WebRTCではSDPの作成・メディアの送信を端末で行い、秘密キー付き開始をアプリのサーバーへ任せます。 */
function webRtcExample(model: RealtimeModel, input: RealtimeInput, endpoint: string, language: IntegrationLanguage): string {
  if (model.family === "voice-live") return voiceLiveWebRtcExample(model, input, endpoint, language);
  const body = JSON.stringify({ sdp: "<audio-only SDP offer>", ...(model.family === "live" ? { instructions: REALTIME_CONVERSATION_INSTRUCTIONS, voice: resolveRealtimeVoice(model, input) } : {}) });
  const contentType = "application/json";
  const notes = `WebRTC: browser creates RTCPeerConnection and an audio-only offer; application backend POSTs the offer to ${endpoint} using its protected api-key; browser applies the SDP answer. Audio uses the WebRTC media track. Always stop the track and close the peer connection; use the Gateway hangup API when available.${model.family === "realtime" ? ` After applying the answer, send this session configuration over the browser DataChannel before audio output: ${JSON.stringify(buildRealtimeSessionEvent(model, input))}` : ""}`;
  if (language === "typescript") return `// ${notes}\nconst response = await fetch(${JSON.stringify(endpoint)}, { method: "POST", redirect: "error", headers: { "api-key": process.env.GATEWAY_API_KEY!, "Content-Type": ${JSON.stringify(contentType)} }, body: ${JSON.stringify(body)}, signal: AbortSignal.timeout(15000) });\nif (!response.ok) throw new Error("Gateway connection failed");\nconst answer = await response.${model.family === "realtime" ? "text" : "json"}();\n// Return only the SDP answer to the authenticated owner; keep sessionId/tickets server-side.`;
  if (language === "python") return `# ${notes}\nimport os, requests\nresponse = requests.post(${JSON.stringify(endpoint)}, headers={"api-key": os.environ["GATEWAY_API_KEY"], "Content-Type": ${JSON.stringify(contentType)}}, data=${JSON.stringify(body)}, timeout=15, allow_redirects=False)\nresponse.raise_for_status()\nanswer = response.${model.family === "realtime" ? "text" : "json()"}`;
  if (language === "php") return `<?php\n// ${notes}\n$curl = curl_init(${JSON.stringify(endpoint)});\ncurl_setopt_array($curl, [CURLOPT_POST => true, CURLOPT_POSTFIELDS => ${JSON.stringify(body)}, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 15, CURLOPT_FOLLOWLOCATION => false, CURLOPT_HTTPHEADER => ["api-key: " . getenv("GATEWAY_API_KEY"), ${JSON.stringify(`Content-Type: ${contentType}`)}]]);\ntry { $answer = curl_exec($curl); if ($answer === false || curl_getinfo($curl, CURLINFO_RESPONSE_CODE) >= 300) throw new RuntimeException("Gateway connection failed"); } finally { curl_close($curl); }`;
  if (language === "csharp") return `// ${notes}\nusing var client = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(15) };\nusing var request = new HttpRequestMessage(HttpMethod.Post, ${JSON.stringify(endpoint)});\nrequest.Headers.Add("api-key", Environment.GetEnvironmentVariable("GATEWAY_API_KEY"));\nrequest.Content = new StringContent(${JSON.stringify(body)}, System.Text.Encoding.UTF8, ${JSON.stringify(contentType)});\nusing var response = await client.SendAsync(request); response.EnsureSuccessStatusCode();\nvar answer = await response.Content.ReadAsStringAsync();`;
  return `# ${notes}\ncurl --fail-with-body --max-time 15 ${JSON.stringify(endpoint)} \\\n  -H "api-key: $GATEWAY_API_KEY" -H ${JSON.stringify(`Content-Type: ${contentType}`)} \\\n  --data-binary @offer.json\n# offer.json: ${body}\n# This creates the session only. WebRTC media must be established and explicitly stopped by the application.`;
}

/** Voice Live WebRTCはHTTP POSTではなく、専用WSSでSDPを交換します。 */
function voiceLiveRtcOffer(model: RealtimeModel, input: RealtimeInput, sdp: string): Record<string, unknown> {
  return { type: "rtc.call.sdp.create", sdp_offer: sdp, session: { modalities: ["text", "audio"], instructions: REALTIME_CONVERSATION_INSTRUCTIONS, voice: { type: "azure-standard", name: resolveRealtimeVoice(model, input) }, turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 500 }, max_response_output_tokens: resolveRealtimeOutputTokens(model, input), tools: [] } };
}

/** 制御WSSを保持する責務と、音声メディアを運ぶWebRTCの責務を分けた組み込み例です。 */
function voiceLiveWebRtcExample(model: RealtimeModel, input: RealtimeInput, endpoint: string, language: IntegrationLanguage): string {
  const event = JSON.stringify(voiceLiveRtcOffer(model, input, "<browser-generated audio-only SDP offer>"));
  const note = "Voice Live WebRTC exchanges SDP over this protected server WSS. Apply sdp_answer to the browser RTCPeerConnection. Keep the WSS until the authenticated owner stops, then close audio tracks, peer connection, and WSS. No HTTP POST is used.";
  if (language === "typescript") return `// npm install ws\n// ${note}\nimport WebSocket from "ws";\nconst ws = new WebSocket(${JSON.stringify(endpoint)}, { headers: { "api-key": process.env.GATEWAY_API_KEY! }, maxPayload: 262144, followRedirects: false });\nconst timeout = setTimeout(() => ws.close(1000), 60000);\nws.on("open", () => ws.send(${JSON.stringify(event)}));\nws.on("message", raw => {\n  const event = JSON.parse(raw.toString());\n  if (event.type === "rtc.call.sdp.created") { /* Return only event.sdp_answer to the authenticated browser. */ }\n  if (event.type === "rtc.call.error" || event.type === "error") ws.close(1000);\n});\nws.on("close", () => clearTimeout(timeout));\n// The authenticated application stop handler also calls ws.close(1000).`;
  if (language === "python") return `# pip install websockets\n# ${note}\nimport asyncio, json, os\nfrom websockets.asyncio.client import connect\n\nasync def negotiate_sdp(deliver_answer, owner_stopped):\n    async with connect(${JSON.stringify(endpoint)}, additional_headers={"api-key":os.environ["GATEWAY_API_KEY"]}, max_size=262144, open_timeout=10) as ws:\n        await ws.send(${JSON.stringify(event)})\n        async with asyncio.timeout(60):\n            async for raw in ws:\n                event = json.loads(raw)\n                if event.get("type") == "rtc.call.sdp.created":\n                    await deliver_answer(event["sdp_answer"])\n                    break\n                if event.get("type") in ("rtc.call.error", "error"): raise RuntimeError("SDP exchange failed")\n            await owner_stopped.wait()\n# deliver_answer is bound to the authenticated browser; owner_stopped is an asyncio.Event.`;
  if (language === "php") return `<?php\n// Requires Swoole coroutine runtime. ${note}\nuse Swoole\\Coroutine\\Http\\Client;\nuse Swoole\\Coroutine\\Channel;\nfunction negotiateSdp(Channel $ownerStopped, callable $deliverAnswer): void {\n  $key = getenv("GATEWAY_API_KEY");\n  if (!$key) throw new RuntimeException("GATEWAY_API_KEY is required");\n  $url = parse_url(${JSON.stringify(endpoint)});\n  $socket = new Client($url["host"], $url["port"] ?? 443, true);\n  $socket->set(["timeout" => 10, "ssl_verify_peer" => true, "ssl_host_name" => $url["host"], "ssl_allow_self_signed" => false]);\n  $socket->setHeaders(["api-key" => $key]);\n  $deadline = microtime(true) + 60;\n  try {\n    if (!$socket->upgrade($url["path"] . (isset($url["query"]) ? "?" . $url["query"] : ""))) throw new RuntimeException("Gateway connection failed");\n    if (!$socket->push(${JSON.stringify(event)})) throw new RuntimeException("SDP exchange failed");\n    $delivered = false;\n    while (microtime(true) < $deadline) {\n      $frame = $socket->recv(min(10, max(0.001, $deadline - microtime(true))));\n      if (!$frame || !is_string($frame->data) || strlen($frame->data) > 262144) throw new RuntimeException("SDP exchange failed");\n      $event = json_decode($frame->data, true, 32, JSON_THROW_ON_ERROR);\n      if (!is_array($event)) throw new RuntimeException("SDP exchange failed");\n      if (($event["type"] ?? "") === "rtc.call.sdp.created") {\n        $answer = $event["sdp_answer"] ?? null;\n        if (!is_string($answer) || strlen($answer) < 20 || strlen($answer) > 65536 || !str_starts_with($answer, "v=0\\r\\n")) throw new RuntimeException("SDP exchange failed");\n        // The callback must return promptly; the transport deadline cannot preempt application code.\n        $deliverAnswer($answer); $delivered = true; break;\n      }\n      if (in_array($event["type"] ?? "", ["rtc.call.error", "error"], true)) throw new RuntimeException("SDP exchange failed");\n    }\n    if (!$delivered) throw new RuntimeException("SDP exchange failed");\n    // Keep the control WSS until this owner stops or the absolute 60-second limit.\n    $ownerStopped->pop(max(0.001, $deadline - microtime(true)));\n  } finally { $socket->close(); }\n}\n// Call inside Swoole\\Coroutine\\run(). Bind one stop Channel and deliverAnswer to this authenticated owner.\n// deliverAnswer forwards only the SDP answer. The application owns browser audio tracks and RTCPeerConnection cleanup.`;
  if (language === "csharp") return `// ${note}\nusing System.Net.WebSockets;\nusing System.Text;\nusing System.Text.Json;\nusing var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(60));\nusing var ws = new ClientWebSocket();\nws.Options.SetRequestHeader("api-key", Environment.GetEnvironmentVariable("GATEWAY_API_KEY"));\nawait ws.ConnectAsync(new Uri(${JSON.stringify(endpoint)}), timeout.Token);\nawait ws.SendAsync(Encoding.UTF8.GetBytes(${JSON.stringify(event)}), WebSocketMessageType.Text, true, timeout.Token);\n// Receive complete JSON messages with a 256 KiB bound. On rtc.call.sdp.created,\n// send only sdp_answer to the authenticated owner. Keep WSS open until their stop.\n// await authenticatedOwnerStopped; then ws.CloseAsync(...).`;
  return `# ${note}\n# curl can check only the WSS upgrade, not exchange rtc.call.sdp.create frames.\n# A WebSocket application sends this SDP control event after the handshake: ${event}\ncurl --http1.1 --include --no-buffer --max-time 10 ${JSON.stringify(endpoint.replace(/^wss:/, "https:"))} \\\n  -H "api-key: $GATEWAY_API_KEY" -H 'Connection: Upgrade' -H 'Upgrade: websocket' \\\n  -H 'Sec-WebSocket-Version: 13' -H "Sec-WebSocket-Key: $(openssl rand -base64 16)"\n# Use the TypeScript/Python/PHP/C# integration for SDP and media; a 101 response is handshake only.`;
}
