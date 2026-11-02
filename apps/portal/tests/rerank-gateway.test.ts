import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { handleRerank, type RerankGatewayDependencies } from "../lib/rerank-gateway.ts";
import { defaultRerankInput } from "../lib/rerank-integration.ts";
import type { RerankModel } from "../lib/rerank-types.ts";

const model: RerankModel = { id: "Cohere-rerank-v4.0-fast", label: "Cohere-rerank-v4.0-fast", providerId: "cohere", provider: "Cohere", apiId: "cohere-inference-v1", operationId: "rerank", path: "/models/cohere/v1/rerank", maxRequestBytes: 65_536, maxDocuments: 8, minTopN: 1, maxTopN: 3 };
const input = defaultRerankInput(model), key = "test-only-rerank-subscription-key", apiKeyId = "portal-11111111-1111-4111-8111-111111111111";
const portalOrigin = "https://portal.example.invalid", gatewayOrigin = "https://gateway.example.invalid";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const body = { results: [{ index: 0, relevance_score: 0.9 }, { index: 2, relevance_score: 0.7 }, { index: 1, relevance_score: 0.1 }], meta: { billed_units: { search_units: 1 } } };

/** 認証済みのブラウザーが送る同一オリジン要求を作ります。 */
function request(value: unknown = input, headers: Record<string, string> = {}, signal?: AbortSignal): Request {
  return new Request(`${portalOrigin}/api/models/rerank`, { method: "POST", headers: { Origin: portalOrigin, "Content-Type": "application/json", ...headers }, body: JSON.stringify({ apiKeyId, ...(value as Record<string, unknown>) }), signal });
}
/** 外部通信を置き換えつつ、実際の認可・制約・応答検証は通します。 */
function dependencies(overrides: Partial<RerankGatewayDependencies> = {}): RerankGatewayDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => portalOrigin, readGatewayEnvironment: async () => ({ origin: gatewayOrigin }), resolveSubscriptionKey: async () => key, readModels: () => [model], fetch: async () => Response.json(body, { headers: { "apim-request-id": "req-123" } }), ...overrides };
}

test("同一オリジンとログイン本人を確認できない要求は転送しない", async () => {
  let calls = 0;
  const deps = dependencies({ fetch: async () => { calls++; throw new Error("not expected"); } });
  assert.equal((await handleRerank(request(input, { Origin: "https://attacker.invalid" }), deps)).status, 403);
  assert.equal((await handleRerank(request(input, { "sec-fetch-site": "cross-site" }), deps)).status, 403);
  const missing = request(); missing.headers.delete("origin");
  assert.equal((await handleRerank(missing, deps)).status, 403);
  assert.equal((await handleRerank(request(), { ...deps, readSession: async () => null })).status, 401);
  assert.equal((await handleRerank(request(), { ...deps, readSession: async () => { throw new Error("secret db detail"); } })).status, 503);
  assert.equal(calls, 0);
});

test("選択した購読を本人とAPIで再認可し、固定パスへ一回だけ送る", async () => {
  let calls = 0;
  const response = await handleRerank(request(), dependencies({
    resolveSubscriptionKey: async (owner, id, apiId) => { assert.deepEqual(owner, actor); assert.equal(id, apiKeyId); assert.equal(apiId, model.apiId); return key; },
    fetch: async (url, options) => {
      calls++; assert.equal(String(url), `${gatewayOrigin}${model.path}`); assert.equal(options?.redirect, "error"); assert.equal(options?.cache, "no-store");
      const headers = new Headers(options?.headers); assert.equal(headers.get("api-key"), key); assert.equal(headers.get("cookie"), null);
      assert.deepEqual(JSON.parse(String(options?.body)), { model: model.id, query: input.query, documents: input.documents, top_n: input.topN });
      return dependencies().fetch!(url, options);
    },
  }));
  assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
  const result = await response.json(); assert.equal(result.ranking[1].document, input.documents[2]); assert.equal(result.searchUnits, 1); assert.equal(result.requestId, "req-123");
  assert.equal(JSON.stringify(result).includes(key), false); assert.equal(calls, 1);
});

