import assert from "node:assert/strict";
import { test } from "node:test";
import { appendRealtimeTranscript, encodeRealtimePcmChunk, getRealtimeSocketUrl, normalizeRealtimeBrowserEvent, readRealtimeSocketMessage, RealtimeMicrophoneQueue, type RealtimeTurn } from "../lib/realtime-client.ts";
import { startRealtimeConversation, RealtimeBrowserError } from "../lib/realtime-browser.ts";
import { createMicrophoneProcessor } from "../lib/realtime-capture-worklet.ts";
import { ModelResponseError } from "../lib/model-response.ts";
import { getRealtimeAudioDictionary } from "../lib/i18n/realtime-audio.ts";
import type { RealtimeModel } from "../lib/model-types.ts";
import { REALTIME_MAX_AUDIO_BYTES, type RealtimeStreamEvent } from "../lib/realtime-request.ts";

const rtcModel: RealtimeModel = { id: "realtime-rtc", label: "Realtime", providerId: "openai", provider: "OpenAI", family: "realtime", transport: "webrtc", apiId: "openai-realtime-control-v1", operationId: "create", path: "/models/openai-realtime/v1/calls", modelId: "gpt-realtime-2.1", voice: "marin", maxRequestBytes: 120_000, maxOutputTokens: 512 };

test("制御WSは同じPortalのCookieを使用し、URLへ認証情報や任意の接続先を含めない", () => {
  assert.equal(getRealtimeSocketUrl("https://portal.example.invalid"), "wss://portal.example.invalid/api/models/realtime/socket");
  assert.equal(getRealtimeSocketUrl("http://localhost:3000"), "ws://localhost:3000/api/models/realtime/socket");
  for (const origin of ["http://public.invalid", "https://user:secret@example.invalid", "https://example.invalid/path", "https://example.invalid?key=secret", "https://example.invalid#secret"]) assert.throws(() => getRealtimeSocketUrl(origin));
});

test("WSフレームは共有JSON契約とサイズを検証し、任意の秘密や上流エラー文を採用しない", () => {
  assert.deepEqual(readRealtimeSocketMessage(JSON.stringify({ type: "status", stage: "connected", elapsedMs: 1, maximumDurationMs: 540_000 })), { type: "status", stage: "connected", elapsedMs: 1, maximumDurationMs: 540_000 });
  for (const raw of ["not-json", "x".repeat(400_001), JSON.stringify({ type: "event", eventType: "session.created", elapsedMs: 1, token: "secret" }), JSON.stringify({ type: "error", error: { code: "GenerationFailed", message: "secret" } }), new ArrayBuffer(8)]) assert.throws(() => readRealtimeSocketMessage(raw), ModelResponseError);
});

test("マイク送信は短いPCM16LEフレームだけを符号化し、0・奇数・大きな入力を拒否する", () => {
  const pcm = new Uint8Array(4_800); pcm[0] = 0x34; pcm[1] = 0x12;
  assert.equal(atob(encodeRealtimePcmChunk(pcm)).charCodeAt(0), 0x34);
  for (const size of [0, 1, 9_602]) assert.throws(() => encodeRealtimePcmChunk(new Uint8Array(size)), ModelResponseError);
});

for (const rate of [24_000, 44_100, 48_000]) test(`Workletは${rate}Hzから24kHz PCM16LEの100msへ変換し、履歴を保存しない`, () => {
  const chunks: { pcm: ArrayBuffer; capturedAt: number }[] = [];
  class Base { port = { postMessage(value: { pcm: ArrayBuffer; capturedAt: number }) { chunks.push(value); } }; }
  const Processor = createMicrophoneProcessor(Base, rate, () => 12.3), processor = new Processor();
  for (let offset = 0; offset < rate / 10; offset += 128) processor.process([[new Float32Array(Math.min(128, rate / 10 - offset)).fill(0.25)]]);
  assert.equal(chunks.length, 1); assert.equal(chunks[0].pcm.byteLength, 4_800); assert.equal(new DataView(chunks[0].pcm).getInt16(0, true), 8_192); assert.equal(chunks[0].capturedAt, 12.3);
});

