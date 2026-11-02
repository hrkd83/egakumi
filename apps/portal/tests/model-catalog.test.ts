import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import { getDecisionModels, getEmbeddingModels, getExecutableModelTargets, getImageGenerationModels, getModelCatalog, getRealtimeModels, getRerankModels, getSpeechModels, getTextGenerationModels } from "../lib/model-catalog.ts";

const definitionDirectory = new URL("../../gateway/apim/apis/", import.meta.url);

/** テストも正本を独立に読み、追加・削除したAPIがカタログから取り残されないことを確認します。 */
function sourceDefinitions() {
  return readdirSync(definitionDirectory).filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(new URL(file, definitionDirectory), "utf8")));
}

test("全Model APIの操作を重複なく7タブに含め、Tool APIは混在しない", () => {
  const catalog = getModelCatalog();
  assert.deepEqual(catalog.map((category) => category.id),
    ["text", "embeddings", "image", "speech", "realtime", "rerank", "decision"]);
  assert.ok(catalog.every((category) => category.operations.length > 0));
  const actual = catalog.flatMap((category) => category.operations.map((operation) => `${operation.apiId}/${operation.operationId}`));
  const expected = sourceDefinitions().filter((definition) => definition.path.startsWith("models/"))
    .flatMap((definition) => definition.operations.map((operation: { name: string }) => `${definition.name}/${operation.name}`));
  assert.deepEqual([...actual].sort(), expected.sort());
  assert.equal(new Set(actual).size, actual.length);
});

test("音声ファイル処理とリアルタイム処理を分離し、Cohereの埋め込みとリランクを別タブへ分類する", () => {
  const catalog = getModelCatalog();
  assert.deepEqual(catalog.find((category) => category.id === "speech")?.apiIds,
    ["azure-speech-v1", "openai-audio-v1"]);
  assert.deepEqual(catalog.find((category) => category.id === "rerank")?.operations.map((operation) => operation.operationId), ["rerank"]);
  assert.ok(catalog.find((category) => category.id === "embeddings")?.operations.some((operation) => operation.operationId === "cohere-embeddings"));
  assert.ok(catalog.find((category) => category.id === "realtime")?.operations.flatMap((operation) => operation.models).includes("gpt-live-transcribe"));
  assert.ok(catalog.flatMap((category) => category.operations).every((operation) =>
    operation.models.every((model) => !model.startsWith("__"))));
  assert.ok(!JSON.stringify(catalog).includes("backendAudience"));
});

test("テキスト一覧は正本の許可モデルを網羅し、各モデルに一致するテキスト用制約を採用する", () => {
  const definition = sourceDefinitions().find((item) => item.name === "openai-inference-v1");
  const constraints = definition.authorization.operationConstraints;
  const expected = ["chat-completions", "responses-create"].flatMap((operationId) =>
    constraints[operationId].allowedModels.map((modelId: string) => `${operationId}/${modelId}`)).sort();
  const models = getTextGenerationModels();
  assert.deepEqual(models.map((model) => `${model.operationId}/${model.id}`).sort(), expected);
  assert.equal(new Set(models.map((model) => `${model.operationId}/${model.id}`)).size, models.length);
  for (const model of models) {
    const constraint = constraints[model.operationId];
    const profile = constraint.profiles.find((item: { profileId: string }) => item.profileId === model.profileId);
    assert.ok(profile.allowedModels.includes(model.id));
    assert.deepEqual(profile.modalities, ["text"]);
    const capabilities = JSON.parse(readFileSync(new URL("../../gateway/apim/model-capabilities.json", import.meta.url), "utf8"));
    const capability = capabilities.entries.find((entry: { modelId: string; apiIds: string[]; operationIds: string[] }) => entry.modelId === model.id && entry.apiIds.includes(model.apiId) && entry.operationIds.includes(model.operationId));
    assert.equal(model.maxOutputTokens, capability.maxOutputTokens);
    assert.equal(model.outputTokenParameter, profile.outputTokenParameter);
    assert.equal(model.maxRequestBytes, profile.maxRequestBytes);
  }
  assert.deepEqual(models.filter((model) => model.id === "gpt-6.1-sol").map((model) => model.operationId).sort(),
    ["chat-completions", "responses-create"]);
  assert.deepEqual(models.filter((model) => model.id === "gpt-5.4-pro").map((model) => model.operationId), ["responses-create"]);
});

