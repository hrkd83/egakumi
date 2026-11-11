import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import type WebSocket from "ws";
import { runRealtimeConversation, type RealtimeConversationCommand, type RealtimeConversationDependencies, type RealtimeConversationDiagnostic } from "../lib/realtime-gateway.ts";
import { decodeRealtimeAudio } from "../lib/realtime-audio.ts";
import { buildRealtimeSessionEvent, RealtimeProtocol, REALTIME_CONVERSATION_INSTRUCTIONS } from "../lib/realtime-protocol.ts";
import { defaultRealtimeInput, getRealtimeEndpoint, getRealtimeIntegrationCode, getRealtimeProtocolGuide, getRealtimeRequestExample, getRealtimeRequestSchema, getRealtimeResponseSchema } from "../lib/realtime-integration.ts";
import { REALTIME_MAX_AUDIO_BYTES, REALTIME_MAX_EVENTS, realtimeExecutionSchema, type RealtimeInput, type RealtimeStreamEvent } from "../lib/realtime-request.ts";
import type { RealtimeModel } from "../lib/model-types.ts";

const realtime: RealtimeModel = { id: "realtime", label: "Realtime", providerId: "openai", provider: "OpenAI", family: "realtime", transport: "websocket", apiId: "openai-realtime-ws-v1", operationId: "invoke", path: "/models/openai-realtime/v1/ws", modelId: "gpt-realtime-2.1", voice: "marin", maxRequestBytes: 65536, maxOutputTokens: 512 };
const live: RealtimeModel = { ...realtime, id: "live", label: "Live", family: "live", apiId: "openai-live-ws-v1", path: "/models/openai-live/v1/ws", modelId: "gpt-live-1" };
const transcription: RealtimeModel = { ...realtime, id: "transcription", family: "transcription", apiId: "openai-transcription-ws-v1", path: "/models/openai-transcription/v1/ws", modelId: "gpt-live-transcribe" };
const voice: RealtimeModel = { ...realtime, id: "voice", family: "voice-live", apiId: "azure-voice-live-ws-v1", path: "/models/voice-live/v1/ws", modelId: "gpt-6.1-sol", voice: "ja-JP-NanamiNeural" };
const models = [realtime, live, transcription, voice];
const key = "test-only-realtime-private-key"; const gateway = "https://gateway.example.invalid";
const chunk = Buffer.alloc(4800).toString("base64");

/** ネットワークだけを差し替え、送信イベント・終了・切断を観測します。 */
class FakeSocket extends EventEmitter {
  readyState = 0; bufferedAmount = 0; sent: Record<string, unknown>[] = []; closes = 0; terminated = 0;
  onSend: (value: Record<string, unknown>) => void = () => undefined;
  constructor() { super(); setTimeout(() => { if (this.readyState === 0) { this.readyState = 1; this.emit("open"); } }, 0); }
  send(text: string, callback: (error?: Error) => void) { const value = JSON.parse(text); this.sent.push(value); callback(); this.onSend(value); }
  event(value: Record<string, unknown>) { this.emit("message", Buffer.from(JSON.stringify(value)), false); }
  close() { this.closes++; this.readyState = 3; queueMicrotask(() => this.emit("close")); }
  terminate() { this.terminated++; this.readyState = 3; queueMicrotask(() => this.emit("close")); }
}