test("マイクの配送は100msへ整え、接続前・古い音声・3件を超える待機音声を破棄する", () => {
  const queue = new RealtimeMicrophoneQueue();
  const frame = (marker: number, capturedAt: number) => { const pcm = new ArrayBuffer(4_800); new Uint8Array(pcm)[0] = marker; return { pcm, capturedAt }; };
  queue.activate(12); queue.push(frame(1, 11.9), 12); assert.equal(queue.take(12, 0), null);
  queue.push(frame(2, 12.05), 12.1); assert.equal(queue.take(12.1, 0), null);
  queue.push(frame(3, 12.1), 12.1); assert.equal(new Uint8Array(queue.take(12.1, 0)!)[0], 3);
  for (let index = 0; index < 10; index++) queue.push(frame(10 + index, 12.2 + index / 10), 13.1);
  const next = queue.take(13.1, 1_000); assert.ok(next); assert.equal(new Uint8Array(next)[0], 17);
  assert.equal(queue.take(13.1, 1_000), null); assert.equal(queue.take(13.1, 1_094), null);
  assert.ok(queue.take(13.2, 1_100)); assert.equal(queue.take(14, 1_200), null);
  queue.push(frame(30, 14), 14); queue.clear(); assert.equal(queue.take(14, 1_300), null);
  queue.activate(14); queue.push(frame(31, 14), 14); assert.equal(queue.take(14, 1_400), null);
  for (const value of [new ArrayBuffer(4_800), frame(0, 15), { pcm: new ArrayBuffer(9_600), capturedAt: 14.1 }]) assert.throws(() => queue.push(value, 14.1), ModelResponseError);
});

test("9分の独立したWorkletとtimerの微小ジッターでも音声を欠落させず、一括追い付き送信しない", () => {
  for (const jitter of [1, 2, 3]) for (const phase of [0, 20, 50, 90]) {
    const queue = new RealtimeMicrophoneQueue(); queue.activate(0);
    const events: { at: number; frame?: number; capturedAt?: number }[] = [];
    for (let frame = 1; frame <= 5_400; frame++) {
      events.push({ at: frame * 100 + 3 + jitter * Math.sin(frame * 1.73), frame, capturedAt: Math.floor((frame * 2_400 - 1) / 128) * 128 / 24_000 });
      events.push({ at: frame * 100 + phase + jitter * Math.sin(frame * 0.67) });
    }
    events.push({ at: 540_100 }, { at: 540_200 }); events.sort((left, right) => left.at - right.at);
    let lastSentAt = -Infinity; const received: number[] = [];
    for (const event of events) {
      const captureTime = Math.floor(event.at * 24 / 128) * 128 / 24_000;
      if (event.frame !== undefined) { const pcm = new ArrayBuffer(4_800); new DataView(pcm).setUint16(0, event.frame); queue.push({ pcm, capturedAt: event.capturedAt }, captureTime); }
      const pcm = queue.take(captureTime, event.at);
      if (pcm) { assert.ok(event.at - lastSentAt >= 95); lastSentAt = event.at; received.push(new DataView(pcm).getUint16(0)); }
      assert.equal(queue.take(captureTime, event.at), null);
    }
    // 最初のフレームだけは接続直前の音声を含むため捨てます。それ以降の5,399件は維持します。
    assert.equal(received.length, 5_399, `jitter=${jitter}, phase=${phase}`);
    assert.deepEqual(received, Array.from({ length: 5_399 }, (_, index) => index + 2));
    queue.clear(); assert.equal(queue.take(541, 541_000), null);
  }
});

test("字幕は100ターン・64000文字を超えても直前までの会話を切り捨てない", () => {
  let turns: RealtimeTurn[] = [];
  for (const [role, text] of [["input", "こんにちは"], ["output", "こんにちは。"], ["input", "もう一つ質問"], ["output", "どうぞ。"]] as const) turns = appendRealtimeTranscript(turns, { type: "transcript", role, text, final: true });
  assert.deepEqual(turns.map((turn) => turn.text), ["こんにちは", "こんにちは。", "もう一つ質問", "どうぞ。"]);
  let delayed: RealtimeTurn[] = [];
  delayed = appendRealtimeTranscript(delayed, { type: "transcript", role: "input", text: "質問", final: false });
  delayed = appendRealtimeTranscript(delayed, { type: "transcript", role: "output", text: "応答", final: false });
  delayed = appendRealtimeTranscript(delayed, { type: "transcript", role: "input", text: "質問です。", final: true });
  assert.deepEqual(delayed.map((turn) => turn.text), ["質問です。", "応答"]);
  for (let index = 0; index < 200; index++) turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "x".repeat(2_000), final: true });
  assert.equal(turns.length, 204); assert.equal(turns[0].text, "こんにちは"); assert.equal(turns.at(-1)?.text, "x".repeat(2_000));
});

test("無音の空文字・空白だけの字幕通知では発話者だけの行を作らない", () => {
  let turns: RealtimeTurn[] = [];
  for (let index = 0; index < 200; index++) for (const text of ["", " ", "\n\t", "　"]) {
    for (const role of ["input", "output"] as const) for (const final of [false, true]) turns = appendRealtimeTranscript(turns, { type: "transcript", role, text, final });
  }
  assert.deepEqual(turns, []);
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "output", text: "応答の途中", final: false });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: " ", final: true });
  assert.deepEqual(turns.map(({ text, final }) => ({ text, final })), [{ text: "応答の途中", final: false }]);
});

