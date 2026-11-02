import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { test } from "node:test";
import WebSocket from "ws";
import { createRealtimeRelay, isRealtimeInternalPath, verifyRealtimeInternalProof, type RealtimeRelayContext, type RealtimeRelayDependencies } from "../lib/realtime-relay.ts";
import type { RealtimeModel } from "../lib/model-types.ts";
import type { RealtimeStreamEvent } from "../lib/realtime-request.ts";

const origin = "https://portal.example.invalid"; const key = "fixture-private-subscription-key-never-printed";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const apiKeyId = "portal-11111111-1111-4111-8111-111111111111";
const model: RealtimeModel = { id: "fixture-realtime", label: "Realtime", providerId: "openai", provider: "OpenAI", family: "realtime", transport: "websocket", apiId: "openai-realtime-ws-v1", operationId: "invoke", path: "/models/openai-realtime/v1/ws/", modelId: "gpt-realtime-2.1", voice: "marin", maxRequestBytes: 65536, maxOutputTokens: 512 };
const context: RealtimeRelayContext = { actor, model, key, apiKeyId, gatewayOrigin: "https://gateway.example.invalid", portalOrigin: origin };
const start = { type: "start", modelId: model.id, apiKeyId, maxOutputTokens: 256 };

/** loopback内だけでブラウザーWSを再現し、実際のAPI・認証・課金先には接続しません。 */
async function fixture(overrides: Partial<RealtimeRelayDependencies> = {}) {
  const relay = createRealtimeRelay({ portalOrigin: origin, authorizeUrl: "http://127.0.0.1:3001/api/_internal/realtime/authorize", internalProof: "a".repeat(64),
    stopTimeoutMs: 30,
    authorize: async () => context, runConversation: async (_model, _input, _endpoint, _key, signal, emit) => {
      emit({ type: "status", stage: "connected", elapsedMs: 0 });
      await new Promise<void>((finish) => { if (signal.aborted) finish(); else signal.addEventListener("abort", () => finish(), { once: true }); });
      emit({ type: "complete", durationMs: 1, inputAudioBytes: 0, outputAudioBytes: 0, stopStatus: "transport-closed" });
    }, ...overrides });
  const server = createServer((_request, response) => response.end()); server.on("upgrade", relay.upgrade); server.listen(0, "127.0.0.1"); await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address !== "string");
  return { relay, url: `ws://127.0.0.1:${address.port}/api/models/realtime/socket`, close: async () => { await relay.shutdown(); await new Promise<void>((finish) => server.close(() => finish())); } };
}
/** 正しいOriginと認証Cookieの有無だけを再現します。Cookie実値はダミーです。 */
function client(url: string, headers: Record<string, string> = {}): WebSocket { return new WebSocket(url, { headers: { Origin: origin, Cookie: "egakumi.portal.session_token=fixture-only", ...headers }, perMessageDeflate: false }); }
/** 先にリスナーを置き、到着順の公開フレームと終端を取りこぼさず収集します。 */
function collect(socket: WebSocket) { const values: RealtimeStreamEvent[] = []; socket.on("message", (value) => values.push(JSON.parse(value.toString()))); return values; }
/** テスト失敗時も短い期限で待機を解除します。 */
async function until(predicate: () => boolean): Promise<void> { const end = Date.now() + 3000; while (!predicate()) { if (Date.now() >= end) throw new Error("Fixture timed out"); await new Promise((finish) => setTimeout(finish, 5)); } }

test("内部proofは未設定・改変・非ASCIIを拒否し、内部URLの符号化表記も公開しない", () => {
  assert.equal(verifyRealtimeInternalProof("a".repeat(64), "a".repeat(64)), true);
  for (const proof of [undefined, "a".repeat(63), "b".repeat(64), "あ".repeat(64)]) assert.equal(verifyRealtimeInternalProof("a".repeat(64), proof), false);
  assert.equal(verifyRealtimeInternalProof(undefined, "a".repeat(64)), false);
  for (const url of ["/api/_internal", "/api/_internal/realtime/authorize", "/api/%5Finternal/realtime/authorize", "/api/%255Finternal/realtime/authorize", "/api//_internal/realtime", "/api/%5c_internal/realtime"]) assert.equal(isRealtimeInternalPath(url), true, url);
  assert.equal(isRealtimeInternalPath("/api/models/realtime/socket"), false);
  assert.equal(isRealtimeInternalPath("/ja/models?return=/api/_internal"), false);
});

test("Origin違い・Cookie欠如・query付きWSを、認可と通話生成の前に拒否する", async () => {
  let called = 0; const app = await fixture({ authorize: async () => { called++; return context; } });
  try {
    for (const [url, headers] of [[app.url, { Origin: "https://other.invalid" }], [app.url, { Cookie: "" }], [`${app.url}?api-key=not-accepted`, {}]] as [string, Record<string, string>][]) {
      const ws = client(url, headers); const status = await new Promise<number>((finish) => { ws.on("unexpected-response", (_request, response) => { finish(response.statusCode ?? 0); response.destroy(); ws.terminate(); }); ws.on("error", () => undefined); });
      assert.equal(status, 403);
    }
    assert.equal(called, 0);
  } finally { await app.close(); }
});

test("開始フレームの期限切れ・任意URL・二重開始で上流を誤って開始しない", async () => {
  let called = 0; const app = await fixture({ firstFrameTimeoutMs: 30, authorize: async () => { called++; return context; } });
  try {
    const first = client(app.url); await once(first, "open"); await once(first, "close"); assert.equal(called, 0);
    const second = client(app.url); const values = collect(second); await once(second, "open"); second.send(JSON.stringify({ ...start, url: "https://other.invalid" })); await once(second, "close");
    assert.ok(values.some((event) => event.type === "error" && event.error.code === "InvalidRequest")); assert.equal(called, 0);
  } finally { await app.close(); }
});

