import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { openRealtimeCallHandle, sealRealtimeCallHandle, type RealtimeCallHandle } from "../lib/realtime-call-handle.ts";
import { handleRealtimeStop, handleRealtimeWebRtc, validateRealtimeSdp, type RealtimeControlSocket, type RealtimeWebRtcDependencies } from "../lib/realtime-webrtc.ts";
import { realtimeStreamEventSchema, type RealtimeStreamEvent } from "../lib/realtime-request.ts";
import type { RealtimeModel } from "../lib/model-types.ts";
import { REALTIME_CONVERSATION_INSTRUCTIONS } from "../lib/realtime-protocol.ts";
import { defaultRealtimeInput, getRealtimeIntegrationCode, getRealtimeRequestExample, getRealtimeRequestSchema } from "../lib/realtime-integration.ts";

const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const apiKeyId = "portal-11111111-1111-4111-8111-111111111111"; const key = "test-only-private-realtime-key"; const handleSecret = "test-only-cleanup-secret-with-more-than-thirty-two-characters";
const portalOrigin = "https://portal.example.invalid"; const gatewayOrigin = "https://gateway.example.invalid";
const sdp = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n";
const realtime: RealtimeModel = { id: "realtime-webrtc", label: "Realtime", providerId: "openai", provider: "OpenAI", family: "realtime", transport: "webrtc", apiId: "openai-realtime-control-v1", operationId: "realtime-create", path: "/models/openai-realtime/v1/calls", modelId: "gpt-realtime-2.1", voice: "marin", maxRequestBytes: 131072, maxOutputTokens: 512, observer: { apiId: "openai-realtime-observer-v1", path: "/models/openai-realtime/v1/observer/" }, hangupPath: "/models/openai-realtime/v1/calls/{callId}/hangup" };
const live: RealtimeModel = { ...realtime, id: "live-webrtc", family: "live", apiId: "openai-live-control-v1", operationId: "live-create", path: "/models/openai-live/v1/sessions", modelId: "gpt-live-1", observer: undefined, hangupPath: undefined };
const voice: RealtimeModel = { ...live, id: "voice-webrtc", providerId: "azure-ai-speech", provider: "Azure AI Speech", family: "voice-live", apiId: "azure-voice-live-webrtc-v1", operationId: "invoke", path: "/models/voice-live/v1/webrtc/", modelId: "gpt-6.1-sol", voice: "ja-JP-NanamiNeural", maxOutputTokens: 128000, numericMaxOutputTokens: 4096, supportsMaximumOutput: true };
const callId = "private-call-identifier";
/** APIMから返る所有チケットの構造だけを模した、外部では使えないfixtureです。 */
function ticket(seconds = 590): string { return `${Buffer.from(JSON.stringify({ v: 3, callId, exp: Math.floor(Date.now() / 1000) + seconds })).toString("base64url")}.test-only-signature`; }
/** HTTPとWSSの上流だけを差し替え、本人・購読・SDPの実検査を通します。 */
function dependencies(overrides: Partial<RealtimeWebRtcDependencies> = {}): RealtimeWebRtcDependencies {
  return { timeoutMs: 100, readSession: async () => ({ ...actor, name: "Fixture account", email: "fixture@example.invalid" }), readPortalOrigin: () => portalOrigin, readModels: () => [realtime, live, voice], readGatewayEnvironment: async () => ({ origin: gatewayOrigin }), readHandleSecret: () => handleSecret,
    resolveSubscriptionKey: async () => key,
    fetch: async (url) => String(url).endsWith("/hangup") ? new Response(null, { status: 204 }) : new Response(sdp, { status: 201, headers: { "Content-Type": "application/sdp", "X-Core-Call-Id": callId, "X-Core-Call-Ticket": ticket() } }),
    connectWebSocket: async () => socket([{ type: "session.created", secret: key }, { type: "response.done", response: { status: "completed", usage: { input_tokens: 3, output_tokens: 4, total_tokens: 7, private: key }, output: key } }]), ...overrides };
}
/** 既知の制御イベントだけを返し、待機中の取消も再現できるsocketです。 */
function socket(events: unknown[], closed?: () => void): RealtimeControlSocket {
  return { send: async () => undefined, receive: async (signal) => {
    if (events.length) return events.shift();
    return new Promise((_, reject) => { if (signal.aborted) reject(new Error("aborted")); else signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }); });
  }, close: async () => { closed?.(); } };
}
/** 公開される入力には、台帳IDと設定、ブラウザー生成SDPしか含めません。 */
function request(model = realtime, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}, signal?: AbortSignal): Request {
  return new Request(`${portalOrigin}/api/models/realtime/webrtc`, { method: "POST", headers: { origin: portalOrigin, "Content-Type": "application/json", ...headers }, body: JSON.stringify({ modelId: model.id, ...(model.family !== "live" && model.family !== "transcription" ? { maxOutputTokens: 256 } : {}), apiKeyId, sdp, ...extra }), signal });
}
/** 生の上流イベントではなく、全件が公開SSE契約へ適合することを確認して取り出します。 */
async function events(response: Response): Promise<RealtimeStreamEvent[]> { return (await response.text()).split("\n\n").filter(Boolean).map((part) => realtimeStreamEventSchema.parse(JSON.parse(part.replace(/^data: /, "")))); }