test("空のfinalは部分字幕を消さずに確定し、確定済み本文も空で上書きしない", () => {
  let turns = appendRealtimeTranscript([], { type: "transcript", role: "input", text: "認識できた本文", final: false });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "\n ", final: true });
  assert.equal(turns.length, 1); assert.equal(turns[0].text, "認識できた本文"); assert.equal(turns[0].final, true);
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "", final: true });
  assert.equal(turns.length, 1); assert.equal(turns[0].text, "認識できた本文");
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "次の発話", final: true });
  assert.deepEqual(turns.map((turn) => turn.text), ["認識できた本文", "次の発話"]);
});

test("空通知を挟んでも遅れて届いたfinalを元の字幕へ反映し、単語間の空白は保持する", () => {
  let turns = appendRealtimeTranscript([], { type: "transcript", role: "input", text: "質問", final: false });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "output", text: "Hello", final: false });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "", final: true });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "output", text: " ", final: false });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "output", text: "world", final: false });
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "input", text: "質問の確定文", final: true });
  assert.deepEqual(turns.map((turn) => turn.text), ["質問の確定文", "Hello world"]);
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "output", text: " ", final: true });
  assert.equal(turns[1].text, "Hello world"); assert.equal(turns[1].final, true);
});

test("既存の空行を除去し、大量の空白deltaでも認識済みの本文を失わない", () => {
  let turns: RealtimeTurn[] = [{ id: 1, role: "input", text: " ", final: true }, { id: 2, role: "output", text: "重要な本文", final: false }];
  turns = appendRealtimeTranscript(turns, { type: "transcript", role: "output", text: " ".repeat(16_000), final: false });
  assert.equal(turns.length, 1); assert.equal(turns[0].text, "重要な本文" + " ".repeat(16_000));
});

test("WSとRTCの16000文字超字幕を同じ内容で受け付ける", () => {
  const text = "合成の長い字幕🙂".repeat(2500);
  const expected = { type: "transcript", role: "output", text, final: true };
  assert.deepEqual(readRealtimeSocketMessage(JSON.stringify(expected)), expected);
  const rtc = normalizeRealtimeBrowserEvent(JSON.stringify({ type: "response.output_audio_transcript.done", transcript: text }), 1);
  assert.deepEqual(rtc.find((event) => event.type === "transcript"), expected);
});

test("応答のcompleted/cancelled/incomplete/failedは会話の終了イベントへ変えない", () => {
  for (const status of ["completed", "cancelled", "incomplete", "failed"]) {
    const events = normalizeRealtimeBrowserEvent(JSON.stringify({ type: "response.done", response: { status, usage: { total_tokens: 10 } } }), 1);
    assert.equal(events.some((event) => event.type === "error" || event.type === "complete"), false);
    assert.equal(events.some((event) => event.type === "event" && event.eventType === "response.failed"), status === "failed");
  }
  const safe = normalizeRealtimeBrowserEvent(JSON.stringify({ type: "session.updated", session: { sdp: "private", token: "secret", id: "private" } }), 2);
  assert.deepEqual(safe, [{ type: "event", eventType: "session.updated", elapsedMs: 2 }]);
});