test("所属・購読の認可失敗を固定コードで返し、秘密や上流の例外を公開しない", async () => {
  let upstream = 0; const app = await fixture({ authorize: async () => { throw new Error("ApiKeyUnavailable"); }, runConversation: async () => { upstream++; } });
  try { const ws = client(app.url); const values = collect(ws); await once(ws, "open"); ws.send(JSON.stringify(start)); await once(ws, "close");
    assert.ok(values.some((event) => event.type === "error" && event.error.code === "ApiKeyUnavailable")); assert.equal(upstream, 0); assert.ok(!JSON.stringify(values).includes(key));
  } finally { await app.close(); }
});

test("認可済みの購読だけで音声を中継し、ユーザーの停止後に回収が終わってから接続を閉じる", async () => {
  const commands: string[] = []; let cleaned = false;
  const app = await fixture({ runConversation: async (selected, input, endpoint, credential, signal, emit, register) => {
    assert.equal(selected.id, model.id); assert.deepEqual(input, { modelId: model.id, maxOutputTokens: 256 }); assert.equal(credential, key); assert.equal(new URL(endpoint).protocol, "wss:");
    const unregister = register((command) => commands.push(command.type)); emit({ type: "status", stage: "connected", elapsedMs: 0 });
    await new Promise<void>((finish) => signal.addEventListener("abort", () => finish(), { once: true }));
    await new Promise((finish) => setTimeout(finish, 15)); cleaned = true; unregister();
    emit({ type: "complete", durationMs: 1, inputAudioBytes: 2, outputAudioBytes: 0, stopStatus: "transport-closed" });
  } });
  try { const ws = client(app.url); const values = collect(ws); await once(ws, "open"); ws.send(JSON.stringify(start)); await until(() => values.some((value) => value.type === "status"));
    ws.send(JSON.stringify({ type: "input_audio", delta: "AAA=", sampleRate: 24000 })); ws.send(JSON.stringify({ type: "cancel_response" })); await until(() => commands.length === 2);
    ws.send(JSON.stringify({ type: "stop" })); await once(ws, "close"); assert.ok(cleaned); assert.deepEqual(commands, ["input_audio", "cancel_response", "stop"]); assert.equal(values.at(-1)?.type, "complete");
    assert.ok(!JSON.stringify(values).includes(key)); assert.ok(!JSON.stringify(values).includes(actor.objectId));
  } finally { await app.close(); }
});

test("WebRTCのanswerをHTTP集約せず即送信し、取消時も所有通話を回収する", async () => {
  const rtc: RealtimeModel = { ...model, id: "fixture-webrtc", transport: "webrtc", apiId: "openai-realtime-control-v1", path: "/models/openai-realtime/v1/calls", observer: { apiId: "openai-realtime-observer-v1", path: "/models/openai-realtime/v1/observer/" } };
  let cleaned = false;
  const app = await fixture({ authorize: async () => ({ ...context, model: rtc }), runWebRtc: async (request, dependencies) => {
    assert.deepEqual(await dependencies.readSession(), actor); assert.equal(await dependencies.resolveSubscriptionKey!(actor, apiKeyId, rtc.observer!.apiId), key);
    await assert.rejects(() => dependencies.resolveSubscriptionKey!(actor, apiKeyId, "unapproved-api"));
    return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "answer", sdp: "v=0\r\nfixture-audio-answer" })}\n\n`));
      request.signal.addEventListener("abort", () => { cleaned = true; controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "complete", durationMs: 1, inputAudioBytes: 0, outputAudioBytes: 0, stopStatus: "confirmed" })}\n\n`)); controller.close(); }, { once: true });
    } }));
  } });
  try { const ws = client(app.url); const values = collect(ws); await once(ws, "open"); ws.send(JSON.stringify({ ...start, modelId: rtc.id, sdp: "v=0\r\nfixture-audio-offer" }));
    await until(() => values.some((value) => value.type === "answer")); assert.equal(cleaned, false);
    ws.send(JSON.stringify({ type: "stop" })); await once(ws, "close"); assert.ok(cleaned); assert.ok(values.some((value) => value.type === "complete" && value.stopStatus === "confirmed"));
  } finally { await app.close(); }
});

test("ブラウザー切断とfront停止の両方で所有通信を取消し、秘密の反射も遮断する", async () => {
  let cleaned = 0; const app = await fixture({ runConversation: async (_model, _input, _endpoint, _key, signal, emit) => {
    emit({ type: "status", stage: "connected", elapsedMs: 0 });
    try { await new Promise<void>((finish) => signal.addEventListener("abort", () => finish(), { once: true })); }
    finally { await new Promise((finish) => setTimeout(finish, 10)); cleaned++; }
  } });
  const first = client(app.url); const firstValues = collect(first); await once(first, "open"); first.send(JSON.stringify(start)); await until(() => firstValues.length > 0); first.terminate(); await until(() => cleaned === 1);
  const second = client(app.url); const secondValues = collect(second); await once(second, "open"); second.send(JSON.stringify(start)); await until(() => secondValues.length > 0); await app.close(); assert.equal(cleaned, 2);
  const secretApp = await fixture({ runConversation: async (_model, _input, _endpoint, _key, _signal, emit) => { emit({ type: "transcript", role: "output", final: true, text: key }); } });
  try { const ws = client(secretApp.url); const values = collect(ws); await once(ws, "open"); ws.send(JSON.stringify(start)); await once(ws, "close"); assert.ok(!JSON.stringify(values).includes(key)); }
  finally { await secretApp.close(); }
});
