import assert from "node:assert/strict";
import { test } from "node:test";
import ts from "typescript";
import { EMBEDDING_SAMPLE_TEXTS, buildEmbeddingRequest, defaultEmbeddingInput, getEmbeddingDefaultDimensions, getEmbeddingDimensionChoices, getEmbeddingEndpoint, getEmbeddingHeaders, getEmbeddingIntegrationExample, getEmbeddingRequestSchema, getEmbeddingResponseSchema } from "../lib/embedding-integration.ts";
import { embeddingInputSchema } from "../lib/embedding-request.ts";
import { INTEGRATION_LANGUAGES } from "../lib/model-integration.ts";
import { getEmbeddingDictionary } from "../lib/i18n/embedding.ts";
import { smallEmbedding as small, largeEmbedding as large, cohereEmbedding as cohere } from "./helpers/embedding-fixtures.ts";

test("埋め込み入力は文章配列を受け付け、URL・秘密・未知の設定は拒否する", () => {
  const input = defaultEmbeddingInput(small);
  assert.equal(embeddingInputSchema.safeParse(input).success, true);
  for (const extra of [{ prompt: "other" }, { url: "https://other.invalid" }, { apiKey: "secret" }, { stream: true }]) assert.equal(embeddingInputSchema.safeParse({ ...input, ...extra }).success, false);
  for (const dimensions of [0, -1, 1.1, 3_073, "256", NaN]) assert.equal(embeddingInputSchema.safeParse({ ...input, dimensions }).success, false);
});

test("OpenAIは次元未指定時にdimensionsを省略し、入力文とfloat形式を送る", () => {
  const body = buildEmbeddingRequest(small, defaultEmbeddingInput(small));
  assert.deepEqual(body, { model: small.id, input: [EMBEDDING_SAMPLE_TEXTS[0]], encoding_format: "float" });
  assert.equal(getEmbeddingDefaultDimensions(small), 1_536);
  assert.equal(getEmbeddingDefaultDimensions(large), 3_072);
  assert.deepEqual(buildEmbeddingRequest(large, { ...defaultEmbeddingInput(large), dimensions: 3_072, texts: [...EMBEDDING_SAMPLE_TEXTS] }), { model: large.id, input: [...EMBEDDING_SAMPLE_TEXTS], encoding_format: "float", dimensions: 3_072 });
});

test("Cohereは入力の用途とfloat配列を送り、出力次元はoutput_dimensionで指定する", () => {
  for (const inputType of cohere.inputTypes) {
    const body = buildEmbeddingRequest(cohere, { ...defaultEmbeddingInput(cohere), inputType, dimensions: 256 });
    assert.deepEqual(body, { model: cohere.id, texts: [EMBEDDING_SAMPLE_TEXTS[0]], input_type: inputType, embedding_types: ["float"], output_dimension: 256 });
  }
  assert.equal("output_dimension" in buildEmbeddingRequest(cohere, defaultEmbeddingInput(cohere)), false);
  assert.deepEqual(getEmbeddingDimensionChoices(cohere), [256, 512, 1_024, 1_536]);
});

test("モデルの次元上限・Cohere対応次元・用途・API側の配列上限を越えない", () => {
  assert.throws(() => buildEmbeddingRequest(small, { ...defaultEmbeddingInput(small), dimensions: 1_537 }));
  assert.throws(() => buildEmbeddingRequest(cohere, { ...defaultEmbeddingInput(cohere), dimensions: 768 }));
  assert.throws(() => buildEmbeddingRequest(cohere, { ...defaultEmbeddingInput(cohere), inputType: "arbitrary" }));
  assert.throws(() => buildEmbeddingRequest(small, { ...defaultEmbeddingInput(small), inputType: "search_query" }));
  assert.throws(() => buildEmbeddingRequest({ ...small, maxInputs: 1 }, { ...defaultEmbeddingInput(small), texts: [...EMBEDDING_SAMPLE_TEXTS] }));
});