/** 実マイク・実通信を一切使わず、会話の開始・継続・停止を再現します。 */
function installBrowserFixture(transport: "webrtc" | "websocket", permission: "allow" | "deny" | "pending" = "allow") {
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const facts = { permissionCalls: 0, micStops: 0, remoteStops: 0, played: 0, audioPaused: 0, contextClosed: 0, peerClosed: 0, channelClosed: 0, captureDisconnected: 0, outputStopped: 0, scheduledSamples: 0, activeOutput: 0, maximumActiveOutput: 0, frames: [] as Record<string, unknown>[], dataFrames: [] as Record<string, unknown>[] };
  let channel: Channel;
  const refs = { socket: null as Socket | null, node: null as Worklet | null, context: null as Context | null };
  let permit!: (stream: Stream) => void;
  class Track extends EventTarget { enabled = true; kind = "audio"; remote: boolean; constructor(remote = false) { super(); this.remote = remote; } stop() { if (this.remote) facts.remoteStops++; else facts.micStops++; } }
  const mic = new Track();
  class Stream { tracks: Track[]; constructor(tracks: Track[]) { this.tracks = tracks; } getTracks() { return this.tracks; } getAudioTracks() { return this.tracks; } }
  class AudioSink { srcObject: unknown; muted = false; play() { assert.equal(this.muted, false); facts.played++; return Promise.resolve(); } pause() { facts.audioPaused++; } }
  class Channel extends EventTarget { readyState = "connecting"; emit(value: unknown) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); } send(raw: string) { const event = JSON.parse(raw); facts.dataFrames.push(event); if (event.type === "session.update") queueMicrotask(() => this.emit({ type: "session.updated" })); } close() { facts.channelClosed++; this.readyState = "closed"; this.dispatchEvent(new Event("close")); } }
  class Peer extends EventTarget {
    connectionState = "new"; iceGatheringState = "complete"; localDescription: { sdp: string } | null = null;
    createDataChannel() { channel = new Channel(); return channel; } addTrack() { /* fakeマイクだけを接続します。 */ }
    createOffer() { return Promise.resolve({ type: "offer", sdp: "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n" }); }
    setLocalDescription(value: { sdp: string }) { this.localDescription = value; return Promise.resolve(); }
    setRemoteDescription() { this.connectionState = "connected"; channel.readyState = "open"; this.dispatchEvent(Object.assign(new Event("track"), { track: new Track(true), streams: [] })); return Promise.resolve(); }
    close() { facts.peerClosed++; this.connectionState = "closed"; }
  }
  const outputSources: Source[] = [];
  class Source extends EventTarget {
    buffer: { length: number } | null = null; endsAt = Infinity; ended = false;
    connect() { /* 端末では再生しません。 */ } disconnect() { /* fake出力を外します。 */ }
    start(when = 0) { facts.scheduledSamples += this.buffer?.length ?? 0; facts.activeOutput++; facts.maximumActiveOutput = Math.max(facts.maximumActiveOutput, facts.activeOutput); this.endsAt = when + (this.buffer?.length ?? 0) / 24000; outputSources.push(this); }
    finish() { if (this.ended) return; this.ended = true; facts.activeOutput--; this.dispatchEvent(new Event("ended")); }
    stop() { facts.outputStopped++; this.finish(); }
  }
  class Context {
    state = "running"; currentTime = 0; destination = {}; audioWorklet = { addModule: async (path: string) => { assert.equal(path, "/realtime-audio-worklet.js"); } };
    constructor() { refs.context = this; }
    resume() { return Promise.resolve(); } close() { facts.contextClosed++; return Promise.resolve(); }
    createMediaStreamSource() { return { connect() { /* fakeマイクをWorkletへ接続します。 */ }, disconnect() { /* fake入力を外します。 */ } }; }
    createBuffer(_channels: number, frames: number) { return { length: frames, getChannelData: () => new Float32Array(frames) }; } createBufferSource() { return new Source(); }
  }
  class Worklet { port: { onmessage: ((event: MessageEvent) => void) | null } = { onmessage: null }; constructor() { refs.node = this; } connect() { /* 出力は無音です。 */ } disconnect() { facts.captureDisconnected++; } }
  class Socket extends EventTarget {
    static OPEN = 1; static CLOSED = 3; readyState = 0; bufferedAmount = 0;
    constructor(url: string) { super(); assert.equal(url, "ws://localhost:3000/api/models/realtime/socket"); refs.socket = this; queueMicrotask(() => { this.readyState = 1; this.dispatchEvent(new Event("open")); }); }
    emit(value: unknown) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
    send(raw: string) { const event = JSON.parse(raw); facts.frames.push(event); if (event.type === "start") queueMicrotask(() => { if (transport === "webrtc") this.emit({ type: "answer", sdp: "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n", handle: "h".repeat(80) }); this.emit({ type: "status", stage: "connected", elapsedMs: 5, maximumDurationMs: 540_000 }); }); if (event.type === "stop") queueMicrotask(() => this.emit({ type: "complete", durationMs: 50, inputAudioBytes: 0, outputAudioBytes: 0, stopStatus: "confirmed" })); }
    close() { if (this.readyState === 3) return; this.readyState = 3; this.dispatchEvent(new Event("close")); }
  }
  const replacements = { navigator: { mediaDevices: { getUserMedia: async () => { facts.permissionCalls++; if (permission === "deny") throw new DOMException("private details", "NotAllowedError"); if (permission === "pending") return new Promise<Stream>((resolve) => { permit = resolve; }); return new Stream([mic]); } } }, location: { origin: "http://localhost:3000" }, WebSocket: Socket, RTCPeerConnection: Peer, Audio: AudioSink, MediaStream: Stream, AudioContext: Context, AudioWorkletNode: Worklet };
  for (const [name, value] of Object.entries(replacements)) { saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name)); Object.defineProperty(globalThis, name, { configurable: true, writable: true, value }); }
  return { facts, mic, socket: () => refs.socket!, channel: () => channel, capture: () => refs.node!, context: () => refs.context!, advancePlayback(seconds: number) { refs.context!.currentTime = seconds; for (const source of outputSources) if (source.endsAt <= seconds) source.finish(); }, permit: () => permit(new Stream([mic])), restore() { for (const [name, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else Reflect.deleteProperty(globalThis, name); } } };
}