/** 認可後の継続会話を作り、ブラウザー側の開始・入力・停止を試験から操作します。 */
function conversation(model = realtime, dependencies: RealtimeConversationDependencies = {}, input: RealtimeInput = defaultRealtimeInput(model)) {
  const controller = new AbortController(); const events: RealtimeStreamEvent[] = []; const diagnostics: RealtimeConversationDiagnostic[] = [];
  let receive: (command: RealtimeConversationCommand) => void = () => undefined; let removed = 0; let socket: FakeSocket;
  let connected: () => void = () => undefined; const ready = new Promise<void>((resolve) => { connected = resolve; });
  const promise = runRealtimeConversation(model, input, getRealtimeEndpoint(model, gateway), key, controller.signal,
    (event) => { events.push(event); if (event.type === "status" && event.stage === "connected") connected(); },
    (handler) => { receive = handler; return () => { removed++; }; }, { timeoutMs: 500, stopTimeoutMs: 5, diagnose: (value) => diagnostics.push(value), connect: (url, options) => {
      assert.equal(url, getRealtimeEndpoint(model, gateway)); assert.equal(options.followRedirects, false); assert.equal(options.maxPayload, 262144); assert.deepEqual(options.headers, { "api-key": key, "User-Agent": "egakumi-portal-realtime/1.0" });
      socket = new FakeSocket(); socket.onSend = (value) => {
        if (value.type === "session.update" || value.type === "session.start") queueMicrotask(() => socket.event({ type: model.family === "live" ? "session.started" : "session.updated", session: { model: model.modelId, id: "never-public" } }));
        if (value.type === "session.close") queueMicrotask(() => socket.event({ type: "session.closed", usage: { total_tokens: 9 } }));
        if (value.type === "response.cancel") queueMicrotask(() => socket.event({ type: "response.done", response: { status: "cancelled" } }));
      }; return socket as unknown as WebSocket;
    }, ...dependencies });
  // エラーの試験でも、呼出側が検査するまでunhandled rejectionにしません。
  void promise.catch(() => undefined);
  return { controller, promise, ready, events, diagnostics, send: (command: RealtimeConversationCommand) => receive(command), socket: () => socket!, removed: () => removed };
}

test("開始設定は自然な日本語会話とVADを使い、WSSにだけPCM24kを指定する", () => {
  assert.ok(REALTIME_CONVERSATION_INSTRUCTIONS.includes("日本語で自然に会話")); assert.ok(!REALTIME_CONVERSATION_INSTRUCTIONS.includes("only OK"));
  type Config = { session: { audio: { input: { format?: object; turn_detection: object }; output: { format?: object; voice: string } } } };
  const ws = buildRealtimeSessionEvent(realtime, defaultRealtimeInput(realtime)) as Config;
  assert.deepEqual(ws.session.audio.input.format, { type: "audio/pcm", rate: 24000 }); assert.equal((ws.session.audio.input.turn_detection as { create_response: boolean }).create_response, true);
  const rtc = buildRealtimeSessionEvent({ ...realtime, transport: "webrtc" }, defaultRealtimeInput(realtime)) as Config;
  assert.equal(rtc.session.audio.input.format, undefined); assert.equal(rtc.session.audio.output.format, undefined); assert.equal(rtc.session.audio.output.voice, realtime.voice);
  assert.equal(buildRealtimeSessionEvent(live, defaultRealtimeInput(live))?.type, "session.start");
  const voiceSettings = JSON.stringify(buildRealtimeSessionEvent(voice, defaultRealtimeInput(voice))); assert.ok(voiceSettings.includes("azure-standard")); assert.ok(voiceSettings.includes("ja-JP-NanamiNeural")); assert.ok(voiceSettings.includes("server_vad"));
  const asr = JSON.stringify(buildRealtimeSessionEvent(transcription, defaultRealtimeInput(transcription))); assert.ok(asr.includes('"type":"transcription"')); assert.ok(asr.includes("gpt-live-transcribe")); assert.ok(asr.includes('"turn_detection":null')); assert.ok(!asr.includes("server_vad"));
});

test("選択音声をWSS開始設定とRealtimeのDataChannel設定へ反映する", async () => {
  const rtc = { ...realtime, transport: "webrtc" as const };
  const rtcStart = buildRealtimeSessionEvent(rtc, { ...defaultRealtimeInput(rtc), voice: "cedar" }) as { session: { audio: { output: { voice: string } } } };
  assert.equal(rtcStart.session.audio.output.voice, "cedar");
  for (const [model, selectedVoice] of [[realtime, "cedar"], [voice, "ja-JP-KeitaNeural"], [live, "marin"]] as const) {
    const run = conversation(model, {}, { ...defaultRealtimeInput(model), voice: selectedVoice });
    try {
      await run.ready;
      const session = run.socket().sent[0].session as { audio?: { output: { voice: string } }; voice?: { name: string } };
      assert.equal(model.family === "voice-live" ? session.voice?.name : session.audio?.output.voice, selectedVoice);
      run.send({ type: "stop" }); await run.promise;
    } finally { run.controller.abort(); }
  }
});

