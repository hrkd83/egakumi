import assert from "node:assert/strict";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { buildDecisionRequest, defaultDecisionInput, getDecisionEndpoint, getDecisionHeaders, getDecisionIntegrationExample, getDecisionRequestSchema, getDecisionResponseSchema } from "../lib/decision-integration.ts";
import { decisionInputSchema, parseDecisionResult } from "../lib/decision-request.ts";
import type { DecisionModel } from "../lib/decision-types.ts";

const model: DecisionModel = { id: "decision-1", label: "decision-1", providerId: "microsoft", provider: "Microsoft", apiId: "microsoft-decision-v1", operationId: "systemone", path: "/models/microsoft-decision/v1/systemone", maxRequestBytes: 8192, responseModel: "Microsoft-Decision-1" };

/** JSONでは通常のデータである名前を、オブジェクトリテラルの特別構文を使わず作ります。 */
function reservedInput() {
  return { modelId: model.id,
    state: JSON.parse(String.raw`{"__proto__":{"marker":"独立したデータ"},"constructor":"quote \" and '","prototype":"通常名","nested":[{"__proto__":{"marker":"配列内🙂"},"constructor":{"prototype":"é"}}]}`),
    questions: JSON.parse(String.raw`{"__proto__":{"type":"choice","instructions":"引用 \" と日本語","criteria":{"__proto__":"候補1","constructor":"候補2","prototype":"候補3"}},"constructor":{"type":"noul","instructions":"確認"},"prototype":{"type":"score","instructions":"段階","criteria":["低","高"]}}`),
  };
}

test("状況・質問名・選択肢の予約名もownデータとして保持し、試作JSONの構造を変えない", () => {
  const input = reservedInput();
  const body = buildDecisionRequest(model, input);
  assert.deepEqual(body, { model: model.id, state: input.state, questions: input.questions });
  assert.deepEqual(JSON.parse(JSON.stringify(body)), { model: model.id, state: input.state, questions: input.questions });
  assert.equal(Object.hasOwn(body.state as object, "__proto__"), true);
  assert.equal(Object.getPrototypeOf(body.state), Object.prototype);
  assert.equal(Object.hasOwn(Object.prototype, "marker"), false);
});

test("予約名の質問・確率・応答メタデータを失わず、元の質問と照合する", () => {
  const input = reservedInput();
  const response = JSON.parse('{"model":"Microsoft-Decision-1","__proto__":{"marker":"応答の通常データ"},"answers":{"__proto__":{"type":"choice","choice":"__proto__","probabilities":{"__proto__":0.75,"constructor":0.2,"prototype":0.05}},"constructor":{"type":"noul","noul":0.4},"prototype":{"type":"score","score":0.5,"probabilities":{"0":0.5,"1":0.5}}}}');
  const parsed = parseDecisionResult(response, model, input, 1);
  assert.deepEqual(parsed.answers.map((answer) => answer.name), ["__proto__", "constructor", "prototype"]);
  assert.equal(parsed.answers[0].value, "__proto__");
  assert.deepEqual(parsed.answers[0].probabilities, response.answers.__proto__.probabilities);
  assert.equal(Object.hasOwn(parsed.answers[0].probabilities!, "__proto__"), true);
  assert.deepEqual(parsed.rawResponse, response);
  assert.equal(Object.hasOwn(parsed.rawResponse, "__proto__"), true);
  assert.equal(Object.hasOwn(Object.prototype, "marker"), false);
});

test("TypeScriptの実コード例を実行しても、配列内を含むJSONの予約名・引用・Unicodeを送信時に保持する", async () => {
  const input = reservedInput();
  const expected = buildDecisionRequest(model, input);
  let calls = 0;
  await runInNewContext(`(async () => {${getDecisionIntegrationExample("typescript", model, input, "https://gateway.example.com")}})()`, {
    process: { env: { GATEWAY_API_KEY: "independent-synthetic-key" } }, AbortSignal, console: { log() {} },
    fetch: async (url: string, init: RequestInit) => {
      calls++;
      assert.equal(url, "https://gateway.example.com/models/microsoft-decision/v1/systemone");
      assert.deepEqual(JSON.parse(String(init.body)), expected);
      assert.equal(init.redirect, "error");
      return { ok: true, json: async () => ({}) };
    },
  });
  assert.equal(calls, 1);
});