test("埋め込み一覧はOpenAIとCohereの正本だけを実行先とし、配列上限と入力用途を引き継ぐ", () => {
  const models = getEmbeddingModels();
  assert.deepEqual(models.map((model) => model.id).sort(), ["embed-v-4-0", "text-embedding-3-large", "text-embedding-3-small"]);
  for (const model of models) {
    const source = sourceDefinitions().find((item) => item.name === model.apiId);
    const operation = source.operations.find((item: { name: string }) => item.name === model.operationId);
    const constraint = source.authorization.operationConstraints[model.operationId];
    assert.equal(model.path, `/${source.path}${operation.urlTemplate}`);
    assert.ok(constraint.allowedModels.includes(model.id));
    assert.equal(model.maxRequestBytes, constraint.maxRequestBytes);
    assert.equal(model.maxInputs, constraint.arrayLimits[model.providerId === "openai" ? "input" : "texts"]);
    assert.deepEqual(model.inputTypes, constraint.stringChoices?.input_type ?? []);
  }
});

test("リランクと意思決定の公開モデル・要求上限を正本から引き継ぎ、応答モデル名と配備名を分ける", () => {
  const rerank = getRerankModels();
  const rerankSource = sourceDefinitions().find((item) => item.name === "cohere-inference-v1");
  const limits = rerankSource.authorization.operationConstraints.rerank;
  assert.deepEqual(rerank.map((model) => model.id), limits.allowedModels);
  for (const model of rerank) {
    assert.equal(model.path, `/${rerankSource.path}/rerank`);
    assert.equal(model.maxDocuments, limits.arrayLimits.documents);
    assert.equal(model.minTopN, limits.integerLimits.top_n.minimum);
    assert.equal(model.maxTopN, limits.integerLimits.top_n.maximum);
    assert.equal(model.maxRequestBytes, limits.maxRequestBytes);
  }
  const decision = getDecisionModels();
  const decisionSource = sourceDefinitions().find((item) => item.name === "microsoft-decision-v1");
  assert.deepEqual(decision.map((model) => model.id), decisionSource.authorization.operationConstraints.systemone.allowedModels);
  assert.equal(decision[0].path, `/${decisionSource.path}/systemone`);
  assert.equal(decision[0].maxRequestBytes, decisionSource.authorization.operationConstraints.systemone.maxRequestBytes);
  assert.equal(decision[0].responseModel, "Microsoft-Decision-1");
  assert.ok(!JSON.stringify([...rerank, ...decision]).includes("backendAudience"));
});

test("画像の生成用一覧へ編集操作を混ぜず、FLUXはモデルに一致する生成プロファイルを使用する", () => {
  const models = getImageGenerationModels();
  assert.equal(models.length, 5);
  assert.equal(new Set(models.map((model) => model.id)).size, models.length);
  for (const model of models) {
    const source = sourceDefinitions().find((item) => item.name === model.apiId);
    const operation = source.operations.find((item: { name: string }) => item.name === model.operationId);
    const constraint = source.authorization.operationConstraints[model.operationId];
    assert.ok(!operation.name.includes("edit"));
    assert.equal(model.path, `/${source.path}${operation.urlTemplate}`);
    assert.ok(constraint.allowedModels.includes(model.id));
    if (model.providerId === "openai") {
      assert.deepEqual(model.sizes, ["1024x1024"]);
      assert.deepEqual(model.qualities, ["low"]);
    } else {
      const profile = constraint.profiles.find((item: { profileId: string }) => item.profileId === model.profileId);
      assert.deepEqual(profile.allowedModels, [model.id]);
      assert.deepEqual(profile.modalities, ["text"]);
      assert.equal(model.minWidth, profile.integerLimits.width.minimum);
      assert.equal(model.maxWidth, profile.integerLimits.width.maximum);
      assert.equal(model.minHeight, profile.integerLimits.height.minimum);
      assert.equal(model.maxHeight, profile.integerLimits.height.maximum);
      assert.deepEqual(model.formats, profile.stringChoices.output_format);
    }
  }
  assert.ok(!JSON.stringify(models).includes("backendUrl"));
});