test("未知・別系列・文字起こしへの音声指定は上流WSSを開く前に拒否する", async () => {
  let connected = 0;
  for (const [model, selectedVoice] of [[realtime, "unregistered"], [realtime, "ja-JP-NanamiNeural"], [voice, "cedar"], [transcription, "marin"], [realtime, " cedar"], [realtime, "a".repeat(151)]] as const) {
    await assert.rejects(runRealtimeConversation(model, { ...defaultRealtimeInput(model), voice: selectedVoice }, getRealtimeEndpoint(model, gateway), key,
      new AbortController().signal, () => undefined, () => () => undefined, { connect: () => { connected++; throw new Error("Unexpected connection"); } }));
  }
  assert.equal(connected, 0);
});

test("接続先・モデル・秘密のURL埋め込み・任意イベントを拒否する", async () => {
  assert.equal(getRealtimeEndpoint(realtime, gateway), `${gateway.replace("https:", "wss:")}${realtime.path}?model=gpt-realtime-2.1`);
  for (const invalid of [{ ...realtime, path: "/models/other" }, { ...realtime, modelId: "unregistered" }, { ...live, apiId: realtime.apiId }]) assert.throws(() => getRealtimeEndpoint(invalid, gateway));
  assert.throws(() => getRealtimeEndpoint(realtime, "https://user:secret@example.invalid"));
  for (const extra of [{ audio: chunk }, { event: { type: "response.create" } }, { url: gateway }, { key }]) assert.equal(realtimeExecutionSchema.safeParse({ ...defaultRealtimeInput(realtime), apiKeyId: "portal-11111111-1111-4111-8111-111111111111", ...extra }).success, false);
  await assert.rejects(runRealtimeConversation(realtime, defaultRealtimeInput(realtime), "wss://other.invalid" + realtime.path, key, new AbortController().signal, () => undefined, () => () => undefined));
});

test("PCMフレームは線形にBase64と偶数バイト境界を検査する", () => {
  assert.equal(decodeRealtimeAudio(chunk).length, 4800);
  for (const invalid of ["", "!AAA", "A===", "AQ==", "A".repeat(Math.ceil(REALTIME_MAX_AUDIO_BYTES / 3) * 4 + 4)]) assert.throws(() => decodeRealtimeAudio(invalid));
});

test("複数のresponse.doneと取消・出力枠不足でもセッションを閉じない", async () => {
  const run = conversation(); await run.ready;
  try {
    for (const status of ["completed", "cancelled", "incomplete", "failed"]) {
      run.socket().event({ type: "response.created" }); run.socket().event({ type: "response.output_audio.delta", delta: chunk });
      run.socket().event({ type: "response.done", response: { status, usage: { input_tokens: 10, output_tokens: 2 } } });
      assert.equal(run.socket().closes, 0); assert.equal(run.events.some((event) => event.type === "complete"), false);
    }
    run.send({ type: "input_audio", delta: chunk, sampleRate: 24000 });
    assert.equal(run.socket().sent.filter((event) => event.type === "input_audio_buffer.append").length, 1);
    assert.equal(run.socket().sent.some((event) => ["input_audio_buffer.commit", "response.create"].includes(String(event.type))), false);
    run.send({ type: "stop" }); await run.promise; assert.equal(run.socket().closes, 1); assert.equal(run.removed(), 1); assert.equal(run.events.filter((event) => event.type === "complete").length, 1);
  } finally { run.controller.abort(); }
});

test("文字起こしは複数itemを扱い、commit前のdeltaは同じitemの受付後だけ公開する", () => {
  const state = new RealtimeProtocol(transcription); state.accept({ type: "session.updated", session: {} }, 0);
  for (const item of ["turn-1", "turn-2"]) {
    const early = state.accept({ type: "conversation.item.input_audio_transcription.delta", item_id: item, content_index: 0, delta: "日本語" }, 1); assert.equal(early.some((event) => event.type === "transcript"), false);
    const commit = state.accept({ type: "input_audio_buffer.committed", item_id: item }, 2); assert.ok(commit.some((event) => event.type === "transcript" && event.text === "日本語"));
    state.accept({ type: "conversation.item.input_audio_transcription.completed", item_id: item, content_index: 0, transcript: "日本語の会話です。" }, 3); assert.equal(state.terminal, false);
  }
  assert.throws(() => state.accept({ type: "conversation.item.input_audio_transcription.delta", item_id: "turn-1", content_index: 0, delta: "replayed" }, 4));
});