for (const family of ["realtime", "voice-live"] as const) test(`${family}のWSは30秒応答の先行受信で切断せず、短い再生予約で全PCMを消費する`, async () => {
  const fixture = installBrowserFixture("websocket"), controller = new AbortController(), events: RealtimeStreamEvent[] = [];
  const model = { ...rtcModel, family, transport: "websocket" as const };
  try {
    const conversation = await startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    const second = btoa("\0".repeat(48000));
    for (let index = 0; index < 30; index++) fixture.socket().emit({ type: "audio", delta: second, sampleRate: 24000 });
    fixture.socket().emit({ type: "event", eventType: "response.done", elapsedMs: 1 });
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(events.some((event) => event.type === "error"), false);
    assert.equal(fixture.facts.micStops, 0);
    assert.ok(fixture.facts.maximumActiveOutput <= 21, "AudioBufferへ9分分を展開して予約しない");
    for (let second = 1; second <= 32; second++) { fixture.advancePlayback(second); await new Promise((resolve) => setTimeout(resolve, 1)); }
    assert.equal(fixture.facts.scheduledSamples, 30 * 24000, "受信したPCMを落とさず一度だけ再生する");
    assert.equal(events.some((event) => event.type === "error"), false);
    await conversation.stop(); assert.equal(fixture.facts.micStops, 1); assert.equal(fixture.facts.activeOutput, 0);
  } finally { controller.abort(); fixture.restore(); }
});

test("WSの累積PCMが旧境界を超えても継続し、利用者の停止でマイクと再生待ちを解放する", async () => {
  const fixture = installBrowserFixture("websocket"), controller = new AbortController(), events: RealtimeStreamEvent[] = [];
  try {
    const model = { ...rtcModel, transport: "websocket" as const };
    const conversation = await startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    const packet = btoa("\0".repeat(250_000));
    let total = 0;
    while (total < REALTIME_MAX_AUDIO_BYTES) {
      const bytes = Math.min(250_000, REALTIME_MAX_AUDIO_BYTES - total);
      fixture.socket().emit({ type: "audio", delta: bytes === 250_000 ? packet : btoa("\0".repeat(bytes)), sampleRate: 24000 });
      total += bytes;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    assert.equal(events.some((event) => event.type === "error"), false); assert.equal(fixture.facts.micStops, 0);
    assert.ok(fixture.facts.maximumActiveOutput <= 21);
    fixture.socket().emit({ type: "audio", delta: btoa("\0\0"), sampleRate: 24000 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(events.some((event) => event.type === "error"), false);
    await conversation.stop();
    const complete = await conversation.completion;
    assert.equal(complete.outputAudioBytes, REALTIME_MAX_AUDIO_BYTES + 2);
    assert.equal(fixture.facts.micStops, 1); assert.equal(fixture.facts.activeOutput, 0); assert.equal(fixture.facts.contextClosed, 1);
  } finally { controller.abort(); fixture.restore(); }
});

test("長いWS応答への割込みは予約済み音声と未予約PCMを両方捨て、次の応答だけを再生する", async () => {
  const fixture = installBrowserFixture("websocket"), controller = new AbortController();
  try {
    const model = { ...rtcModel, transport: "websocket" as const };
    const conversation = await startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: () => undefined });
    for (let index = 0; index < 30; index++) fixture.socket().emit({ type: "audio", delta: btoa("\0".repeat(48000)), sampleRate: 24000 });
    await new Promise((resolve) => setTimeout(resolve, 20));
    fixture.socket().emit({ type: "event", eventType: "input_audio_buffer.speech_started", elapsedMs: 1 });
    await new Promise((resolve) => setTimeout(resolve, 5));
    const previous = fixture.facts.scheduledSamples;
    fixture.socket().emit({ type: "audio", delta: btoa("\0".repeat(48000)), sampleRate: 24000 });
    await new Promise((resolve) => setTimeout(resolve, 5));
    fixture.advancePlayback(10); await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(fixture.facts.scheduledSamples - previous, 24000);
    await conversation.stop(); assert.equal(fixture.facts.activeOutput, 0);
  } finally { controller.abort(); fixture.restore(); }
});

for (const transport of ["webrtc", "websocket"] as const) test(`${transport}は複数ターンで自動終了せず、ミュートと利用者停止で全音声を解放する`, async () => {
  const fixture = installBrowserFixture(transport); const controller = new AbortController();
  const model = { ...rtcModel, transport }; const events: RealtimeStreamEvent[] = [];
  try {
    const conversation = await startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    assert.equal(fixture.facts.permissionCalls, 1); assert.equal(fixture.mic.enabled, true);
    conversation.setMuted(true); assert.equal(fixture.mic.enabled, false); conversation.setMuted(false); assert.equal(fixture.mic.enabled, true);
    if (transport === "webrtc") {
      for (const text of ["一回目", "二回目"]) { fixture.channel().dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ type: "response.output_audio_transcript.done", transcript: text }) })); fixture.channel().dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ type: "response.done", response: { status: "completed" } }) })); }
      assert.equal(fixture.facts.played, 1);
    } else {
      fixture.context().currentTime = 0.2; fixture.capture().port.onmessage?.(new MessageEvent("message", { data: { pcm: new ArrayBuffer(4_800), capturedAt: 0.2 } }));
      assert.equal(fixture.facts.frames.filter((frame) => frame.type === "input_audio").length, 1);
      for (const text of ["一回目", "二回目"]) { fixture.socket().emit({ type: "transcript", role: "output", text, final: true }); fixture.socket().emit({ type: "event", eventType: "response.done", elapsedMs: 10 }); }
      fixture.socket().emit({ type: "audio", delta: btoa("\0".repeat(4_800)), sampleRate: 24_000 });
      await new Promise((resolve) => setTimeout(resolve, 5)); fixture.socket().emit({ type: "event", eventType: "input_audio_buffer.speech_started", elapsedMs: 15 });
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(fixture.facts.frames.some((frame) => frame.type === "stop"), false);
    assert.equal(fixture.facts.micStops, 0);
    assert.equal(events.filter((event) => event.type === "transcript").length, 2);
    const result = await conversation.stop(); assert.equal(result.stopStatus, "confirmed");
    assert.equal(fixture.facts.micStops, 1);
    if (transport === "webrtc") { assert.equal(fixture.facts.peerClosed, 1); assert.equal(fixture.facts.remoteStops, 1); assert.equal(fixture.facts.audioPaused, 1); }
    else { assert.equal(fixture.facts.contextClosed, 1); assert.equal(fixture.facts.outputStopped, 1); assert.equal(fixture.capture().port.onmessage, null); }
  } finally { controller.abort(); fixture.restore(); }
});

