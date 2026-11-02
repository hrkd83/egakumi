import assert from "node:assert/strict";
import { test } from "node:test";
import { buildRerankRequest, defaultRerankInput, getRerankEndpoint, getRerankHeaders, getRerankIntegrationExample, getRerankRequestSchema, getRerankResponseSchema } from "../lib/rerank-integration.ts";
import { rerankInputSchema } from "../lib/rerank-request.ts";
import { parseRerankResult, readRerankResponse } from "../lib/rerank-response.ts";
import type { RerankModel } from "../lib/rerank-types.ts";

const model: RerankModel = { id: "Cohere-rerank-v4.0-fast", label: "Cohere-rerank-v4.0-fast", providerId: "cohere", provider: "Cohere", apiId: "cohere-inference-v1", operationId: "rerank", path: "/models/cohere/v1/rerank", maxRequestBytes: 65_536, maxDocuments: 8, minTopN: 1, maxTopN: 3 };
const input = defaultRerankInput(model);

test("リランクは任意の質問・複数文書を保ち、最大返却件数を初期値にする", () => {
  assert.equal(input.topN, 3);
  const edited = { ...input, query: "任意の質問\n'apostrophe'", documents: ["私の文書", "another document"], topN: 2 };
  assert.deepEqual(buildRerankRequest(model, edited), { model: model.id, query: edited.query, documents: edited.documents, top_n: 2 });
  assert.equal(defaultRerankInput({ ...model, maxDocuments: 2 }).topN, 2);
});

test("空白、未知項目、不正モデル、文書数より多い返却件数を拒否する", () => {
  for (const bad of [{ ...input, query: " " }, { ...input, documents: [""] }, { ...input, documents: [] }, { ...input, topN: 0 }, { ...input, endpoint: "https://other.invalid" }]) assert.equal(rerankInputSchema.safeParse(bad).success, false);
  assert.throws(() => buildRerankRequest(model, { ...input, modelId: "other" }));
  assert.throws(() => buildRerankRequest(model, { ...input, documents: ["one"], topN: 2 }));
  assert.throws(() => buildRerankRequest({ ...model, maxDocuments: 1 }, input));
  assert.throws(() => buildRerankRequest(model, { ...input, documents: Array(9).fill("x") }));
  assert.throws(() => buildRerankRequest(model, { ...input, documents: Array(4).fill("x"), topN: 4 }));
  assert.throws(() => buildRerankRequest({ ...model, minTopN: 2 }, { ...input, topN: 1 }));
});

test("正本の件数上限が増えた場合も旧固定値で拒否しない", () => {
  const expanded = { ...model, maxDocuments: 12, maxTopN: 10 };
  const expandedInput = { ...input, documents: Array.from({ length: 12 }, (_, index) => `document ${index}`), topN: 10 };
  assert.equal((buildRerankRequest(expanded, expandedInput).documents as string[]).length, 12);
  const results = Array.from({ length: 10 }, (_, index) => ({ index, relevance_score: (10 - index) / 10 }));
  assert.equal(parseRerankResult({ results }, expanded, expandedInput, 0).ranking.length, 10);
});

test("コード例の接続先とヘッダーは公開固定パスと環境変数だけを使う", () => {
  assert.equal(getRerankEndpoint(model, "https://gateway.example.invalid"), "https://gateway.example.invalid/models/cohere/v1/rerank");
  for (const origin of ["http://other.invalid", "https://user:pass@other.invalid", "https://other.invalid/path", "https://other.invalid?x=1", "https://other.invalid#x"]) assert.throws(() => getRerankEndpoint(model, origin));
  assert.throws(() => getRerankEndpoint({ ...model, path: "//attacker.invalid" }, "https://gateway.example.invalid"));
  assert.equal(getRerankHeaders()["api-key"], "${GATEWAY_API_KEY}");
});

test("全言語の例が同じ入力を使い、埋め込み引用符や改行でコードを壊さない", () => {
  const edited = { ...input, query: "'''\nJSON\n$(never-run) \\ path", documents: ['"""', "日本語 'quotes'", "line\nline"] };
  for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
    const code = getRerankIntegrationExample(language, model, edited, "https://gateway.example.invalid");
    assert.ok(code.includes("GATEWAY_API_KEY"));
    assert.ok(code.includes(model.id));
    assert.ok(code.includes("/models/cohere/v1/rerank"));
    assert.equal(code.includes("base64"), false);
  }
  const python = getRerankIntegrationExample("python", model, edited, "https://gateway.example.invalid");
  const raw = /payload = json.loads\(r'''\n([\s\S]*?)\n'''\)/.exec(python)![1];
  assert.deepEqual(JSON.parse(raw), buildRerankRequest(model, edited));
  const curl = getRerankIntegrationExample("curl", model, edited, "https://gateway.example.invalid");
  assert.ok(curl.includes("'\\''"));
  assert.equal(curl.includes("--location"), false);
});

test("スキーマはGateway制約と0始まりindexを説明し、出力トークンを捏造しない", () => {
  const schema = getRerankRequestSchema(model) as { properties: Record<string, Record<string, unknown>> };
  assert.equal(schema.properties.documents.maxItems, 8);
  assert.equal(schema.properties.top_n.maximum, 3);
  assert.equal(JSON.stringify(schema).includes("max_output_tokens"), false);
  assert.ok(JSON.stringify(getRerankResponseSchema()).includes("Zero-based"));
});

test("応答のindexを元文書へ結び付け、関連度・使用量・生JSONを保持する", async () => {
  const value = { results: [{ index: 2, relevance_score: 0.9 }, { index: 0, relevance_score: 0.5 }, { index: 1, relevance_score: 0.1 }], meta: { billed_units: { search_units: 1 } } };
  const parsed = parseRerankResult(value, model, input, 10, "req-123");
  assert.equal(parsed.ranking[0].document, input.documents[2]);
  assert.equal(parsed.ranking[0].index, 2);
  assert.equal(parsed.searchUnits, 1);
  assert.deepEqual(parsed.rawResponse, value);
  assert.deepEqual(await readRerankResponse(Response.json(parsed)), parsed);
});

test("重複・範囲外index、不正スコア、順位逆転、件数不足は成功として扱わない", async () => {
  for (const results of [
    [{ index: 0, relevance_score: 0.9 }],
    [{ index: 0, relevance_score: 0.9 }, { index: 0, relevance_score: 0.6 }, { index: 1, relevance_score: 0.1 }],
    [{ index: 3, relevance_score: 0.9 }, { index: 0, relevance_score: 0.6 }, { index: 1, relevance_score: 0.1 }],
    [{ index: 2, relevance_score: Infinity }, { index: 0, relevance_score: 0.6 }, { index: 1, relevance_score: 0.1 }],
    [{ index: 2, relevance_score: 0.2 }, { index: 0, relevance_score: 0.6 }, { index: 1, relevance_score: 0.1 }],
  ]) assert.throws(() => parseRerankResult({ results }, model, input, 0));
  await assert.rejects(() => readRerankResponse(Response.json({ error: { code: "private detail" } }, { status: 500 })), /GenerationFailed/);
  await assert.rejects(() => readRerankResponse(new Response("{}", { headers: { "Content-Type": "text/plain" } })), /GenerationFailed/);
});