test("停止参照は暗号化され、改変・別本人・別鍵・期限切れを拒否する", () => {
  const now = Date.now(); const value: RealtimeCallHandle = { version: 1, ...actor, apiKeyId, modelId: realtime.id, callId, ticket: ticket(), expiresAt: now + 300_000 };
  const first = sealRealtimeCallHandle(value, handleSecret, now); const second = sealRealtimeCallHandle(value, handleSecret, now);
  assert.notEqual(first, second); assert.ok(!first.includes(callId)); assert.deepEqual(openRealtimeCallHandle(first, handleSecret, actor, now), value);
  assert.throws(() => openRealtimeCallHandle(`${first.slice(0, 8)}X${first.slice(9)}`, handleSecret, actor, now));
  assert.throws(() => openRealtimeCallHandle(first, handleSecret + "changed", actor, now));
  assert.throws(() => openRealtimeCallHandle(first, handleSecret, { ...actor, objectId: "44444444-4444-4444-8444-444444444444" }, now));
  assert.throws(() => openRealtimeCallHandle(first, handleSecret, actor, now + 300_000));
  assert.throws(() => sealRealtimeCallHandle({ ...value, expiresAt: now + 600_001 }, handleSecret, now));
});

test("SDPは音声track・先頭形式・サイズを検査し、映像とNULLを拒否する", () => {
  assert.equal(validateRealtimeSdp(sdp), sdp);
  for (const value of ["not-sdp", "v=0\r\ns=no audio\r\n", `${sdp}m=video 9 RTP/AVP 96\r\n`, `${sdp}\0`, `${sdp}${"a".repeat(65536)}`]) assert.throws(() => validateRealtimeSdp(value));
});

test("未認証・異なるOrigin・不正入力は、上流呼び出しとキー取得の前に拒否する", async () => {
  let calls = 0; const context = dependencies({ fetch: async () => { calls++; throw new Error(key); }, resolveSubscriptionKey: async () => { calls++; return key; } });
  assert.equal((await handleRealtimeWebRtc(request(realtime, {}, { origin: "https://other.invalid" }), context)).status, 403);
  assert.equal((await handleRealtimeWebRtc(request(realtime, {}, { "sec-fetch-site": "cross-site" }), context)).status, 403);
  assert.equal((await handleRealtimeWebRtc(request(), { ...context, readSession: async () => null })).status, 401);
  assert.equal((await handleRealtimeWebRtc(request(), { ...context, readSession: async () => { throw new Error(key); } })).status, 503);
  for (const extra of [{ modelId: "other" }, { sdp: "invalid" }, { url: "https://elsewhere.invalid" }, { apiKey: key }, { maxOutputTokens: 513 }]) assert.equal((await handleRealtimeWebRtc(request(realtime, extra), context)).status, 400);
  assert.equal(calls, 0);
});