for (const transport of ["webrtc", "websocket"] as const) test(`${transport}はパネルを閉じるAbortで直ちに音声を解放し、重複停止や遅い通知で再開しない`, async () => {
  const fixture = installBrowserFixture(transport), controller = new AbortController(), events: RealtimeStreamEvent[] = [];
  let queuedCapture: ((event: MessageEvent) => void) | null = null;
  try {
    const model = { ...rtcModel, transport };
    const conversation = await startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    if (transport === "websocket") {
      fixture.context().currentTime = 0.2; queuedCapture = fixture.capture().port.onmessage;
      queuedCapture?.(new MessageEvent("message", { data: { pcm: new ArrayBuffer(4_800), capturedAt: 0.2 } }));
      fixture.context().currentTime = 0.3; queuedCapture?.(new MessageEvent("message", { data: { pcm: new ArrayBuffer(4_800), capturedAt: 0.3 } }));
      fixture.socket().emit({ type: "audio", delta: btoa("\0".repeat(4_800)), sampleRate: 24_000 });
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    // パネルのuseEffect cleanupと同じく、まずAbortしてからstopも呼びます。
    controller.abort(); const cleanup = conversation.stop(); controller.abort();
    assert.equal(fixture.facts.micStops, 1);
    assert.equal(fixture.facts.frames.filter((frame) => frame.type === "stop").length, 1);
    if (transport === "webrtc") {
      assert.equal(fixture.facts.remoteStops, 1); assert.equal(fixture.facts.peerClosed, 1); assert.equal(fixture.facts.channelClosed, 1); assert.equal(fixture.channel().readyState, "closed"); assert.equal(fixture.facts.audioPaused, 1);
    } else {
      assert.equal(fixture.facts.contextClosed, 1); assert.equal(fixture.facts.captureDisconnected, 1); assert.equal(fixture.capture().port.onmessage, null); assert.equal(fixture.facts.outputStopped, 1);
    }
    assert.equal((await cleanup).stopStatus, "confirmed"); assert.equal(await conversation.stop(), await conversation.completion);
    const sentFrames = fixture.facts.frames.length, visibleEvents = events.length;
    if (transport === "websocket") {
      fixture.context().currentTime = 1;
      queuedCapture?.(new MessageEvent("message", { data: { pcm: new ArrayBuffer(4_800), capturedAt: 1 } }));
      fixture.socket().emit({ type: "audio", delta: btoa("\0".repeat(4_800)), sampleRate: 24_000 });
    } else fixture.channel().emit({ type: "response.created" });
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.equal(fixture.facts.frames.length, sentFrames); assert.equal(events.length, visibleEvents);
    assert.equal(fixture.facts.frames.filter((frame) => frame.type === "start").length, 1); assert.equal(fixture.facts.frames.filter((frame) => frame.type === "stop").length, 1);
  } finally { controller.abort(); fixture.restore(); }
});

test("許可待ち後や音声再生時のWorkletまとめ配送でも、古い音声を捨て100msずつ送る", async () => {
  const fixture = installBrowserFixture("websocket", "pending"), controller = new AbortController(), events: RealtimeStreamEvent[] = [];
  try {
    const model = { ...rtcModel, family: "voice-live" as const, transport: "websocket" as const };
    const pending = startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    fixture.context().currentTime = 50; fixture.permit(); const conversation = await pending;
    fixture.socket().emit({ type: "audio", delta: btoa("\0".repeat(4_800)), sampleRate: 24_000 }); await new Promise((resolve) => setTimeout(resolve, 5));
    fixture.context().currentTime = 50.5;
    for (const capturedAt of [49.9, 50.05, 50.2, 50.3, 50.4, 50.5]) fixture.capture().port.onmessage?.(new MessageEvent("message", { data: { pcm: new ArrayBuffer(4_800), capturedAt } }));
    const frames = () => fixture.facts.frames.filter((frame) => frame.type === "input_audio");
    assert.equal(frames().length, 1); assert.equal(atob(String(frames()[0].delta)).length, 4_800);
    fixture.context().currentTime = 50.6; await new Promise((resolve) => setTimeout(resolve, 120));
    assert.ok(frames().length <= 2); assert.equal(events.some((event) => event.type === "error"), false); assert.equal(fixture.facts.micStops, 0);
    await conversation.stop(); const sent = frames().length; await new Promise((resolve) => setTimeout(resolve, 120)); assert.equal(frames().length, sent);
  } finally { controller.abort(); fixture.restore(); }
});

test("RTCは応答中だけ取消を送り、無応答中と取消待ちのクリックで会話を止めない", async () => {
  const fixture = installBrowserFixture("webrtc"), controller = new AbortController();
  try {
    const conversation = await startRealtimeConversation({ model: rtcModel, input: { modelId: rtcModel.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: () => undefined });
    const cancellations = () => fixture.facts.dataFrames.filter((frame) => frame.type === "response.cancel");
    conversation.cancelResponse(); assert.equal(cancellations().length, 0);
    fixture.channel().emit({ type: "response.created" }); conversation.cancelResponse(); conversation.cancelResponse(); assert.equal(cancellations().length, 1);
    fixture.channel().emit({ type: "response.done", response: { status: "completed" } }); conversation.cancelResponse(); assert.equal(cancellations().length, 1);
    fixture.channel().emit({ type: "response.created" }); conversation.cancelResponse(); assert.equal(cancellations().length, 2);
    assert.notEqual(cancellations()[0].event_id, cancellations()[1].event_id); assert.equal(fixture.facts.micStops, 0);
    await conversation.stop();
  } finally { controller.abort(); fixture.restore(); }
});

for (const withEventId of [true, false]) test(`RTCは明示取消と完了の競合だけを許容し、次の応答を続ける（相関ID${withEventId ? "あり" : "省略"}）`, async () => {
  const fixture = installBrowserFixture("webrtc"), controller = new AbortController(), events: RealtimeStreamEvent[] = [];
  try {
    const conversation = await startRealtimeConversation({ model: rtcModel, input: { modelId: rtcModel.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    fixture.channel().emit({ type: "response.created" }); conversation.cancelResponse();
    const cancellation = fixture.facts.dataFrames.find((frame) => frame.type === "response.cancel")!;
    fixture.channel().emit({ type: "response.done", response: { status: "completed" } });
    fixture.channel().emit({ type: "error", error: { code: "response_cancel_not_active", message: "private upstream details", ...(withEventId ? { event_id: cancellation.event_id } : {}) } });
    fixture.channel().emit({ type: "response.created" }); fixture.channel().emit({ type: "response.output_audio_transcript.done", transcript: "会話を継続できます。" });
    assert.equal(events.some((event) => event.type === "error" || event.type === "complete"), false); assert.equal(JSON.stringify(events).includes("private"), false);
    assert.equal(events.some((event) => event.type === "transcript" && event.text === "会話を継続できます。"), true); assert.equal(fixture.facts.micStops, 0);
    await conversation.stop();
  } finally { controller.abort(); fixture.restore(); }
});

for (const scenario of ["not-requested", "wrong-code", "wrong-event", "consumed", "new-turn", "expired"] as const) test(`RTCは取消競合に該当しないエラーを握りつぶさず接続を解放する（${scenario}）`, async (context) => {
  const fixture = installBrowserFixture("webrtc"), controller = new AbortController(), events: RealtimeStreamEvent[] = [];
  try {
    const conversation = await startRealtimeConversation({ model: rtcModel, input: { modelId: rtcModel.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: (event) => events.push(event) });
    fixture.channel().emit({ type: "response.created" });
    if (scenario !== "not-requested") conversation.cancelResponse();
    const cancellation = fixture.facts.dataFrames.find((frame) => frame.type === "response.cancel");
    const error = { type: "error", error: { code: scenario === "wrong-code" ? "other_error" : "response_cancel_not_active", event_id: scenario === "wrong-event" ? "unrelated" : cancellation?.event_id, message: "private upstream details" } };
    if (scenario === "consumed") fixture.channel().emit(error);
    if (scenario === "new-turn") { fixture.channel().emit({ type: "response.done", response: { status: "completed" } }); fixture.channel().emit({ type: "response.created" }); }
    if (scenario === "expired") { const now = performance.now(); context.mock.method(performance, "now", () => now + 6_000); }
    fixture.channel().emit(error); await conversation.completion;
    assert.equal(events.some((event) => event.type === "error" && event.error.code === "GenerationFailed"), true); assert.equal(JSON.stringify(events).includes("private"), false);
    assert.equal(fixture.facts.micStops, 1); assert.equal(fixture.facts.peerClosed, 1);
  } finally { controller.abort(); fixture.restore(); }
});

test("マイク許可を拒否した場合は秘密の例外文を出さず、WS接続を作らない", async () => {
  const fixture = installBrowserFixture("webrtc", "deny");
  try { await assert.rejects(startRealtimeConversation({ model: rtcModel, input: { modelId: rtcModel.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: new AbortController().signal, onEvent: () => undefined }), (error: unknown) => error instanceof RealtimeBrowserError && error.reason === "MicrophoneDenied" && !error.message.includes("private")); assert.equal(fixture.facts.frames.length, 0); }
  finally { fixture.restore(); }
});

test("許可ダイアログ中に中止し、後から許可されてもマイクを使わず直ちに解放する", async () => {
  const fixture = installBrowserFixture("webrtc", "pending"), controller = new AbortController();
  try { const pending = startRealtimeConversation({ model: rtcModel, input: { modelId: rtcModel.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: () => undefined }); controller.abort(); await assert.rejects(pending); fixture.permit(); await new Promise((resolve) => setTimeout(resolve, 5)); assert.equal(fixture.facts.micStops, 1); assert.equal(fixture.facts.frames.length, 0); }
  finally { fixture.restore(); }
});

test("開始前に中止済みならマイクの許可も接続も要求しない", async () => {
  const fixture = installBrowserFixture("webrtc"), controller = new AbortController(); controller.abort();
  try { await assert.rejects(startRealtimeConversation({ model: rtcModel, input: { modelId: rtcModel.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: () => undefined })); assert.equal(fixture.facts.permissionCalls, 0); }
  finally { fixture.restore(); }
});

test("予期しない制御切断でもマイクと受信音声を停止し、自動再接続しない", async () => {
  const fixture = installBrowserFixture("websocket"), controller = new AbortController();
  try { const model = { ...rtcModel, transport: "websocket" as const }; const conversation = await startRealtimeConversation({ model, input: { modelId: model.id, maxOutputTokens: 256 }, apiKeyId: "test-key", signal: controller.signal, onEvent: () => undefined }); fixture.socket().close(); const result = await conversation.completion; assert.equal(result.stopStatus, "unconfirmed"); assert.equal(fixture.facts.micStops, 1); assert.equal(fixture.facts.contextClosed, 1); assert.equal(fixture.facts.frames.filter((frame) => frame.type === "start").length, 1); }
  finally { controller.abort(); fixture.restore(); }
});

test("会話画面は両言語が揃い、固定音声テストの説明を残さない", () => {
  const ja = getRealtimeAudioDictionary("ja"), en = getRealtimeAudioDictionary("en"); assert.deepEqual(Object.keys(ja), Object.keys(en)); assert.ok(Object.values(ja).every(Boolean)); assert.ok(Object.values(en).every(Boolean)); assert.equal(JSON.stringify(ja).includes("固定サンプル"), false); assert.equal(JSON.stringify(en).includes("fixed sample"), false);
});

test("リアルタイムの入力エラーは音声と接続の案内にし、通常生成のStream設定を促さない", () => {
  for (const locale of ["ja", "en"] as const) { const dictionary = getRealtimeAudioDictionary(locale); assert.ok(dictionary.InvalidRequest); assert.doesNotMatch(dictionary.InvalidRequest, /Stream/); }
});