test("VAD非対応の文字起こしは約300msごとにcommitし、停止時の残り音声も確定する", async () => {
  const run = conversation(transcription); await run.ready; let commits = 0;
  const original = run.socket().onSend;
  run.socket().onSend = (event) => {
    original(event);
    if (event.type === "input_audio_buffer.commit") { const item = `item-${++commits}`; queueMicrotask(() => { run.socket().event({ type: "input_audio_buffer.committed", item_id: item }); run.socket().event({ type: "conversation.item.input_audio_transcription.completed", item_id: item, content_index: 0, transcript: "認識しました" }); }); }
  };
  for (let index = 0; index < 3; index++) run.send({ type: "input_audio", delta: chunk });
  await new Promise<void>((resolve) => setTimeout(resolve, 100));
  assert.equal(commits, 1); assert.equal(run.socket().closes, 0); assert.equal(run.events.some((event) => event.type === "complete"), false);
  run.send({ type: "input_audio", delta: chunk }); run.send({ type: "stop" }); await run.promise;
  assert.equal(commits, 2); assert.equal(run.events.filter((event) => event.type === "transcript" && event.final).length, 2); assert.equal(run.socket().closes, 1);
});

test("Liveは音声を受信しても継続し、利用者の停止時だけsession.closeと最終usageを確認する", async () => {
  const run = conversation(live); await run.ready;
  run.socket().event({ type: "session.output_audio.delta", delta: chunk }); run.socket().event({ type: "session.usage.updated", usage: { total_tokens: 3 } });
  assert.equal(run.socket().sent.some((event) => event.type === "session.close"), false);
  run.send({ type: "input_audio", delta: chunk }); assert.ok(run.socket().sent.some((event) => event.type === "session.input_audio.append"));
  run.send({ type: "stop" }); run.send({ type: "input_audio", delta: chunk }); await run.promise;
  assert.ok(run.socket().sent.some((event) => event.type === "session.close")); assert.ok(run.events.some((event) => event.type === "complete" && event.stopStatus === "confirmed")); assert.ok(run.events.filter((event) => event.type === "usage").length >= 2);
});

test("応答取消は現在の応答だけを中断し、次の発話と明示停止を受け付ける", async () => {
  const run = conversation(); await run.ready; run.socket().event({ type: "response.created" }); run.send({ type: "cancel_response" }); await new Promise<void>((resolve) => setTimeout(resolve, 0));
  assert.ok(run.socket().sent.some((event) => event.type === "response.cancel")); assert.equal(run.socket().closes, 0);
  run.send({ type: "input_audio", delta: chunk }); run.send({ type: "stop" }); await run.promise;
  const liveRun = conversation(live); await liveRun.ready; liveRun.send({ type: "cancel_response" }); assert.equal(liveRun.socket().sent.some((event) => event.type === "response.cancel"), false); liveRun.send({ type: "stop" }); await liveRun.promise;
});

test("過大フレーム・音声レート超過・送信待ち過大は無制限に蓄積しない", async () => {
  for (const mode of ["frame", "rate", "backpressure"] as const) {
    // 実行マシンの負荷で同一バースト中に許容量が補充されないよう、単調増加時計を固定します。
    const run = conversation(realtime, { now: () => 0 }); await run.ready;
    if (mode === "backpressure") run.socket().bufferedAmount = 128_001;
    if (mode === "rate") for (let index = 0; index < 21; index++) run.send({ type: "input_audio", delta: chunk });
    else run.send({ type: "input_audio", delta: mode === "frame" ? Buffer.alloc(9602).toString("base64") : chunk });
    await assert.rejects(run.promise); assert.ok(run.socket().closes + run.socket().terminated > 0); assert.equal(run.removed(), 1);
  }
});

