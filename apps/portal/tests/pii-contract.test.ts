import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { PII_API, PII_SAMPLE, buildPiiRequest, piiInputSchema, piiResponseSchema } from "../lib/pii-contract.ts";
import { getPiiEndpoint, getPiiHeaders, getPiiIntegrationExample, getPiiRequestSchema, getPiiResponseSchema } from "../lib/pii-integration.ts";
import { readPiiResponse } from "../lib/pii-response.ts";

const result = { redactedText: "連絡先は***********************です。", language: "ja", detectedCount: 1, modelVersion: PII_API.modelVersion, warningCodes: [], requestId: "f5235390-eec1-4803-986b-0901a5d6ba19" };

test("PIIの公開パス・上限・モデル版は正本のAPI定義に一致する", () => {
  const api = JSON.parse(readFileSync(new URL("../../gateway/apim/apis/pii-v1.json", import.meta.url), "utf8"));
  const operation = api.operations.find((entry: { name: string }) => entry.name === PII_API.operationId);
  const limits = api.authorization.operationConstraints[PII_API.operationId];
  assert.equal(api.name, PII_API.apiId);
  assert.equal(`/${api.path}${operation.urlTemplate}`, PII_API.path);
  assert.equal(operation.method, "POST");
  assert.equal(limits.maxRequestBytes, PII_API.maxRequestBytes);
  assert.equal(limits.modelVersion, PII_API.modelVersion);
  assert.deepEqual(limits.allowedLanguages, [...PII_API.languages]);
  assert.deepEqual(limits.allowedWarningCodes, [...PII_API.allowedWarningCodes]);
});

test("PIIの公開スキーマと表示スキーマの必須項目・型・言語が一致する", () => {
  const source = JSON.parse(readFileSync(new URL("../../gateway/apim/schemas/pii.json", import.meta.url), "utf8")).schemas;
  const request = getPiiRequestSchema(), response = getPiiResponseSchema();
  assert.deepEqual(request.required, source.PiiRedactRequest.required);
  assert.deepEqual(response.required, source.PiiRedactResult.required);
  assert.deepEqual(Object.keys(response.properties as object), Object.keys(source.PiiRedactResult.properties));
  assert.equal(request.additionalProperties, false);
  assert.equal(response.additionalProperties, false);
});

test("本文はUnicode文字数で勝手に切らずUTF-8のJSON全体上限を適用する", () => {
  assert.deepEqual(buildPiiRequest(PII_SAMPLE), PII_SAMPLE);
  assert.doesNotThrow(() => buildPiiRequest({ text: "😀".repeat(5120), language: "ja" }));
  assert.throws(() => buildPiiRequest({ text: "あ".repeat(11000), language: "ja" }));
  for (const value of [{ text: " ", language: "ja" }, { text: "hello", language: "fr" }, { ...PII_SAMPLE, url: "https://example.net" }]) assert.equal(piiInputSchema.safeParse(value).success, false);
});

test("応答はマスキング結果と許可メタデータだけを受け取り追加された原文を拒否する", () => {
  assert.equal(piiResponseSchema.safeParse(result).success, true);
  for (const value of [{ ...result, entities: [{ text: "secret" }] }, { ...result, input: PII_SAMPLE.text }, { ...result, warningCodes: ["Unexpected"] }, { ...result, detectedCount: -1 }, { ...result, modelVersion: "latest" }]) assert.equal(piiResponseSchema.safeParse(value).success, false);
});

test("5言語のガイドは同じ編集済み本文と固定公開パスを使いキーは環境変数だけ", () => {
  const input = { text: "文中に'引用'と\\記号を含みます。", language: "ja" as const };
  assert.equal(getPiiEndpoint("https://gateway.example.com"), "https://gateway.example.com/tools/pii/v1/redact");
  for (const origin of ["http://example.com", "https://user:pass@example.com", "https://example.com/path"]) assert.throws(() => getPiiEndpoint(origin));
  for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
    const code = getPiiIntegrationExample(language, input, "https://gateway.example.com");
    assert.match(code, /GATEWAY_API_KEY/); assert.match(code, /\/tools\/pii\/v1\/redact/); assert.match(code, /language/);
    assert.doesNotMatch(code, /connectionSession|X-Core-Connection/);
  }
  assert.equal(getPiiHeaders()["api-key"], "${GATEWAY_API_KEY}");
});

test("ブラウザー側も型と応答上限を検査し自由なエラー文を表示しない", async () => {
  assert.deepEqual((await readPiiResponse(Response.json({ result, durationMs: 10 }))).result, result);
  await assert.rejects(readPiiResponse(Response.json({ result: { ...result, input: "original" }, durationMs: 10 })), /GenerationFailed/);
  await assert.rejects(readPiiResponse(Response.json({ error: { code: "Anything", message: "private" } }, { status: 502 })), /GenerationFailed/);
  await assert.rejects(readPiiResponse(new Response("x".repeat(131073), { headers: { "content-type": "application/json" } })), /GenerationFailed/);
});