test("予約名を保持しても未知型・余分な質問フィールド・深さ32・8192バイトの制約を緩めない", () => {
  const input = reservedInput();
  for (const question of [{ type: "unknown", instructions: "x" }, { type: "noul", instructions: "x", extra: true }, { type: "choice", instructions: "x", criteria: { one: "only" } }]) {
    assert.equal(decisionInputSchema.safeParse({ ...input, questions: JSON.parse(`{"__proto__":${JSON.stringify(question)}}`) }).success, false);
  }
  assert.equal(decisionInputSchema.safeParse({ ...input, questions: { question: JSON.parse('{"type":"noul","instructions":"x","__proto__":{"extra":true}}') } }).success, false);
  assert.equal(decisionInputSchema.safeParse({ ...input, questions: { "": { type: "noul", instructions: "x" } } }).success, false);
  const nested = (depth: number) => { let value: unknown = null; for (let index = 0; index < depth; index++) value = { inner: value }; return value; };
  assert.equal(decisionInputSchema.safeParse({ ...input, state: nested(32) }).success, true);
  assert.equal(decisionInputSchema.safeParse({ ...input, state: nested(33) }).success, false);
  for (const value of [undefined, () => true, Infinity]) assert.equal(decisionInputSchema.safeParse({ ...input, state: { value } }).success, false);
  const base = { ...input, state: JSON.parse('{"__proto__":"ordinary","payload":""}') };
  const bytes = Buffer.byteLength(JSON.stringify({ model: model.id, state: base.state, questions: base.questions }));
  for (const length of [8191, 8192]) assert.equal(Buffer.byteLength(JSON.stringify(buildDecisionRequest(model, { ...base, state: { ...base.state, payload: "a".repeat(length - bytes) } }))), length);
  assert.throws(() => buildDecisionRequest(model, { ...base, state: { ...base.state, payload: "a".repeat(8193 - bytes) } }), RangeError);
});

/** 質問型3種類を含む整合した応答で、固定の判定内容を強要しないことを確認します。 */
function result() {
  return { model: "microsoft-decision-1", answers: {
    refund: { type: "noul", noul: 0.9 },
    team: { type: "choice", choice: "billing", probabilities: { billing: 0.9, engineering: 0.05, support: 0.05 }, confidence: 0.9 },
    severity: { type: "score", score: 1.1, probabilities: { "0": 0.1, "1": 0.7, "2": 0.2 } },
  }, usage: { input_tokens: 123, output_tokens: 0 } };
}

test("編集したJSONと質問名を保持し、生成用パラメーターやキーを上流本文へ混ぜない", () => {
  const input = defaultDecisionInput(model);
  input.state = { text: '引用"や改行\n、$value と `text`、記号\'を含む', count: 3, nested: { enabled: true, list: [null, "確認"] } };
  const body = buildDecisionRequest(model, input);
  assert.deepEqual(body, { model: "decision-1", state: input.state, questions: input.questions });
  for (const name of ["stream", "max_tokens", "max_output_tokens", "max_completion_tokens", "apiKeyId"]) assert.equal(Object.hasOwn(body, name), false);
  assert.equal(decisionInputSchema.safeParse({ ...input, stream: true }).success, false);
  assert.equal(decisionInputSchema.safeParse({ ...input, state: "text" }).success, false);
});

test("質問形式・選択肢とUTF-8本文上限を送信前に検査する", () => {
  const input = defaultDecisionInput(model);
  for (const questions of [{}, { test: { type: "unknown", instructions: "x" } }, { test: { type: "noul", instructions: " " } }, { test: { type: "choice", instructions: "x", criteria: { one: "only" } } }, { test: { type: "score", instructions: "x", criteria: ["only"] } }]) {
    assert.equal(decisionInputSchema.safeParse({ ...input, questions }).success, false);
  }
  assert.throws(() => buildDecisionRequest(model, { ...input, state: { text: "あ".repeat(3000) } }));
  assert.throws(() => buildDecisionRequest(model, { ...input, modelId: "unknown-model" }));
  assert.equal(decisionInputSchema.safeParse({ ...input, state: { value: Infinity } }).success, false);
  let nested: unknown = null;
  for (let index = 0; index < 35; index++) nested = { inner: nested };
  assert.equal(decisionInputSchema.safeParse({ ...input, state: { nested } }).success, false);
});