test("WebRTCの未知音声と別系列の音声はキー取得・上流接続前に拒否する", async () => {
  let sideEffects = 0;
  const context = dependencies({
    resolveSubscriptionKey: async () => { sideEffects++; return key; },
    fetch: async () => { sideEffects++; throw new Error("Unexpected request"); },
    connectWebSocket: async () => { sideEffects++; throw new Error("Unexpected connection"); },
  });
  for (const [model, selectedVoice] of [[realtime, "unregistered"], [realtime, "ja-JP-NanamiNeural"], [voice, "cedar"], [live, "cedar"], [voice, " ja-JP-KeitaNeural"], [realtime, "a".repeat(151)]] as const) {
    assert.equal((await handleRealtimeWebRtc(request(model, { voice: selectedVoice }), context)).status, 400);
  }
  assert.equal(sideEffects, 0);
});

test("Realtimeの音声選択を受け付けてもcalls作成本文はSDPだけを維持する", async () => {
  let created = 0; const original = dependencies();
  const output = await events(await handleRealtimeWebRtc(request(realtime, { voice: "cedar" }), { ...original, fetch: async (url, init) => {
    if (!String(url).endsWith("/hangup")) { created++; assert.deepEqual(JSON.parse(init?.body as string), { sdp }); }
    return original.fetch!(url, init);
  } }));
  assert.equal(created, 1); assert.ok(output.some((event) => event.type === "answer"));
});

test("observerの権限も開始前に確認し、権限不足で通話を作らない", async () => {
  let calls = 0; const checked: string[] = [];
  const response = await handleRealtimeWebRtc(request(), dependencies({ resolveSubscriptionKey: async (_, id, apiId) => { assert.equal(id, apiKeyId); checked.push(apiId); if (apiId === realtime.observer!.apiId) throw new ApiKeyError("Forbidden", 403); return key; }, fetch: async () => { calls++; throw new Error("not called"); } }));
  assert.equal(response.status, 403); assert.deepEqual(checked, [realtime.apiId, realtime.observer!.apiId]); assert.equal(calls, 0);
});

test("APIMの小さい時計差で正常な所有情報を捨てず、停止参照の期限は延長しない", async () => {
  const before = Date.now();
  const response = await handleRealtimeWebRtc(request(), dependencies({ timeoutMs: 100,
    fetch: async (url) => String(url).endsWith("/hangup") ? new Response(null, { status: 204 }) : new Response(sdp, { status: 201, headers: { "Content-Type": "application/sdp", "X-Core-Call-Id": callId, "X-Core-Call-Ticket": ticket(601) } }),
  }));
  const output = await events(response);
  const answer = output.find((event) => event.type === "answer");
  assert.ok(answer?.type === "answer" && answer.handle);
  const call = openRealtimeCallHandle(answer.handle, handleSecret, actor);
  assert.ok(call.expiresAt <= Date.now() + 590_000 && call.expiresAt >= before);
  assert.equal(output.at(-1)?.type, "complete");
  assert.ok(output.some((event) => event.type === "complete" && event.stopStatus === "confirmed"));
});

test("Realtimeはobserverを接続してからanswerを送り、所有情報を隠して正常終了する", async () => {
  const calls: string[] = []; let observerConnected = false; let closed = false;
  const response = await handleRealtimeWebRtc(request(), dependencies({ fetch: async (url, init) => {
    calls.push(String(url)); const headers = new Headers(init?.headers); assert.equal(headers.get("api-key"), key); assert.equal(init?.redirect, "error");
    if (String(url).endsWith("/hangup")) { assert.ok(headers.get("X-Core-Call-Ticket")); return new Response(null, { status: 204 }); }
    assert.deepEqual(JSON.parse(init?.body as string), { sdp }); return dependencies().fetch!(url, init);
  }, connectWebSocket: async (url, headers) => {
    observerConnected = true; assert.equal(new URL(url).searchParams.get("call_id"), callId); assert.equal(new URL(url).pathname, realtime.observer!.path); assert.ok(headers["X-Core-Call-Ticket"]);
    return socket([{ type: "response.done", response: { status: "completed", usage: { total_tokens: 7, [key]: 1 } } }], () => { closed = true; });
  } }));
  assert.equal(response.headers.get("content-type"), "text/event-stream; charset=utf-8");
  const output = await events(response); assert.ok(observerConnected); assert.ok(closed); const answer = output.find((event) => event.type === "answer"); assert.ok(answer?.type === "answer" && answer.handle);
  assert.equal(openRealtimeCallHandle(answer.handle, handleSecret, actor).callId, callId); assert.ok(!JSON.stringify(output).includes(callId)); assert.ok(!JSON.stringify(output).includes(key));
  assert.equal(output.at(-1)?.type, "complete"); assert.deepEqual(output.find((event) => event.type === "usage"), { type: "usage", values: { total_tokens: 7 } });
  assert.equal((output.at(-1) as { stopStatus: string }).stopStatus, "confirmed"); assert.equal(calls.length, 2);
});

