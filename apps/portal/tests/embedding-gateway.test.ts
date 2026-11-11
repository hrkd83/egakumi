import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { handleEmbeddings, type EmbeddingGatewayDependencies } from "../lib/embedding-gateway.ts";
import { defaultEmbeddingInput, EMBEDDING_SAMPLE_TEXTS } from "../lib/embedding-integration.ts";
import { parseEmbeddingResult, readEmbeddingResponse } from "../lib/embedding-response.ts";
import { smallEmbedding as small, cohereEmbedding as cohere } from "./helpers/embedding-fixtures.ts";

const portalOrigin = "https://portal.example.invalid";
const gatewayOrigin = "https://gateway.example.invalid";
const key = "test-only-embedding-subscription-key";
const apiKeyId = "portal-11111111-1111-4111-8111-111111111111";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const input = { ...defaultEmbeddingInput(small), dimensions: 3 };

test("任意の複数文章を順序どおり送信し、その件数のベクトルを返す", async () => {
  const texts = ["引用\"と改行\nを含む文章", "'''や${text}を含む文章", "三つ目の文章"];
  const response = await handleEmbeddings(request({ ...input, texts }), dependencies({ fetch: async (_url, options) => {
    assert.deepEqual(JSON.parse(String(options?.body)).input, texts);
    return Response.json({ model: small.id, data: texts.map((_text, index) => ({ index, embedding: [0.1, 0.2, 0.3] })) });
  } }));
  assert.equal(response.status, 200); assert.equal((await response.json()).vectorCount, 3);
});

/** 認証済みの同一オリジンPOSTを、固定された公開入力で作ります。 */
function request(value: unknown = input, headers: Record<string, string> = {}, signal?: AbortSignal): Request {
  return new Request(`${portalOrigin}/api/models/embeddings`, { method: "POST", headers: { Origin: portalOrigin, "Content-Type": "application/json", ...headers }, body: JSON.stringify({ apiKeyId, ...(value as Record<string, unknown>) }), signal });
}

/** 上流通信と認証だけを置き換え、ハンドラー自身の制約はそのまま検証します。 */
function dependencies(overrides: Partial<EmbeddingGatewayDependencies> = {}): EmbeddingGatewayDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => portalOrigin, readGatewayEnvironment: async () => ({ origin: gatewayOrigin }), resolveSubscriptionKey: async () => key, readModels: () => [small, cohere], fetch: async () => Response.json({ model: small.id, data: [{ index: 0, embedding: [0.1, -0.2, 0.3] }], usage: { prompt_tokens: 12, total_tokens: 12 } }, { headers: { "apim-request-id": "request_123" } }), ...overrides };
}

test("認証と同一オリジンを確認できない埋め込み要求はAPIMへ送らない", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("should not call"); } });
  assert.equal((await handleEmbeddings(request(input, { Origin: "https://other.invalid" }), context)).status, 403);
  assert.equal((await handleEmbeddings(request(input, { "sec-fetch-site": "cross-site" }), context)).status, 403);
  const missing = request(); missing.headers.delete("Origin");
  assert.equal((await handleEmbeddings(missing, context)).status, 403);
  assert.equal((await handleEmbeddings(request(), { ...context, readSession: async () => null })).status, 401);
  assert.equal((await handleEmbeddings(request(), { ...context, readSession: async () => { throw new Error("private database detail"); } })).status, 503);
  assert.equal(calls, 0);
});

test("未知項目・未知モデル・不正な寸法と用途・過大本文を転送前に拒否する", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("should not call"); } });
  for (const invalid of [{ ...input, prompt: "other" }, { ...input, modelId: "unknown" }, { ...input, dimensions: 1_537 }, { ...input, inputType: "search_query" }, { ...defaultEmbeddingInput(cohere), dimensions: 768 }]) assert.equal((await handleEmbeddings(request(invalid), context)).status, 400);
  assert.equal((await handleEmbeddings(request(input, { "Content-Type": "text/plain" }), context)).status, 415);
  assert.equal((await handleEmbeddings(request(input, { "Content-Length": "524288001" }), context)).status, 413);
  assert.equal(calls, 0);
});

