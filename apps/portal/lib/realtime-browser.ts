import type { RealtimeModel } from "./model-types.ts";
import { ModelResponseError } from "./model-response.ts";
import { REALTIME_MAX_AUDIO_BYTES, REALTIME_MAX_DURATION_MS, type RealtimeInput, type RealtimeStreamEvent } from "./realtime-request.ts";
import { decodeRealtimeAudio } from "./realtime-audio.ts";
import { encodeRealtimePcmChunk, getRealtimeSocketUrl, normalizeRealtimeBrowserEvent, readRealtimeSocketMessage, stopRealtimeBrowser, RealtimeMicrophoneQueue, type RealtimeCompletion } from "./realtime-client.ts";
import { buildRealtimeSessionEvent } from "./realtime-protocol.ts";

/** ブラウザーの許可・対応状況を、任意の例外本文を表示せず案内するための分類です。 */
export type RealtimeBrowserFailure = "MicrophoneDenied" | "MicrophoneUnavailable" | "BrowserUnsupported";
/** マイクの拒否など、GatewayのHTTPエラーとは別に案内すべき失敗です。 */
export class RealtimeBrowserError extends Error {
  readonly reason: RealtimeBrowserFailure;
  constructor(reason: RealtimeBrowserFailure) { super(reason); this.name = "RealtimeBrowserError"; this.reason = reason; }
}
/** 利用者の操作で会話を継続・終了するための接続ハンドルです。秘密値は公開しません。 */
export type RealtimeConversation = { completion: Promise<RealtimeCompletion>; stop(): Promise<RealtimeCompletion>; setMuted(muted: boolean): void; play(): Promise<void>; cancelResponse(): void };

/** 通信の失敗を、翻訳可能な固定エラーへ揃えます。 */
function failed(): ModelResponseError { return new ModelResponseError({ error: { code: "GenerationFailed" } }); }
/** ブラウザーAPIの待機も、利用者の取消と接続準備の期限へ連動させます。 */
async function during<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted(); let listener: (() => void) | undefined;
  try { return await Promise.race([promise, new Promise<never>((_, reject) => { listener = () => reject(signal.reason); signal.addEventListener("abort", listener, { once: true }); })]); }
  finally { if (listener) signal.removeEventListener("abort", listener); }
}
/** ICEやデータチャネルを、切断・期限・取消を含めて待ちます。 */
async function waitFor(check: () => boolean, targets: { target: EventTarget; events: string[] }[], signal: AbortSignal, milliseconds = 15_000): Promise<void> {
  await during(new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => finish(failed()), milliseconds);
    /** 監視を外し、過去の接続からの通知を残しません。 */
    function finish(error?: unknown) { clearTimeout(timer); for (const { target, events } of targets) for (const event of events) target.removeEventListener(event, inspect); signal.removeEventListener("abort", abort); if (error) reject(error); else resolve(); }
    /** 接続状態の変化だけを確認します。 */
    function inspect() { try { if (check()) finish(); } catch (error) { finish(error); } }
    /** 取消時にも同じ後始末を行います。 */
    function abort() { finish(signal.reason); }
    for (const { target, events } of targets) for (const event of events) target.addEventListener(event, inspect);
    signal.addEventListener("abort", abort, { once: true }); inspect();
  }), signal);
}

/**
 * マイクとAIの双方向音声を開始し、利用者が停止するまで複数の発話を継続します。
 * @remarks 会話の録音保存や自動再接続は行いません。キーはBFFで解決し、認証済みPortalの同一オリジンWSだけへ接続します。
 */