test("音声レートの許容量は経過時間で補充し、補充分を超える同時入力だけを拒否する", async () => {
  let now = 0;
  const run = conversation(realtime, { now: () => now }); await run.ready;
  for (let index = 0; index < 20; index++) run.send({ type: "input_audio", delta: chunk });
  assert.equal(run.socket().sent.filter((event) => event.type === "input_audio_buffer.append").length, 20);
  // 100ms分の補充で1フレームを受け付けます。さらに時計を進めなければ次は拒否します。
  now = 100;
  run.send({ type: "input_audio", delta: chunk });
  assert.equal(run.socket().sent.filter((event) => event.type === "input_audio_buffer.append").length, 21);
  assert.equal(run.socket().closes, 0);
  run.send({ type: "input_audio", delta: chunk });
  await assert.rejects(run.promise, (error: unknown) => error instanceof Error && error.message === "InvalidRequest");
  assert.equal(run.diagnostics.at(-1)?.reason, "audio-rate-exceeded");
  assert.equal(run.diagnostics.at(-1)?.availableBytes, 960);
  assert.equal(run.removed(), 1);
});

test("応答再生時にまとめ配送された音声を許容し、2秒分を超える入力は安全な理由だけで拒否する", async () => {
  const run = conversation(voice); await run.ready;
  run.socket().event({ type: "response.created" }); run.socket().event({ type: "response.audio.delta", delta: chunk });
  // 旧300ms枠では4つ目が失敗していました。メインスレッド待ち後の同一tick配送を再現します。
  for (let index = 0; index < 4; index++) run.send({ type: "input_audio", delta: chunk, sampleRate: 24000 });
  assert.equal(run.socket().sent.filter((event) => event.type === "input_audio_buffer.append").length, 4); assert.equal(run.socket().closes, 0); assert.deepEqual(run.diagnostics, []);
  run.socket().event({ type: "response.done", response: { status: "completed" } }); run.send({ type: "stop" }); await run.promise;

  const excess = conversation(voice, { now: () => 0 }); await excess.ready;
  for (let index = 0; index < 21; index++) excess.send({ type: "input_audio", delta: chunk, sampleRate: 24000 });
  await assert.rejects(excess.promise, (error: unknown) => error instanceof Error && error.message === "InvalidRequest");
  assert.equal(excess.socket().sent.filter((event) => event.type === "input_audio_buffer.append").length, 20);
  assert.equal(excess.diagnostics.length, 1);
  const diagnostic = excess.diagnostics[0];
  assert.equal(diagnostic.reason, "audio-rate-exceeded"); assert.equal(diagnostic.family, "voice-live"); assert.equal(diagnostic.transport, "websocket"); assert.equal(diagnostic.frameBytes, 4800); assert.equal(diagnostic.inputAudioBytes, 96_000); assert.ok((diagnostic.availableBytes ?? 4800) < 4800);
  assert.deepEqual(Object.keys(diagnostic).sort(), ["reason", "family", "transport", "elapsedMs", "inputAudioBytes", "frameBytes", "availableBytes"].sort());
  const logged = JSON.stringify(excess.diagnostics); assert.ok(!logged.includes(key)); assert.ok(!logged.includes(chunk)); assert.ok(!logged.includes("never-public")); assert.ok(!logged.includes("apiKeyId")); assert.ok(!logged.includes("modelId"));
  assert.ok(excess.socket().closes + excess.socket().terminated > 0); assert.equal(excess.removed(), 1);
});

for (const model of [realtime, voice]) test(`${model.family}の30秒応答をPCM bridgeが欠落なく送り、発話完了後も次のマイク入力を受ける`, async () => {
  const run = conversation(model); await run.ready;
  const second = Buffer.alloc(48_000).toString("base64");
  run.socket().event({ type: "response.created" });
  for (let index = 0; index < 30; index++) run.socket().event({ type: "response.audio.delta", delta: second });
  run.socket().event({ type: "response.done", response: { status: "completed" } });
  assert.equal(run.events.filter((event) => event.type === "audio").length, 30);
  assert.equal(run.events.some((event) => event.type === "complete" || event.type === "error"), false);
  assert.equal(run.socket().closes, 0); assert.equal(run.removed(), 0);
  run.send({ type: "input_audio", delta: chunk, sampleRate: 24_000 });
  assert.equal(run.socket().sent.filter((event) => event.type === "input_audio_buffer.append").length, 1);
  run.send({ type: "stop" }); await run.promise;
  const result = run.events.find((event) => event.type === "complete");
  assert.equal(result?.type === "complete" && result.outputAudioBytes, 30 * 48_000);
  assert.equal(run.removed(), 1);
});