test("音声は正本の3操作を形式・本文上限に結び付け、OpenAIのモデルはパスに束縛する", () => {
  const models = getSpeechModels();
  assert.deepEqual(models.map((model) => model.id), ["azure-speech-stt", "azure-speech-tts", "gpt-transcribe"]);
  for (const model of models) {
    const source = sourceDefinitions().find((item) => item.name === model.apiId);
    const operation = source.operations.find((item: { name: string }) => item.name === model.operationId);
    const constraint = source.authorization.operationConstraints[model.operationId];
    assert.equal(model.maxRequestBytes, constraint.maxRequestBytes);
    assert.equal(model.path, `/${source.path}${operation.urlTemplate.replace("{deployment}", model.modelId ?? "")}`);
    assert.equal(model.language, new URL(sourceDefinitions().find((item) => item.name === "azure-speech-v1").operations.find((item: { name: string }) => item.name === "stt").backendUrlTemplate, "https://definition.invalid").searchParams.get("language"));
    if (model.apiId === "openai-audio-v1") assert.ok(constraint.allowedModels.includes(model.modelId));
  }
  assert.ok(!JSON.stringify(models).includes("backendUrl"));
  assert.ok(!models.some((model) => model.providerId === "openai" && model.kind === "tts"));
});

test("リアルタイムは公開APIと方式を束縛し、観測・終了を独立モデルとして表示しない", () => {
  const models = getRealtimeModels();
  assert.equal(models.length, 8);
  assert.equal(new Set(models.map((model) => model.id)).size, models.length);
  for (const model of models) {
    const source = sourceDefinitions().find((item) => item.name === model.apiId);
    const operation = source.operations.find((item: { name: string }) => item.name === model.operationId);
    assert.equal(model.path, `/${source.path}${operation.urlTemplate}`);
    assert.ok(!model.modelId.startsWith("__"));
    assert.ok(!model.apiId.includes("observer"));
    if (model.family === "realtime" && model.transport === "webrtc") {
      assert.equal(model.observer?.apiId, "openai-realtime-observer-v1");
      assert.equal(model.hangupPath, "/models/openai-realtime/v1/calls/{callId}/hangup");
    } else { assert.equal(model.observer, undefined); assert.equal(model.hangupPath, undefined); }
    if (model.family === "voice-live") assert.equal(model.modelId, "gpt-4.1");
    if (model.modelId.endsWith("-mini")) assert.equal(model.transport, "websocket");
  }
  assert.ok(!JSON.stringify(models).includes("backendUrl"));
});

test("購読候補は実装済みの全カテゴリを含み、各APIを重複させない", () => {
  assert.deepEqual(getExecutableModelTargets().map((target) => target.apiId).sort(), [
    "azure-speech-v1", "azure-voice-live-webrtc-v1", "azure-voice-live-ws-v1", "blackforest-images-v1", "cohere-inference-v1", "microsoft-decision-v1", "openai-audio-v1", "openai-embeddings-v1", "openai-images-v1", "openai-inference-v1", "openai-live-control-v1", "openai-live-ws-v1", "openai-realtime-control-v1", "openai-realtime-ws-v1", "openai-transcription-ws-v1",
  ]);
});