export async function startRealtimeConversation(options: { model: RealtimeModel; input: RealtimeInput; apiKeyId: string; signal: AbortSignal; onEvent(event: RealtimeStreamEvent): void; onPlaybackBlocked?(blocked: boolean): void }): Promise<RealtimeConversation> {
  const { model, input, apiKeyId, signal, onEvent, onPlaybackBlocked } = options;
  signal.throwIfAborted();
  if (!globalThis.navigator?.mediaDevices?.getUserMedia || typeof WebSocket === "undefined" || model.transport === "webrtc" && typeof RTCPeerConnection === "undefined") throw new RealtimeBrowserError("BrowserUnsupported");
  const started = performance.now(); const elapsed = () => Math.max(0, Math.round(performance.now() - started));
  const control = new AbortController(); const active = AbortSignal.any([signal, control.signal]);
  let socket: WebSocket | null = null, peer: RTCPeerConnection | null = null, channel: RTCDataChannel | null = null;
  let microphone: MediaStream | null = null, remote: MediaStream | null = null, audio: HTMLAudioElement | null = null;
  let context: AudioContext | null = null, captureSource: MediaStreamAudioSourceNode | null = null, capture: AudioWorkletNode | null = null;
  const playing = new Set<AudioBufferSourceNode>(); let playhead = 0;
  let playbackTimer: ReturnType<typeof setInterval> | undefined;
  let pendingPlayback: (Uint8Array | undefined)[] = [], playbackIndex = 0, playbackOffset = 0, pendingPlaybackBytes = 0;
  const microphoneQueue = new RealtimeMicrophoneQueue(); let microphoneTimer: ReturnType<typeof setInterval> | undefined;
  let muted = false, connected = false, stopping = false, finished = false, readySettled = false, sessionUpdated = false;
  let responseActive = false, cancellationSequence = 0;
  let requestedCancellation: { eventId: string; expiresAt: number } | undefined;
  let maximumDurationMs = REALTIME_MAX_DURATION_MS, inputBytes = 0, outputBytes = 0, handle: string | undefined;
  let serverComplete: RealtimeCompletion | undefined, stopTask: Promise<RealtimeCompletion> | undefined;
  let sessionTimer: ReturnType<typeof setTimeout> | undefined;
  let resolveReady!: (conversation: RealtimeConversation) => void, rejectReady!: (reason: unknown) => void, resolveComplete!: (event: RealtimeCompletion) => void, resolveAck!: (event: RealtimeCompletion | null) => void;
  const ready = new Promise<RealtimeConversation>((resolve, reject) => { resolveReady = resolve; rejectReady = reject; }); void ready.catch(() => undefined);
  const completion = new Promise<RealtimeCompletion>((resolve) => { resolveComplete = resolve; });
  const ack = new Promise<RealtimeCompletion | null>((resolve) => { resolveAck = resolve; });
  let frameWindow = performance.now(), frameCount = 0, pendingMessages = 0;
  let messageQueue = Promise.resolve();

  /** 再生待ちの音声を消し、取消後に古い応答を再生しません。 */
  function clearPlayback() {
    if (playbackTimer) clearInterval(playbackTimer); playbackTimer = undefined;
    pendingPlayback = []; playbackIndex = 0; playbackOffset = 0; pendingPlaybackBytes = 0;
    for (const node of playing) { try { node.stop(); } catch { /* 自然終了済みも同じ経路で解放します。 */ } node.disconnect(); }
    playing.clear(); playhead = context?.currentTime ?? 0;
  }
  /** 停止クリックと同時に端末のマイク・スピーカー・接続を止めます。 */
  function releaseMedia() {
    if (microphoneTimer) clearInterval(microphoneTimer); microphoneQueue.clear();
    microphone?.getTracks().forEach((track) => track.stop()); remote?.getTracks().forEach((track) => track.stop());
    if (capture) { capture.port.onmessage = null; capture.disconnect(); } captureSource?.disconnect(); clearPlayback();
    channel?.close(); peer?.close(); if (audio) { audio.pause(); audio.srcObject = null; }
    if (context) void context.close().catch(() => undefined);
  }
  /** BFFに返す操作は決められたJSONだけに限定し、送信待ちが増え続ける接続を終了します。 */
  function send(value: Record<string, unknown>) {
    if (!socket || socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 262_144) throw failed();
    socket.send(JSON.stringify(value));
  }
  /** 応答一件の完了では呼ばず、停止・切断・致命的エラーでだけ会話を終了します。 */
  function end(error?: unknown, sendStop = true): Promise<RealtimeCompletion> {
    if (stopTask) return stopTask;
    stopping = true; connected = false;
    if (setupTimer) clearTimeout(setupTimer); if (sessionTimer) clearTimeout(sessionTimer);
    signal.removeEventListener("abort", abort);
    releaseMedia();
    if (error && !(error instanceof DOMException && error.name === "AbortError")) onEvent({ type: "error", error: { code: error instanceof ModelResponseError ? error.failure.error.code : "GenerationFailed" } });
    onEvent({ type: "status", stage: "stopping", elapsedMs: elapsed() });
    if (!readySettled) { readySettled = true; rejectReady(error ?? new DOMException("Stopped", "AbortError")); }
    stopTask = (async () => {
      if (sendStop && socket?.readyState === WebSocket.OPEN) { try { send({ type: "stop" }); } catch { /* 終了確認を待ち、必要なら所有参照で再確認します。 */ } }
      if (!serverComplete && socket?.readyState === WebSocket.OPEN) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        try { await Promise.race([ack, new Promise<void>((resolve) => { timer = setTimeout(resolve, 8_000); })]); } finally { if (timer) clearTimeout(timer); }
      }
      let stopStatus = serverComplete?.stopStatus ?? (model.family === "realtime" ? "unconfirmed" : "transport-closed");
      if (handle && stopStatus !== "confirmed") { try { stopStatus = await stopRealtimeBrowser(handle); } catch { stopStatus = "unconfirmed"; } }
      socket?.close(); control.abort(); finished = true;
      const event: RealtimeCompletion = { type: "complete", durationMs: elapsed(), inputAudioBytes: inputBytes, outputAudioBytes: outputBytes, stopStatus, ...(handle && stopStatus === "unconfirmed" ? { handle } : {}) };
      onEvent(event); resolveComplete(event); return event;
    })();
    return stopTask;
  }
  /** 利用者による中止は、接続準備中でもマイクの解放へつなげます。 */
  function abort() { void end(new DOMException("Stopped", "AbortError")); }
  /** 出力音声の再生拒否だけは接続を破棄せず、利用者の再生操作を受け付けます。 */
  async function play() {
    try { if (context) await context.resume(); if (audio?.srcObject) await audio.play(); onPlaybackBlocked?.(false); }
    catch { onPlaybackBlocked?.(true); }
  }
  /** 認証・メディア双方の準備が揃った時点で、マイク送信を有効にします。 */
  function markReady() {
    if (stopping || readySettled) return;
    connected = true; readySettled = true; if (setupTimer) clearTimeout(setupTimer);
    microphone?.getAudioTracks().forEach((track) => { track.enabled = !muted; });
    if (context) { microphoneQueue.activate(context.currentTime); microphoneTimer = setInterval(deliverMicrophone, 100); }
    sessionTimer = setTimeout(() => void end(), maximumDurationMs);
    onEvent({ type: "status", stage: "connected", elapsedMs: elapsed(), maximumDurationMs });
    resolveReady(conversation);
  }
  /** メインスレッドに遅延があっても、古い録音を連続送信せず一定の間隔へ戻します。 */
  function deliverMicrophone() {
    if (!connected || stopping || !context) return;
    try {
      const pcm = microphoneQueue.take(context.currentTime, performance.now()); if (!pcm) return;
      const bytes = new Uint8Array(pcm); send({ type: "input_audio", delta: encodeRealtimePcmChunk(bytes), sampleRate: 24_000 }); inputBytes += bytes.length;
    } catch (error) { void end(error); }
  }
  /**
   * 先読み2秒だけをAudioBufferへ展開し、生成が再生より速い長い応答も順に再生します。
   * @remarks 未再生PCMは会話の累積音声上限内だけメモリーへ置き、消費した領域は直ちに解放します。
   */
  function schedulePlayback() {
    if (!context || stopping) return;
    while (pendingPlaybackBytes > 0) {
      const availableSamples = Math.floor((2 - (Math.max(playhead, context.currentTime) - context.currentTime)) * 24_000 + 0.00001);
      if (availableSamples <= 0) break;
      const bytes = pendingPlayback[playbackIndex]!;
      const length = Math.min(bytes.length - playbackOffset, 4_800, availableSamples * 2);
      const buffer = context.createBuffer(1, length / 2, 24_000), samples = buffer.getChannelData(0);
      const data = new DataView(bytes.buffer, bytes.byteOffset + playbackOffset, length);
      for (let index = 0; index < samples.length; index++) samples[index] = data.getInt16(index * 2, true) / 32_768;
      const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination); playing.add(source);
      source.addEventListener("ended", () => {
        playing.delete(source); source.disconnect();
        try { schedulePlayback(); } catch (error) { void end(error); }
      }, { once: true });
      playhead = Math.max(playhead, context.currentTime); source.start(playhead); playhead += length / 48_000;
      playbackOffset += length; pendingPlaybackBytes -= length;
      if (playbackOffset === bytes.length) { pendingPlayback[playbackIndex++] = undefined; playbackOffset = 0; }
      if (playbackIndex === pendingPlayback.length) { pendingPlayback = []; playbackIndex = 0; }
      else if (playbackIndex > 256 && playbackIndex * 2 > pendingPlayback.length) { pendingPlayback = pendingPlayback.slice(playbackIndex); playbackIndex = 0; }
    }
    if (!pendingPlaybackBytes && !playing.size && playbackTimer) { clearInterval(playbackTimer); playbackTimer = undefined; }
  }
  /** 受信量は9分の既存上限へ束縛し、再生速度の違いを通信エラーへ置き換えません。 */
  function playPcm(base64: string) {
    if (!context || stopping) return;
    const bytes = decodeRealtimeAudio(base64);
    if (outputBytes + bytes.length > REALTIME_MAX_AUDIO_BYTES || pendingPlaybackBytes + bytes.length > REALTIME_MAX_AUDIO_BYTES) throw failed();
    outputBytes += bytes.length; pendingPlaybackBytes += bytes.length; pendingPlayback.push(bytes);
    schedulePlayback();
    if (!playbackTimer) playbackTimer = setInterval(() => { try { schedulePlayback(); } catch (error) { void end(error); } }, 100);
    if (context.state !== "running") onPlaybackBlocked?.(true);
  }
  /** RTCデータチャネルの準備後に設定を送り、複数の発話を維持します。 */
  async function activatePeer() {
    const current = peer, data = channel; if (!current || !data) throw failed();
    await waitFor(() => { if (["failed", "closed"].includes(current.connectionState)) throw failed(); return current.connectionState === "connected" && data.readyState === "open"; }, [{ target: current, events: ["connectionstatechange"] }, { target: data, events: ["open", "close"] }], active);
    const configuration = buildRealtimeSessionEvent(model, input);
    if (configuration) { data.send(JSON.stringify(configuration)); await waitFor(() => sessionUpdated, [{ target: data, events: ["message"] }], active, 10_000); }
    markReady();
  }
  /** フレームは一件ずつ処理し、SDPや認証参照を画面側へ流しません。 */
  async function receive(event: RealtimeStreamEvent) {
    if (finished) return;
    if (event.type === "complete") { serverComplete = event; handle = event.handle ?? handle; resolveAck(event); void end(undefined, false); return; }
    if (event.type === "error") { handle = event.handle ?? handle; onEvent(event); void end(new ModelResponseError({ error: event.error }), false); return; }
    if (stopping) return;
    if (event.type === "answer") { handle = event.handle; if (!peer) throw failed(); await during(peer.setRemoteDescription({ type: "answer", sdp: event.sdp }), active); void activatePeer().catch((error) => void end(error)); return; }
    if (event.type === "audio") { if (model.transport !== "websocket") throw failed(); playPcm(event.delta); return; }
    if (event.type === "status") {
      if (event.maximumDurationMs !== undefined) maximumDurationMs = event.maximumDurationMs;
      if (event.stage === "connected") { if (model.transport === "websocket") markReady(); return; }
      onEvent({ ...event, elapsedMs: elapsed() }); return;
    }
    if (model.transport === "webrtc" && (event.type === "transcript" || event.type === "usage")) return;
    if (event.type === "event" && ["input_audio_buffer.speech_started", "response.cancelled"].includes(event.eventType)) clearPlayback();
    onEvent(event.type === "event" ? { ...event, elapsedMs: elapsed() } : event);
  }
  /** 自分が要求した取消と応答完了の短い競合だけを許容し、ほかのサービスエラーは終了へつなげます。 */
  function receivePeer(raw: unknown) {
    if (stopping) return;
    if (typeof raw !== "string") throw failed();
    const values = normalizeRealtimeBrowserEvent(raw, elapsed());
    if (values.some((value) => value.type === "error") && requestedCancellation && performance.now() <= requestedCancellation.expiresAt) {
      const service = JSON.parse(raw) as { type?: unknown; error?: { code?: unknown; event_id?: unknown } };
      if (service.type === "error" && service.error?.code === "response_cancel_not_active" && (service.error.event_id === undefined || service.error.event_id === requestedCancellation.eventId)) {
        requestedCancellation = undefined; responseActive = false;
        onEvent({ type: "event", eventType: "response.cancel.not-active", elapsedMs: elapsed() }); return;
      }
    }
    for (const value of values) {
      if (value.type === "event") {
        if (value.eventType === "session.updated") sessionUpdated = true;
        if (value.eventType === "response.created") { responseActive = true; requestedCancellation = undefined; }
        if (value.eventType === "response.done") responseActive = false;
      }
      if (value.type === "error") void end(new ModelResponseError({ error: value.error })); else onEvent(value);
    }
  }
  const conversation: RealtimeConversation = {
    completion, stop: () => end(), play,
    setMuted(value) { muted = value; microphone?.getAudioTracks().forEach((track) => { track.enabled = connected && !muted; }); if (context && connected) microphoneQueue.activate(context.currentTime); },
    cancelResponse() {
      if (!connected || stopping) return; clearPlayback();
      if (["live", "transcription"].includes(model.family)) return;
      try {
        if (model.transport === "webrtc") {
          if (!responseActive || channel?.readyState !== "open" || requestedCancellation && performance.now() <= requestedCancellation.expiresAt) return;
          requestedCancellation = { eventId: `egakumi_cancel_${++cancellationSequence}`, expiresAt: performance.now() + 5_000 };
          channel.send(JSON.stringify({ type: "response.cancel", event_id: requestedCancellation.eventId }));
        } else send({ type: "cancel_response" });
      } catch (error) { void end(error); }
    },
  };

  signal.addEventListener("abort", abort, { once: true });
  const setupTimer = setTimeout(() => void end(new ModelResponseError({ error: { code: "GatewayTimeout" } })), 90_000);
  try {
    if (model.transport === "websocket") {
      if (typeof AudioContext === "undefined" || typeof AudioWorkletNode === "undefined") throw new RealtimeBrowserError("BrowserUnsupported");
      context = new AudioContext({ sampleRate: 24_000 }); void context.resume().catch(() => onPlaybackBlocked?.(true));
    } else audio = new Audio();
    const permission = navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    void permission.then((stream) => { if (stopping || active.aborted) stream.getTracks().forEach((track) => track.stop()); }).catch(() => undefined);
    try { microphone = await during(permission, active); }
    catch (error) { if (active.aborted) throw active.reason; throw new RealtimeBrowserError(error instanceof DOMException && ["NotAllowedError", "SecurityError"].includes(error.name) ? "MicrophoneDenied" : "MicrophoneUnavailable"); }
    for (const track of microphone.getAudioTracks()) { track.enabled = false; track.addEventListener("ended", () => { if (!stopping) void end(new RealtimeBrowserError("MicrophoneUnavailable")); }); }
    let sdp: string | undefined;
    if (model.transport === "webrtc") {
      peer = new RTCPeerConnection(); channel = peer.createDataChannel(model.family === "voice-live" ? "voice-live-events" : "oai-events");
      for (const track of microphone.getTracks()) peer.addTrack(track, microphone);
      peer.addEventListener("track", (event) => { if (event.track.kind !== "audio" || stopping) return; remote = event.streams[0] ?? new MediaStream([event.track]); if (audio) { audio.srcObject = remote; void play(); } });
      peer.addEventListener("connectionstatechange", () => { if (!stopping && peer?.connectionState === "failed") void end(failed()); });
      channel.addEventListener("message", (event) => { try { receivePeer(event.data); } catch (error) { void end(error); } });
      channel.addEventListener("error", () => { if (!stopping) void end(failed()); }); channel.addEventListener("close", () => { if (!stopping) void end(failed()); });
      await during(peer.setLocalDescription(await during(peer.createOffer(), active)), active);
      await waitFor(() => peer?.iceGatheringState === "complete", [{ target: peer, events: ["icegatheringstatechange"] }], active);
      sdp = peer.localDescription?.sdp; if (!sdp) throw failed();
    } else {
      const current = context!; if (!current.audioWorklet) throw new RealtimeBrowserError("BrowserUnsupported");
      await during(current.audioWorklet.addModule("/realtime-audio-worklet.js"), active);
      captureSource = current.createMediaStreamSource(microphone); capture = new AudioWorkletNode(current, "egakumi-microphone"); captureSource.connect(capture); capture.connect(current.destination);
      capture.port.onmessage = (event: MessageEvent) => { if (!connected || stopping) return; try { microphoneQueue.push(event.data, current.currentTime); deliverMicrophone(); } catch (error) { void end(error); } };
    }
    active.throwIfAborted(); socket = new WebSocket(getRealtimeSocketUrl(location.origin));
    socket.addEventListener("open", () => { try { send({ type: "start", ...input, apiKeyId, ...(sdp ? { sdp } : {}) }); } catch (error) { void end(error); } });
    socket.addEventListener("message", (message) => {
      if (finished) return;
      const now = performance.now(); if (now - frameWindow > 1_000) { frameWindow = now; frameCount = 0; }
      if (++frameCount > 400 || ++pendingMessages > 64) { void end(failed()); return; }
      messageQueue = messageQueue.then(() => receive(readRealtimeSocketMessage(message.data))).catch((error) => { void end(error); }).finally(() => { pendingMessages--; });
    });
    socket.addEventListener("error", () => { if (!stopping) void end(failed(), false); });
    socket.addEventListener("close", () => { resolveAck(null); if (!stopping) void end(failed(), false); });
    onEvent({ type: "status", stage: "connecting", elapsedMs: elapsed() });
  } catch (error) { await end(error); throw error; }
  return ready;
}