test("SDP不正でも作成済み通話を回収し、停止未確認のときだけ復旧参照を残す", async () => {
  let stopped = 0;
  const output = await events(await handleRealtimeWebRtc(request(), dependencies({ fetch: async (url) => {
    if (String(url).endsWith("/hangup")) { stopped++; return new Response(null, { status: 503 }); }
    return new Response("not-sdp", { status: 201, headers: { "Content-Type": "application/sdp", "X-Core-Call-Id": callId, "X-Core-Call-Ticket": ticket() } });
  } })));
  assert.equal(stopped, 1); assert.equal(output.some((event) => event.type === "answer"), false);
  const last = output.at(-1); assert.ok(last?.type === "complete" && last.handle); assert.equal(last.stopStatus, "unconfirmed"); assert.equal(openRealtimeCallHandle(last.handle, handleSecret, actor).callId, callId);
  assert.ok(output.some((event) => event.type === "error")); assert.ok(!JSON.stringify(output).includes(callId));
});

test("複数の応答と割込み後も会話を維持し、利用者の停止で通話を回収する", async () => {
  const cancelled = new AbortController(); let stopped = 0; let closed = false;
  const context = dependencies();
  const response = await handleRealtimeWebRtc(request(realtime, {}, {}, cancelled.signal), dependencies({ timeoutMs: 5000,
    fetch: async (url, init) => { if (String(url).endsWith("/hangup")) stopped++; return context.fetch!(url, init); },
    connectWebSocket: async () => socket([
      { type: "response.done", response: { status: "completed", usage: { total_tokens: 7 } } },
      { type: "response.done", response: { status: "cancelled" } },
      { type: "response.done", response: { status: "incomplete" } },
    ], () => { closed = true; }),
  }));
  const reader = response.body!.getReader(); let transcript = "";
  while ((transcript.match(/"eventType":"response.done"/g) ?? []).length < 3) {
    const chunk = await reader.read(); assert.equal(chunk.done, false); transcript += new TextDecoder().decode(chunk.value);
  }
  assert.equal(stopped, 0); assert.equal(closed, false);
  cancelled.abort();
  while (true) { const chunk = await reader.read(); if (chunk.done) break; transcript += new TextDecoder().decode(chunk.value); }
  assert.equal(stopped, 1); assert.equal(closed, true); assert.ok(transcript.includes('"stopStatus":"confirmed"'));
});

test("APIMが502で未回収の所有情報を返しても、同じ購読で停止を試みる", async () => {
  let stopped = 0;
  const output = await events(await handleRealtimeWebRtc(request(), dependencies({ fetch: async (url) => {
    if (String(url).endsWith("/hangup")) { stopped++; return new Response(null, { status: 204 }); }
    return new Response(null, { status: 502, headers: { "X-Core-Cleanup-Status": "unconfirmed", "X-Core-Call-Id": callId, "X-Core-Call-Ticket": ticket() } });
  } })));
  assert.equal(stopped, 1); assert.ok(output.some((event) => event.type === "error")); assert.equal((output.at(-1) as { stopStatus: string }).stopStatus, "confirmed");
});

test("observer接続失敗はanswerを公開せず、通話を停止する", async () => {
  let stopped = 0; const context = dependencies();
  const output = await events(await handleRealtimeWebRtc(request(), { ...context, fetch: async (url, init) => { if (String(url).endsWith("/hangup")) stopped++; return context.fetch!(url, init); }, connectWebSocket: async () => { throw new Error(key); } }));
  assert.equal(stopped, 1); assert.ok(!output.some((event) => event.type === "answer")); assert.ok(!JSON.stringify(output).includes(key));
});