test("OpenAIを選んだ本人の購読キーと入力本文で一回だけ実行し、全ベクトルと実JSONを返す", async () => {
  let calls = 0;
  const result = await handleEmbeddings(request(), dependencies({
    resolveSubscriptionKey: async (owner, id, apiId) => { assert.deepEqual(owner, actor); assert.equal(id, apiKeyId); assert.equal(apiId, "openai-embeddings-v1"); return key; },
    fetch: async (url, options) => {
      calls++;
      assert.equal(String(url), `${gatewayOrigin}${small.path}`);
      assert.equal(options?.redirect, "error"); assert.equal(options?.cache, "no-store");
      assert.equal(new Headers(options?.headers).get("api-key"), key);
      assert.equal(new Headers(options?.headers).get("cookie"), null);
      assert.deepEqual(JSON.parse(String(options?.body)), { model: small.id, input: [EMBEDDING_SAMPLE_TEXTS[0]], encoding_format: "float", dimensions: 3 });
      return dependencies().fetch!(url, options);
    },
  }));
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("cache-control"), "no-store");
  const data = await result.json();
  assert.deepEqual(data.vectors, [[0.1, -0.2, 0.3]]);
  assert.equal(data.dimensions, 3); assert.equal(data.vectorCount, 1);
  assert.deepEqual(data.usage, { inputTokens: 12, totalTokens: 12 });
  assert.equal(data.rawResponse.data[0].embedding.length, 3);
  assert.equal(data.requestId, "request_123");
  assert.equal(JSON.stringify(data).includes(key), false);
  assert.equal(calls, 1);
});

test("Cohereは実配備名とfloat配列を使い、billed_units.input_tokensを表示へ取り出す", async () => {
  const result = await handleEmbeddings(request({ ...defaultEmbeddingInput(cohere), dimensions: 256, texts: [...EMBEDDING_SAMPLE_TEXTS], inputType: "search_query" }), dependencies({ fetch: async (url, options) => {
    assert.equal(String(url), `${gatewayOrigin}${cohere.path}`);
    assert.deepEqual(JSON.parse(String(options?.body)), { model: cohere.id, texts: [...EMBEDDING_SAMPLE_TEXTS], input_type: "search_query", embedding_types: ["float"], output_dimension: 256 });
    return Response.json({ id: "sample", embeddings: { float: [Array(256).fill(0.1), Array(256).fill(0.2)] }, meta: { billed_units: { input_tokens: 20, image_tokens: 0 } }, response_type: "embeddings_floats" });
  } }));
  assert.equal(result.status, 200);
  const data = await result.json();
  assert.equal(data.vectorCount, 2); assert.equal(data.dimensions, 256);
  assert.deepEqual(data.usage, { inputTokens: 20 });
  assert.equal(data.rawResponse.response_type, "embeddings_floats");
});

test("現在の所属で選択キーを利用できない場合やキー解決失敗時に代替キーで実行しない", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("should not call"); }, resolveSubscriptionKey: async () => { throw new ApiKeyError("AccessDenied", 403); } });
  assert.equal((await handleEmbeddings(request(), context)).status, 403);
  assert.equal((await handleEmbeddings(request(), { ...context, resolveSubscriptionKey: async () => { throw new Error("private ARM details"); } })).status, 503);
  assert.equal(calls, 0);
});

test("公開APIと違うパスや関係のないAPIのモデル定義を実行しない", async () => {
  for (const model of [{ ...small, path: "/models/other/v1" }, { ...small, apiId: "cohere-inference-v1" as const }, { ...small, providerId: "cohere" as const }]) assert.equal((await handleEmbeddings(request(), dependencies({ readModels: () => [model] }))).status, 503);
  assert.equal((await handleEmbeddings(request(), dependencies({ readGatewayEnvironment: async () => ({ origin: "https://user:secret@other.invalid" }) }))).status, 503);
});

test("上流の認可エラー・リダイレクト・任意本文は隠し、429だけは待機秒数を返す", async () => {
  for (const status of [302, 400, 401, 403, 500]) {
    const result = await handleEmbeddings(request(), dependencies({ fetch: async () => new Response(`secret=${key}`, { status }) }));
    assert.equal(result.status, 502); assert.equal((await result.text()).includes(key), false);
  }
  const limited = await handleEmbeddings(request(), dependencies({ fetch: async () => new Response("private", { status: 429, headers: { "retry-after": "45" } }) }));
  assert.equal(limited.status, 429); assert.deepEqual(await limited.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 45 });
});

