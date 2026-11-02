import assert from "node:assert/strict";
import { test } from "node:test";
import { buildDecisionRequest, defaultDecisionInput, getDecisionEndpoint, getDecisionHeaders, getDecisionIntegrationExample, getDecisionRequestSchema, getDecisionResponseSchema } from "../lib/decision-integration.ts";
import { decisionInputSchema, parseDecisionResult } from "../lib/decision-request.ts";
import type { DecisionModel } from "../lib/decision-types.ts";

const model: DecisionModel = { id: "decision-1", label: "decision-1", providerId: "microsoft", provider: "Microsoft", apiId: "microsoft-decision-v1", operationId: "systemone", path: "/models/microsoft-decision/v1/systemone", maxRequestBytes: 8192, responseModel: "Microsoft-Decision-1" };

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