test("未知項目・未知モデル・不正件数・文字列以外の文書を送信前に拒否する", async () => {
  let calls = 0;
  const deps = dependencies({ fetch: async () => { calls++; throw new Error("not expected"); } });
  for (const invalid of [{ ...input, endpoint: "https://attacker.invalid" }, { ...input, modelId: "unknown" }, { ...input, documents: ["one"], topN: 2 }, { ...input, topN: 4 }, { ...input, query: " " }, { ...input, documents: [{ text: "object" }] }]) {
    const result = await handleRerank(request(invalid), deps); assert.ok([400].includes(result.status));
  }
  assert.equal((await handleRerank(request(input, { "Content-Type": "text/plain" }), deps)).status, 415);
  assert.equal((await handleRerank(request(input, { "Content-Length": "1048577" }), deps)).status, 413);
  assert.equal((await handleRerank(request({ ...input, query: "あ".repeat(22_000) }), deps)).status, 413);
  assert.equal(calls, 0);
});

test("無権限・キー解決失敗時に共有キーへフォールバックしない", async () => {
  let calls = 0;
  const deps = dependencies({ fetch: async () => { calls++; throw new Error("not expected"); } });
  assert.equal((await handleRerank(request(), { ...deps, resolveSubscriptionKey: async () => { throw new ApiKeyError("AccessDenied", 403); } })).status, 403);
  assert.equal((await handleRerank(request(), { ...deps, resolveSubscriptionKey: async () => { throw new Error("private upstream detail"); } })).status, 503);
  assert.equal(calls, 0);
});

test("未許可パス・別API・別プロバイダーと不正な接続先を拒否する", async () => {
  for (const wrong of [{ ...model, path: "//attacker.invalid" }, { ...model, apiId: "other" }, { ...model, providerId: "other" }, { ...model, operationId: "other" }]) assert.equal((await handleRerank(request(), dependencies({ readModels: () => [wrong as RerankModel] }))).status, 503);
  for (const origin of ["http://gateway.example.invalid", "https://user:secret@gateway.example.invalid", "https://gateway.example.invalid/path", "https://gateway.example.invalid?x=1"]) assert.equal((await handleRerank(request(), dependencies({ readGatewayEnvironment: async () => ({ origin }) }))).status, 503);
});

test("上流エラー本文を公開せず、429は安全な待機秒数だけを返す", async () => {
  for (const status of [302, 400, 401, 403, 500]) {
    const result = await handleRerank(request(), dependencies({ fetch: async () => new Response(key, { status }) }));
    assert.equal(result.status, 502); assert.equal((await result.text()).includes(key), false);
  }
  const limited = await handleRerank(request(), dependencies({ fetch: async () => new Response(key, { status: 429, headers: { "retry-after": "12" } }) }));
  assert.deepEqual(await limited.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 12 });
});

test("不正応答・重複順位・秘密反射・過大応答を成功として返さない", async () => {
  for (const value of [{ ...body, private: key }, { results: [{ index: 0, relevance_score: 0.8 }] }, { results: [{ index: 0, relevance_score: 0.8 }, { index: 0, relevance_score: 0.7 }, { index: 1, relevance_score: 0.1 }] }, { error: { message: key } }]) {
    const result = await handleRerank(request(), dependencies({ fetch: async () => Response.json(value) })); assert.equal(result.status, 502); assert.equal((await result.text()).includes(key), false);
  }
  assert.equal((await handleRerank(request(), dependencies({ fetch: async () => new Response("x", { headers: { "content-type": "text/plain" } }) }))).status, 502);
  assert.equal((await handleRerank(request(), dependencies({ fetch: async () => new Response(" ".repeat(1_048_577), { headers: { "content-type": "application/json" } }) }))).status, 502);
});

test("本文が止まった場合もタイムアウトで中止し、自動再試行しない", async () => {
  let cancelled = false, calls = 0;
  const keepAlive = setTimeout(() => undefined, 1_000);
  try {
    const result = await handleRerank(request(), dependencies({ timeoutMs: 10, fetch: async () => {
      calls++; return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"results":')); }, cancel() { cancelled = true; } }), { headers: { "content-type": "application/json" } });
    } }));
    assert.equal(result.status, 504); assert.equal(cancelled, true); assert.equal(calls, 1);
  } finally { clearTimeout(keepAlive); }
});

test("ブラウザーの中止をAPIM通信へ伝える", async () => {
  const controller = new AbortController();
  const result = await handleRerank(request(input, {}, controller.signal), dependencies({ fetch: async (_url, options) => { controller.abort(); assert.equal(options?.signal?.aborted, true); throw new Error("cancelled"); } }));
  assert.equal(result.status, 499);
});