test("SSE受信を中止しても、キーを使うサーバー側の停止処理は継続する", async () => {
  let stopped = 0; let resolveStop: () => void = () => undefined; const stoppedPromise = new Promise<void>((resolve) => { resolveStop = resolve; }); const context = dependencies();
  const response = await handleRealtimeWebRtc(request(), { ...context, connectWebSocket: async () => socket([]), fetch: async (url, init) => { if (String(url).endsWith("/hangup")) { stopped++; resolveStop(); } return context.fetch!(url, init); } });
  const reader = response.body!.getReader(); while (true) { const chunk = await reader.read(); if (new TextDecoder().decode(chunk.value).includes('"type":"answer"')) break; }
  await reader.cancel(); await stoppedPromise; assert.equal(stopped, 1);
});

test("Liveは固定instructionsとmarinで開始し、契約にないhangup成功を主張しない", async () => {
  let calls = 0;
  const output = await events(await handleRealtimeWebRtc(request(live), dependencies({ timeoutMs: 20, fetch: async (_, init) => { calls++; const body = JSON.parse(init?.body as string); assert.equal(body.voice, "marin"); assert.equal(body.instructions, REALTIME_CONVERSATION_INSTRUCTIONS); assert.equal(body.model, undefined); return Response.json({ sessionId: "private-live-id", sdp }); } })));
  assert.equal(calls, 1); assert.ok(output.some((event) => event.type === "answer")); assert.equal((output.at(-1) as { stopStatus: string }).stopStatus, "transport-closed"); assert.ok(!JSON.stringify(output).includes("private-live-id"));
});

/** 別々に作るガイド・要求例と実BFFを比較し、ガイド自身だけを期待値にしません。 */
test("Live RTCの五言語ガイドとJSON・部分schemaは実BFFの固定指示・音声・SDPと一致する", async () => {
  const expected = { sdp, instructions: REALTIME_CONVERSATION_INSTRUCTIONS, voice: "marin" };
  let captured: unknown; let calls = 0;
  const output = await events(await handleRealtimeWebRtc(request(live, { voice: "marin" }), dependencies({ timeoutMs: 20,
    fetch: async (url, init) => {
      calls++; assert.equal(String(url), gatewayOrigin + live.path);
      assert.equal(init?.method, "POST"); assert.equal(init?.redirect, "error");
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("Content-Type"), "application/json"); assert.equal(headers.get("api-key"), key);
      captured = JSON.parse(String(init?.body));
      return Response.json({ sessionId: "isolated-guide-live-id", sdp });
    },
  })));
  assert.equal(calls, 1); assert.deepEqual(captured, expected);
  assert.ok(output.some((event) => event.type === "answer"));
  const input = { ...defaultRealtimeInput(live), voice: "marin" };
  const example = JSON.parse(getRealtimeRequestExample(live, input));
  assert.equal(example.method, "POST"); assert.equal(example.contentType, "application/json");
  assert.equal(example.body.sdp, "<browser-generated audio-only SDP offer>");
  assert.deepEqual({ ...example.body, sdp }, captured);
  const schema = getRealtimeRequestSchema(live) as { required: string[]; properties: Record<string, { type?: string; enum?: string[]; maxLength?: number }> };
  assert.ok(schema.required.includes("sdp")); assert.equal(schema.properties.instructions.type, "string");
  assert.ok(REALTIME_CONVERSATION_INSTRUCTIONS.length <= schema.properties.instructions.maxLength!);
  assert.ok(schema.properties.voice.enum!.includes(expected.voice));
  const bodyPatterns = {
    typescript: /body: ("(?:\\.|[^"\\])*")/,
    python: /data=("(?:\\.|[^"\\])*")/,
    php: /CURLOPT_POSTFIELDS => ("(?:\\.|[^"\\])*")/,
    csharp: /new StringContent\(("(?:\\.|[^"\\])*")/,
  };
  for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
    const code = getRealtimeIntegrationCode(live, input, gatewayOrigin, language);
    assert.ok(code.includes(gatewayOrigin + live.path)); assert.ok(code.includes("GATEWAY_API_KEY")); assert.ok(!code.includes(key));
    const match = language === "curl" ? /# offer\.json: ([^\n]+)/.exec(code) : bodyPatterns[language].exec(code);
    assert.ok(match, language);
    const body = JSON.parse(language === "curl" ? match[1] : JSON.parse(match[1]));
    assert.equal(body.sdp, "<audio-only SDP offer>", language);
    assert.deepEqual({ ...body, sdp }, captured, language);
  }
});

