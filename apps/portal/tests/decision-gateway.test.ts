import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { handleDecision, type DecisionGatewayDependencies } from "../lib/decision-gateway.ts";
import { defaultDecisionInput, readDecisionResponse } from "../lib/decision-integration.ts";
import type { DecisionModel } from "../lib/decision-types.ts";

const origin = "https://portal.example.invalid";
const gateway = "https://gateway.example.invalid";
const key = "test-only-decision-subscription-secret";
const apiKeyId = "portal-11111111-1111-4111-8111-111111111111";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const model: DecisionModel = { id: "decision-1", label: "decision-1", providerId: "microsoft", provider: "Microsoft", apiId: "microsoft-decision-v1", operationId: "systemone", path: "/models/microsoft-decision/v1/systemone", maxRequestBytes: 8192, responseModel: "Microsoft-Decision-1" };
const input = defaultDecisionInput(model);

/** 外部へ出ずに、3種類の正しい回答を返します。 */
function body() { return { model: model.responseModel, answers: { refund: { type: "noul", noul: 0.9 }, team: { type: "choice", choice: "billing", probabilities: { billing: 0.9, engineering: 0.05, support: 0.05 } }, severity: { type: "score", score: 1.1, probabilities: { "0": 0.1, "1": 0.7, "2": 0.2 } } } }; }

/** 本人と台帳キー・通信を試験用の境界へ置き換えます。 */
function dependencies(overrides: Partial<DecisionGatewayDependencies> = {}): DecisionGatewayDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => origin, readGatewayEnvironment: async () => ({ origin: gateway }), readModels: () => [model], resolveSubscriptionKey: async () => key, fetch: async () => Response.json(body(), { headers: { "apim-request-id": "request_123" } }), ...overrides };
}

/** 任意の追加入力でも、送信前の検証を省略せず通します。 */
function request(value: unknown = input, headers: Record<string, string> = {}, signal?: AbortSignal): Request {
  return new Request(`${origin}/api/models/decision`, { method: "POST", headers: { Origin: origin, "Content-Type": "application/json", ...headers }, body: JSON.stringify({ apiKeyId, ...(value as Record<string, unknown>) }), signal });
}

test("本人・同一オリジン・JSON形式を確認できない要求は送信しない", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("not called"); } });
  assert.equal((await handleDecision(request(input, { Origin: "https://other.invalid" }), context)).status, 403);
  assert.equal((await handleDecision(request(input, { "sec-fetch-site": "cross-site" }), context)).status, 403);
  const missing = request(); missing.headers.delete("origin");
  assert.equal((await handleDecision(missing, context)).status, 403);
  assert.equal((await handleDecision(request(), { ...context, readSession: async () => null })).status, 401);
  assert.equal((await handleDecision(request(), { ...context, readSession: async () => { throw new Error(key); } })).status, 503);
  assert.equal((await handleDecision(request(input, { "Content-Type": "text/plain" }), context)).status, 415);
  assert.equal(calls, 0);
});

test("入力制約、モデル固定先、要求バイト上限を外部送信前に検証する", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("not called"); } });
  for (const invalid of [{ ...input, modelId: "unknown" }, { ...input, stream: true }, { ...input, questions: {} }]) assert.equal((await handleDecision(request(invalid), context)).status, 400);
  assert.equal((await handleDecision(request({ ...input, state: { text: "あ".repeat(3000) } }), context)).status, 413);
  assert.equal((await handleDecision(request(input, { "Content-Length": "524288001" }), context)).status, 413);
  for (const changed of [{ ...model, path: "/models/other" }, { ...model, apiId: "other" }, { ...model, operationId: "other" }]) assert.equal((await handleDecision(request(), { ...context, readModels: () => [changed as DecisionModel] })).status, 503);
  assert.equal(calls, 0);
});

test("本人が選んだ台帳キーを毎回照会し、公開固定APIへ購読キー方式で一度だけ送る", async () => {
  let calls = 0;
  const response = await handleDecision(request(), dependencies({
    resolveSubscriptionKey: async (owner, id, apiId) => { assert.deepEqual(owner, actor); assert.equal(id, apiKeyId); assert.equal(apiId, "microsoft-decision-v1"); return key; },
    fetch: async (url, options) => {
      calls++;
      assert.equal(String(url), `${gateway}${model.path}`);
      assert.equal(options?.redirect, "error"); assert.equal(options?.cache, "no-store");
      const headers = new Headers(options?.headers);
      assert.equal(headers.get("api-key"), key); assert.equal(headers.get("Authorization"), null); assert.equal(headers.get("cookie"), null);
      assert.deepEqual(JSON.parse(String(options?.body)), { model: model.id, state: input.state, questions: input.questions });
      return Response.json(body(), { headers: { "apim-request-id": "request_123" } });
    },
  }));
  assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
  const data = await readDecisionResponse(response);
  assert.equal(data.answers.length, 3); assert.equal(data.requestId, "request_123"); assert.equal(JSON.stringify(data).includes(key), false); assert.equal(calls, 1);
});