test("判定・選択・段階評価を元の質問と対応させ、確率や実使用量を返す", () => {
  const data = parseDecisionResult(result(), model, defaultDecisionInput(model), 120, "request_123");
  assert.equal(data.model, "microsoft-decision-1");
  assert.deepEqual(data.answers.map((answer) => [answer.name, answer.type, answer.value]), [["refund", "noul", 0.9], ["team", "choice", "billing"], ["severity", "score", 1.1]]);
  assert.deepEqual(data.usage, { inputTokens: 123, outputTokens: 0 });
  assert.equal(data.requestId, "request_123");
  assert.deepEqual(data.rawResponse, result());
  const otherChoice = result();
  otherChoice.answers.team = { type: "choice", choice: "support", probabilities: { billing: 0.1, engineering: 0.1, support: 0.8 }, confidence: 0.8 };
  assert.equal(parseDecisionResult(otherChoice, model, defaultDecisionInput(model), 1).answers[1].value, "support");
});

test("応答の欠損・不正な確率・未知選択肢・異なるモデル・不整合スコアを成功にしない", () => {
  const mutations: ((value: Record<string, unknown>) => void)[] = [
    (value) => { value.model = "another-model"; },
    (value) => { value.error = { message: "not successful" }; },
    (value) => { (value.answers as Record<string, unknown>).refund = { type: "noul", noul: true }; },
    (value) => { (value.answers as Record<string, unknown>).refund = { type: "noul", noul: 1.01 }; },
    (value) => { (value.answers as Record<string, unknown>).refund = { type: "noul", noul: NaN }; },
    (value) => { delete (value.answers as Record<string, unknown>).team; },
    (value) => { (value.answers as Record<string, unknown>).extra = { type: "noul", noul: 1 }; },
    (value) => { (value.answers as Record<string, unknown>).team = { type: "choice", choice: "other", probabilities: { billing: 1, engineering: 0, support: 0 } }; },
    (value) => { (value.answers as Record<string, unknown>).team = { type: "choice", choice: "support", probabilities: { billing: 0.9, engineering: 0.05, support: 0.05 } }; },
    (value) => { (value.answers as Record<string, unknown>).team = { type: "choice", choice: "billing", probabilities: { billing: 0.9, engineering: 0.1, support: 0.1 } }; },
    (value) => { (value.answers as Record<string, unknown>).severity = { type: "score", score: 1, probabilities: { "0": 0.1, "1": 0.7, "2": 0.2 } }; },
    (value) => { (value.answers as Record<string, unknown>).severity = { type: "score", score: 1.1, probabilities: { "0": 0.1, "1": 0.7, "2": 0.2 }, confidence: Infinity }; },
  ];
  for (const mutate of mutations) { const value = structuredClone(result()); mutate(value); assert.throws(() => parseDecisionResult(value, model, defaultDecisionInput(model), 1)); }
});

test("1質問の要求でも固定の3回答を要求せず、要求された質問名だけを受け付ける", () => {
  const input = { modelId: model.id, state: { text: "test" }, questions: { approved: { type: "noul" as const, instructions: "Has the request been approved?" } } };
  const data = parseDecisionResult({ model: model.responseModel, answers: { approved: { type: "noul", noul: 0.3 } } }, model, input, 1);
  assert.deepEqual(data.answers, [{ name: "approved", type: "noul", value: 0.3 }]);
});

test("コード例とスキーマは購読キー方式と正しい配備名・型を提示する", () => {
  const input = defaultDecisionInput(model);
  input.state = { message: "quote ' \" and $name `command` and\nnew line" };
  assert.equal(getDecisionEndpoint(model, "https://gateway.example.com"), "https://gateway.example.com/models/microsoft-decision/v1/systemone");
  assert.throws(() => getDecisionEndpoint(model, "https://user:secret@gateway.example.com"));
  assert.throws(() => getDecisionEndpoint({ ...model, path: "//other.invalid" }, "https://gateway.example.com"));
  assert.deepEqual(getDecisionHeaders(), { "Content-Type": "application/json", Accept: "application/json", "api-key": "${GATEWAY_API_KEY}" });
  for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
    const code = getDecisionIntegrationExample(language, model, input, "https://gateway.example.com");
    assert.match(code, /GATEWAY_API_KEY/); assert.match(code, /api-key/); assert.match(code, /systemone/);
    assert.doesNotMatch(code, /Authorization|Bearer|max_output_tokens|stream/);
  }
  assert.ok(getDecisionRequestSchema(model).properties);
  assert.ok(getDecisionResponseSchema().properties);
});