for (const maxOutputTokens of [256, "model-max"] as const) test(`Voice Liveは専用WSSでSDPと選択音声・出力上限を交換する（${maxOutputTokens}）`, async () => {
  let sent: unknown; let closed = false;
  const cancelled = new AbortController();
  const response = await handleRealtimeWebRtc(request(voice, { maxOutputTokens, voice: "ja-JP-KeitaNeural" }, {}, cancelled.signal), dependencies({ connectWebSocket: async (url, headers) => {
    assert.equal(url, gatewayOrigin.replace("https:", "wss:") + voice.path); assert.equal(headers["api-key"], key);
    return { ...socket([{ type: "rtc.call.sdp.created", sdp_answer: sdp, private: key }, { type: "response.done", response: { status: "completed" } }], () => { closed = true; }), send: async (value) => { sent = value; } };
  }, fetch: async () => { throw new Error("Voice Live must use its WSS contract"); } }));
  const reader = response.body!.getReader(); const chunks: Uint8Array[] = [];
  while (true) { const chunk = await reader.read(); assert.equal(chunk.done, false); chunks.push(chunk.value!); if (new TextDecoder().decode(chunk.value).includes('"eventType":"response.done"')) break; }
  assert.equal(closed, false, "生成が完了しても、音声受信を待つ制御WSSを閉じない");
  cancelled.abort();
  while (true) { const chunk = await reader.read(); if (chunk.done) break; chunks.push(chunk.value); }
  const output = await events(new Response(Buffer.concat(chunks)));
  const offer = sent as { type: string; sdp_offer: string; session: { voice: unknown; turn_detection: unknown; max_response_output_tokens: number | "inf" } };
  assert.equal(offer.type, "rtc.call.sdp.create"); assert.equal(offer.sdp_offer, sdp); assert.deepEqual(offer.session.voice, { type: "azure-standard", name: "ja-JP-KeitaNeural" }); assert.equal(offer.session.max_response_output_tokens, maxOutputTokens === "model-max" ? "inf" : maxOutputTokens); assert.ok(offer.session.turn_detection); assert.ok(closed);
  assert.equal((output.at(-1) as { stopStatus: string }).stopStatus, "transport-closed"); assert.ok(!JSON.stringify(output).includes(key));
});

test("停止参照は本人と現在の購読権限を再確認し、固定hangupだけを呼ぶ", async () => {
  const value: RealtimeCallHandle = { version: 1, ...actor, apiKeyId, modelId: realtime.id, callId, ticket: ticket(), expiresAt: Date.now() + 300_000 };
  const handle = sealRealtimeCallHandle(value, handleSecret); let calls = 0;
  const stop = (body = { handle }) => new Request(`${portalOrigin}/api/models/realtime/stop`, { method: "POST", headers: { origin: portalOrigin, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const response = await handleRealtimeStop(stop(), dependencies({ fetch: async (url, init) => { calls++; assert.equal(String(url), gatewayOrigin + `/models/openai-realtime/v1/calls/${callId}/hangup`); assert.equal(new Headers(init?.headers).get("X-Core-Call-Ticket"), value.ticket); return new Response(null, { status: 204 }); } }));
  assert.deepEqual(await response.json(), { stopStatus: "confirmed" }); assert.equal(calls, 1);
  assert.equal((await handleRealtimeStop(stop(), dependencies({ readSession: async () => ({ ...actor, objectId: "44444444-4444-4444-8444-444444444444" }) }))).status, 400);
  assert.equal((await handleRealtimeStop(stop(), dependencies({ resolveSubscriptionKey: async () => { throw new ApiKeyError("Forbidden", 403); } }))).status, 403);
  const failed = await handleRealtimeStop(stop(), dependencies({ fetch: async () => new Response(null, { status: 404 }) })); assert.equal(failed.status, 502); assert.deepEqual(await failed.json(), { stopStatus: "unconfirmed" });
});
