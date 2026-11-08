import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { handlePiiRedaction, type PiiGatewayDependencies } from "../lib/pii-gateway.ts";
import { PII_API, PII_SAMPLE } from "../lib/pii-contract.ts";

const key = "key-for-pii-test-1234567890", apiKeyId = "portal-7da3b268-cfcf-4b2f-bc37-cd3f27397989";
const input = { ...PII_SAMPLE, apiKeyId };
const body = { redactedText: "サンプル担当者は****です。", language: "ja", detectedCount: 3, modelVersion: PII_API.modelVersion, warningCodes: [], requestId: "b21d67b3-7cef-49fc-b529-168f229b9cb0" };
const actor = { tenantId: "9d252ced-a744-45c1-a42f-9116a0ab846d", objectId: "a6994205-46b7-45ac-978d-99d011b58c81" };

/** テストは本文を保存しない固定の認証済み依存で実行します。 */
function dependencies(overrides: Partial<PiiGatewayDependencies> = {}): PiiGatewayDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => "https://portal.example.com", readGatewayEnvironment: async () => ({ origin: "https://gateway.example.com" }), resolveSubscriptionKey: async () => key, fetch: async () => Response.json(body), ...overrides };
}
/** 本番と同じ同一オリジンのJSONリクエストを作ります。 */
function request(value: unknown = input, headers: Record<string, string> = {}, signal?: AbortSignal): Request {
  return new Request("https://portal.example.com/api/tools/pii", { method: "POST", headers: { origin: "https://portal.example.com", "content-type": "application/json", ...headers }, body: JSON.stringify(value), signal });
}

test("現在の本人とpii-v1利用権限を確認し固定経路へ一度だけ送る", async () => {
  let calls = 0;
  const response = await handlePiiRedaction(request(), dependencies({ resolveSubscriptionKey: async (user, id, apiId) => { assert.deepEqual(user, actor); assert.equal(id, apiKeyId); assert.equal(apiId, "pii-v1"); return key; }, fetch: async (url, options) => {
    calls++; assert.equal(String(url), "https://gateway.example.com/tools/pii/v1/redact"); assert.equal(options?.redirect, "error"); assert.equal(options?.cache, "no-store");
    assert.equal(new Headers(options?.headers).get("api-key"), key); assert.deepEqual(JSON.parse(String(options?.body)), PII_SAMPLE); return Response.json(body);
  } }));
  assert.equal(response.status, 200); assert.equal(calls, 1); assert.equal(response.headers.get("cache-control"), "no-store");
  const result = await response.json(); assert.deepEqual(result.result, body); assert.equal(typeof result.durationMs, "number"); assert.equal(JSON.stringify(result).includes(key), false);
});

test("他オリジン・未ログイン・不正入力・利用できない購読では送信しない", async () => {
  let calls = 0; const never = async () => { calls++; return Response.json(body); };
  assert.equal((await handlePiiRedaction(request(input, { origin: "https://attacker.example" }), dependencies({ fetch: never }))).status, 403);
  assert.equal((await handlePiiRedaction(request(input, { "sec-fetch-site": "cross-site" }), dependencies({ fetch: never }))).status, 403);
  assert.equal((await handlePiiRedaction(request(), dependencies({ readSession: async () => null, fetch: never }))).status, 401);
  assert.equal((await handlePiiRedaction(request({ ...input, url: "https://attacker.example" }), dependencies({ fetch: never }))).status, 400);
  assert.equal((await handlePiiRedaction(request({ ...input, language: "fr" }), dependencies({ fetch: never }))).status, 400);
  assert.equal((await handlePiiRedaction(request({ ...input, text: "あ".repeat(11000) }), dependencies({ fetch: never }))).status, 400);
  assert.equal((await handlePiiRedaction(request(), dependencies({ resolveSubscriptionKey: async () => { throw new ApiKeyError("AccessDenied", 403); }, fetch: never }))).status, 403);
  assert.equal(calls, 0);
});

test("接続先の任意パスやHTTP化を拒否する", async () => {
  for (const origin of ["http://gateway.example.com", "https://user:pass@gateway.example.com", "https://gateway.example.com/path", "https://gateway.example.com/?secret=x"]) {
    const response = await handlePiiRedaction(request(), dependencies({ readGatewayEnvironment: async () => ({ origin }), fetch: async () => { throw new Error("must not fetch"); } }));
    assert.equal(response.status, 503);
  }
});

test("上流の秘密を含むエラー本文は捨て、制限・入力失敗を固定コードで返す", async () => {
  for (const status of [400, 413, 422, 500, 504]) {
    const response = await handlePiiRedaction(request(), dependencies({ fetch: async () => new Response(`secret ${key}`, { status }) }));
    assert.equal((await response.text()).includes(key), false);
    assert.equal(response.status, status === 500 ? 502 : status);
  }
  const response = await handlePiiRedaction(request(), dependencies({ fetch: async () => new Response(key, { status: 429, headers: { "retry-after": "12" } }) }));
  assert.deepEqual(await response.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 12 });
});

test("追加原文・秘密反射・異言語・不正モデル・過大応答を成功として返さない", async () => {
  for (const value of [{ ...body, text: PII_SAMPLE.text }, { ...body, redactedText: key }, { ...body, language: "en" }, { ...body, modelVersion: "latest" }, { ...body, entities: [{ text: "private" }] }]) {
    const response = await handlePiiRedaction(request(), dependencies({ fetch: async () => Response.json(value) }));
    assert.equal(response.status, 502); assert.equal((await response.text()).includes(key), false);
  }
  assert.equal((await handlePiiRedaction(request(), dependencies({ fetch: async () => new Response("x".repeat(131073), { headers: { "content-type": "application/json" } }) }))).status, 502);
});

test("止まった本文を期限で中止し、再試行で二重送信しない", async () => {
  let calls = 0, cancelled = false;
  const keepAlive = setTimeout(() => undefined, 1000);
  try {
    const response = await handlePiiRedaction(request(), dependencies({ timeoutMs: 10, fetch: async () => { calls++; return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"redactedText":')); }, cancel() { cancelled = true; } }), { headers: { "content-type": "application/json" } }); } }));
    assert.equal(response.status, 504); assert.equal(calls, 1); assert.equal(cancelled, true);
  } finally { clearTimeout(keepAlive); }
});

test("ブラウザーによる中止を上流にも伝える", async () => {
  const controller = new AbortController();
  const response = await handlePiiRedaction(request(input, {}, controller.signal), dependencies({ fetch: async (_url, options) => { controller.abort(); assert.equal(options?.signal?.aborted, true); throw new Error("cancelled"); } }));
  assert.equal(response.status, 499);
});

test("引用符付きキーがredactedTextへJSONエスケープされても拒否する", async () => {
  const credential = 'synthetic-pii-"key"-\\-value';
  const raw = JSON.stringify({ ...body, redactedText: credential }); assert.equal(raw.includes(credential), false);
  const response = await handlePiiRedaction(request(), dependencies({ resolveSubscriptionKey: async () => credential, fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) }));
  assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
});