test("生のID・キー・上流エラーを転送せず、不正イベント・切断を失敗として解放する", async () => {
  for (const mode of ["disconnect", "binary", "secret", "failed"] as const) {
    const run = conversation(); await run.ready;
    if (mode === "disconnect") run.socket().close();
    else if (mode === "binary") run.socket().emit("message", Buffer.alloc(8), true);
    else run.socket().event(mode === "secret" ? { type: "unknown", echoed: key } : { type: "error", error: { message: key } });
    await assert.rejects(run.promise); assert.ok(!JSON.stringify(run.events).includes(key)); assert.ok(!JSON.stringify(run.events).includes("never-public")); assert.equal(run.removed(), 1);
  }
});

test("取消と接続時間上限は確実に後始末し、自動再接続しない", async () => {
  const abort = conversation(); await abort.ready; abort.controller.abort(); await assert.rejects(abort.promise, (error: unknown) => error instanceof Error && error.message === "RequestCancelled"); assert.equal(abort.removed(), 1);
  const deadline = conversation(realtime, { timeoutMs: 10 }); await deadline.promise; assert.ok(deadline.events.some((event) => event.type === "error" && event.error.code === "GatewayTimeout")); assert.equal(deadline.socket().closes, 1);
  const preAborted = new AbortController(); preAborted.abort(); let calls = 0;
  await assert.rejects(runRealtimeConversation(realtime, defaultRealtimeInput(realtime), getRealtimeEndpoint(realtime, gateway), key, preAborted.signal, () => undefined, () => { calls++; return () => undefined; })); assert.equal(calls, 0);
});

test("未知イベントを公開せず、旧累積件数上限を超えても正常に継続する", () => {
  const state = new RealtimeProtocol(realtime); assert.deepEqual(state.accept({ type: "unknown", body: key }, 0), []);
  for (let index = 1; index < REALTIME_MAX_EVENTS; index++) state.accept({ type: "unknown" }, 0);
  assert.deepEqual(state.accept({ type: "unknown" }, 0), []);
});

test("WebRTCのスキーマはSDP・track・製品別停止を示し、WSS会話ガイドはVADと複数発話を示す", () => {
  const rtc: RealtimeModel = { ...realtime, transport: "webrtc", apiId: "openai-realtime-control-v1", path: "/models/openai-realtime/v1/calls", observer: { apiId: "openai-realtime-observer-v1", path: "/models/openai-realtime/v1/observer/" }, hangupPath: "/models/openai-realtime/v1/calls/{callId}/hangup" };
  const liveRtc: RealtimeModel = { ...live, transport: "webrtc", apiId: "openai-live-control-v1", path: "/models/openai-live/v1/sessions" };
  const voiceRtc: RealtimeModel = { ...voice, transport: "webrtc", apiId: "azure-voice-live-webrtc-v1", path: "/models/voice-live/v1/webrtc/" };
  assert.equal(getRealtimeRequestSchema(rtc).contentMediaType, "application/json"); assert.equal(getRealtimeResponseSchema(rtc).contentMediaType, "application/sdp"); assert.equal(getRealtimeResponseSchema(rtc).httpStatus, 201);
  assert.deepEqual(getRealtimeResponseSchema(liveRtc).required, ["sessionId", "sdp"]); assert.equal((getRealtimeResponseSchema(liveRtc).stop as { gatewayHangupAvailable: boolean }).gatewayHangupAvailable, false);
  assert.deepEqual(getRealtimeResponseSchema(voiceRtc).required, ["type", "sdp_answer"]); assert.ok(getRealtimeEndpoint(voiceRtc, gateway).startsWith("wss:"));
  const rtcBody = JSON.parse(getRealtimeRequestExample(rtc, defaultRealtimeInput(rtc))); assert.equal(typeof rtcBody.body.sdp, "string");
  for (const model of models) {
    const guide = getRealtimeProtocolGuide(model); assert.ok(!guide.includes("固定サンプル")); assert.ok(guide.includes("停止"));
    for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) { const code = getRealtimeIntegrationCode(model, defaultRealtimeInput(model), gateway, language); assert.ok(code.includes("GATEWAY_API_KEY")); assert.ok(!code.includes("speech-ja-jp")); assert.ok(!code.includes(key)); }
  }
});