test("BFFのJSON読取から上流・ブラウザー応答まで予約名を通常データとして往復する", async () => {
  const edited = { modelId: model.id,
    state: JSON.parse('{"__proto__":{"marker":"入力"},"items":[{"__proto__":{"marker":"配列"}}],"constructor":"通常名","prototype":"🙂"}'),
    questions: JSON.parse('{"__proto__":{"type":"choice","instructions":"候補を選ぶ","criteria":{"__proto__":"候補1","constructor":"候補2"}}}'),
  };
  const native = JSON.parse('{"model":"Microsoft-Decision-1","__proto__":{"marker":"応答"},"answers":{"__proto__":{"type":"choice","choice":"__proto__","probabilities":{"__proto__":0.8,"constructor":0.2}}}}');
  let calls = 0;
  const response = await handleDecision(request(edited), dependencies({ fetch: async (_url, options) => {
    calls++;
    assert.deepEqual(JSON.parse(String(options?.body)), { model: model.id, state: edited.state, questions: edited.questions });
    return Response.json(native);
  } }));
  assert.equal(response.status, 200);
  const parsed = await readDecisionResponse(response);
  assert.deepEqual(parsed.answers, [{ name: "__proto__", type: "choice", value: "__proto__", probabilities: native.answers.__proto__.probabilities }]);
  assert.deepEqual(parsed.rawResponse, native);
  assert.equal(Object.hasOwn(parsed.rawResponse, "__proto__"), true);
  assert.equal(Object.hasOwn(parsed.answers[0].probabilities!, "__proto__"), true);
  assert.equal(Object.hasOwn(Object.prototype, "marker"), false);
  assert.equal(calls, 1);
});

test("権限を失ったキーや失敗したキー照会を別のキーで代替しない", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("not called"); } });
  assert.equal((await handleDecision(request(), { ...context, resolveSubscriptionKey: async () => { throw new ApiKeyError("AccessDenied", 403); } })).status, 403);
  assert.equal((await handleDecision(request(), { ...context, resolveSubscriptionKey: async () => { throw new Error(key); } })).status, 503);
  assert.equal((await handleDecision(request(), { ...context, readGatewayEnvironment: async () => ({ origin: "https://user:secret@other.invalid" }) })).status, 503);
  assert.equal(calls, 0);
});

test("不正な成功本文、秘密反射、過大応答を画面へ出さない", async () => {
  for (const value of [{ ...body(), privateField: key }, { ...body(), model: "other" }, { model: model.responseModel, answers: {} }, { error: { code: key } }]) {
    const response = await handleDecision(request(), dependencies({ fetch: async () => Response.json(value) }));
    assert.equal(response.status, 502); assert.equal((await response.text()).includes(key), false);
  }
  assert.equal((await handleDecision(request(), dependencies({ fetch: async () => new Response(" ".repeat(1_048_577), { headers: { "content-type": "application/json" } }) }))).status, 502);
  for (const status of [302, 400, 401, 403, 500]) {
    const response = await handleDecision(request(), dependencies({ fetch: async () => new Response(key, { status }) }));
    assert.equal(response.status, 502); assert.equal((await response.text()).includes(key), false);
  }
  const response = await handleDecision(request(), dependencies({ fetch: async () => new Response(key, { status: 429, headers: { "retry-after": "30" } }) }));
  assert.equal(response.status, 429); assert.deepEqual(await response.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 30 });
});

test("応答途中の停滞・ブラウザー切断を中止し、再送しない", async () => {
  const keepAlive = setTimeout(() => undefined, 1000);
  let cancelled = false, calls = 0;
  try {
    const response = await handleDecision(request(), dependencies({ timeoutMs: 10, fetch: async () => {
      calls++; return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"answers":')); }, cancel() { cancelled = true; } }), { headers: { "content-type": "application/json" } });
    } }));
    assert.equal(response.status, 504); assert.equal(cancelled, true); assert.equal(calls, 1);
  } finally { clearTimeout(keepAlive); }
  const controller = new AbortController();
  const response = await handleDecision(request(input, {}, controller.signal), dependencies({ fetch: async (_url, options) => { controller.abort(); assert.equal(options?.signal?.aborted, true); throw new Error("cancelled"); } }));
  assert.equal(response.status, 499);
});

test("引用符付きキーが回答JSONの値や名前へ反射されても公開しない", async () => {
  const credential = 'synthetic-decision-"key"-\\-value';
  for (const reflected of [{ metadata: { reflected: credential } }, { [credential]: "marker" }]) {
    const raw = JSON.stringify({ ...body(), ...reflected }); assert.equal(raw.includes(credential), false);
    const response = await handleDecision(request(), dependencies({ resolveSubscriptionKey: async () => credential, fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) }));
    assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
  }
});

test("ブラウザーの応答検証も未知のエラー・壊れた成功データを受け付けない", async () => {
  await assert.rejects(() => readDecisionResponse(Response.json({ error: { code: key } }, { status: 500 })), (error: unknown) => error instanceof Error && error.message === "GenerationFailed");
  await assert.rejects(() => readDecisionResponse(Response.json({ model: "broken" })));
});