test("上流の欠損・不正な数値・次元ずれ・件数ずれ・重複index・秘密の反射を成功として公開しない", async () => {
  const values = [
    { model: small.id, data: [{ index: 0, embedding: [1, null, 3] }] },
    { model: small.id, data: [{ index: 0, embedding: [1, 2] }] },
    { model: small.id, data: [{ index: 0, embedding: [1, 2, 3] }, { index: 0, embedding: [1, 2, 3] }] },
    { model: small.id, data: [{ index: 0, embedding: [1, 2, 3] }], privateField: key },
    { error: { code: "private" } },
  ];
  for (const value of values) { const result = await handleEmbeddings(request(), dependencies({ fetch: async () => Response.json(value) })); assert.equal(result.status, 502); assert.equal((await result.text()).includes(key), false); }
  assert.throws(() => parseEmbeddingResult({ model: small.id, data: [{ index: 0, embedding: [Infinity] }] }, small, { ...input, dimensions: 1 }, 0));
});

test("Content-Lengthがない過大応答も中止する", async () => {
  const result = await handleEmbeddings(request(), dependencies({ fetch: async () => new Response(" ".repeat(1_048_577), { headers: { "content-type": "application/json" } }) }));
  assert.equal(result.status, 502);
});

test("JSONのUnicodeエスケープで隠れた購読キーも正常応答として返さない", async () => {
  const encoded = [...key].map((character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`).join("");
  const raw = JSON.stringify({ model: small.id, data: [{ index: 0, embedding: [1, 2, 3] }], metadata: { reflected: key } }).replace(key, encoded);
  assert.equal(raw.includes(key), false);
  let calls = 0;
  const response = await handleEmbeddings(request(), dependencies({ fetch: async () => { calls++; return new Response(raw, { headers: { "content-type": "application/json" } }); } }));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
  assert.equal(calls, 1);
});

test("引用符やバックスラッシュを含むキーの値・プロパティ名への反射も拒否する", async () => {
  const credential = 'synthetic-embedding-"key"-\\-value';
  for (const reflected of [{ metadata: { reflected: credential } }, { [credential]: "marker" }]) {
    const raw = JSON.stringify({ model: small.id, data: [{ index: 0, embedding: [1, 2, 3] }], ...reflected });
    assert.equal(raw.includes(credential), false);
    const response = await handleEmbeddings(request(), dependencies({ resolveSubscriptionKey: async () => credential, fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) }));
    assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
  }
});

test("ヘッダー受信後に本文が停止しても期限で中止し、再試行しない", async () => {
  let cancelled = false;
  let calls = 0;
  const keepAlive = setTimeout(() => undefined, 1_000);
  try {
    const result = await handleEmbeddings(request(), dependencies({ timeoutMs: 10, fetch: async () => {
      calls++;
      return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"data":')); }, cancel() { cancelled = true; } }), { headers: { "content-type": "application/json" } });
    } }));
    assert.equal(result.status, 504); assert.equal(cancelled, true); assert.equal(calls, 1);
  } finally { clearTimeout(keepAlive); }
});

test("ブラウザー切断をAPIMのAbortSignalへ伝え、利用者の中止として返す", async () => {
  const controller = new AbortController();
  const result = await handleEmbeddings(request(input, {}, controller.signal), dependencies({ fetch: async (_url, options) => {
    controller.abort();
    assert.equal(options?.signal?.aborted, true);
    throw new Error("cancelled");
  } }));
  assert.equal(result.status, 499);
});

test("ブラウザー側は実行結果を再検証し、未知のエラー本文を画面へ表示しない", async () => {
  const successful = await handleEmbeddings(request(), dependencies());
  assert.equal((await readEmbeddingResponse(successful)).dimensions, 3);
  await assert.rejects(() => readEmbeddingResponse(Response.json({ error: { code: "private credential detail" } }, { status: 500 })), (error: unknown) => error instanceof Error && error.message === "GenerationFailed");
  await assert.rejects(() => readEmbeddingResponse(Response.json({ model: "broken" })));
  await assert.rejects(() => readEmbeddingResponse(Response.json({ model: "broken", vectorCount: 2, dimensions: 3, vectors: [[1, 2, 3]], durationMs: 0, rawResponse: {} })));
});