test("公開URLは認証情報・任意パス・クエリを含む起点やパストラバーサルを受け付けない", () => {
  assert.equal(getEmbeddingEndpoint(cohere, "https://gateway.example.invalid"), "https://gateway.example.invalid/models/cohere/v1/embed");
  for (const origin of ["http://other.invalid", "https://user:secret@other.invalid", "https://other.invalid/path", "https://other.invalid?secret=value"]) assert.throws(() => getEmbeddingEndpoint(small, origin));
  for (const path of ["//other.invalid", "/models/../auth", "/models/openai?key=value", "/models/\\secret"]) assert.throws(() => getEmbeddingEndpoint({ ...small, path }, "https://gateway.example.invalid"));
  assert.equal(getEmbeddingHeaders()["api-key"], "${GATEWAY_API_KEY}");
});

test("全言語の組み込み例は選択した本文・モデル・次元・バッチに連動し、ブラウザー用キーを含めない", () => {
  for (const model of [small, large, cohere]) for (const language of INTEGRATION_LANGUAGES) {
    const input = { ...defaultEmbeddingInput(model), dimensions: 256, texts: [...EMBEDDING_SAMPLE_TEXTS] };
    const code = getEmbeddingIntegrationExample(language, model, input, "https://gateway.example.invalid");
    assert.ok(code.includes(model.id));
    assert.ok(code.includes(model.path));
    assert.ok(code.includes("GATEWAY_API_KEY"));
    assert.ok(code.includes(EMBEDDING_SAMPLE_TEXTS[0]) && code.includes(EMBEDDING_SAMPLE_TEXTS[1]));
    assert.ok(code.includes(model.providerId === "cohere" ? '"output_dimension": 256' : '"dimensions": 256'));
    assert.equal(code.includes("X-Egakumi-Profile"), false);
    if (language === "typescript") assert.deepEqual(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext }, reportDiagnostics: true }).diagnostics, []);
  }
});

test("部分スキーマはAPI固有のパラメーターとfloat応答を正しく示す", () => {
  const openai = getEmbeddingRequestSchema(small) as { properties: Record<string, unknown> };
  const cohereSchema = getEmbeddingRequestSchema(cohere) as { properties: Record<string, unknown> };
  assert.ok("input" in openai.properties && "dimensions" in openai.properties);
  assert.equal("texts" in openai.properties, false);
  assert.ok("texts" in cohereSchema.properties && "input_type" in cohereSchema.properties && "output_dimension" in cohereSchema.properties);
  assert.ok(JSON.stringify(getEmbeddingResponseSchema(cohere)).includes('"float"'));
});

test("日本語と英語の文言は全項目が揃い、会社や利用者の固有情報を持たない", () => {
  const ja = getEmbeddingDictionary("ja");
  const en = getEmbeddingDictionary("en");
  assert.deepEqual(Object.keys(ja), Object.keys(en));
  assert.ok(Object.values(ja).every(Boolean) && Object.values(en).every(Boolean));
});

/** 配備別名や既定値が変わっても、モデルIDをハードコードして別の上限へ戻しません。 */
test("API正本由来の既定・最大次元を配備別名でも保持し、画面の選択肢と本文へ適用する", () => {
  const alias = { ...small, id: "synthetic-embedding-alias", embeddingDimensions: { default: 512, maximum: 1024 } };
  assert.equal(getEmbeddingDefaultDimensions(alias), 512);
  assert.deepEqual(getEmbeddingDimensionChoices(alias), [256, 512, 1024]);
  assert.equal(buildEmbeddingRequest(alias, { ...defaultEmbeddingInput(alias), dimensions: 1024 }).dimensions, 1024);
  assert.throws(() => buildEmbeddingRequest(alias, { ...defaultEmbeddingInput(alias), dimensions: 1025 }));
  const requestSchema = getEmbeddingRequestSchema(alias) as { properties: { dimensions: { maximum: number } } };
  assert.equal(requestSchema.properties.dimensions.maximum, 1024);
  const choices = getEmbeddingDimensionChoices(cohere);
  choices.pop();
  assert.deepEqual(getEmbeddingDimensionChoices(cohere), cohere.embeddingDimensions.allowed);
});
