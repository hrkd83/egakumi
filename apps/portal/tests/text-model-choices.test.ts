import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseTextModelVariant, groupTextModels, resolveTextModelVariant, updateTextModelSelection } from "../lib/text-model-choices.ts";
import { buildTextGenerationRequest, getTextGenerationEndpoint, getTextGenerationRequestSchema, getTextGenerationResponseSchema } from "../lib/model-integration.ts";
import type { TextGenerationModel } from "../lib/model-types.ts";

/** 順序や表示名に依存せず、実モデルとAPI方式で選択できるカタログを用意します。 */
function model(id: string, operationId: TextGenerationModel["operationId"], providerId: TextGenerationModel["providerId"] = "openai"): TextGenerationModel {
  return { id, label: "Same display label", provider: providerId, providerId, operationId,
    apiId: `${providerId}-inference-v1`, path: operationId === "responses-create" ? "/models/openai/v1/responses" : `/models/${providerId}/v1/chat/completions`,
    profileId: `${id}-${operationId}`, maxRequestBytes: 65_536, maxOutputTokens: 16_384,
    outputTokenParameter: operationId === "responses-create" ? "max_output_tokens" : "max_completion_tokens" };
}
const chat = model("gpt-current", "chat-completions"), responses = model("gpt-current", "responses-create");
const chatOnly = model("gpt-chat-only", "chat-completions");
const entries = [chat, responses, chatOnly];

test("同じ実モデルのAPI方式だけをまとめ、カード件数とモデル選択を重複させない", () => {
  const choices = groupTextModels([...entries, chat]);
  assert.equal(choices.length, 2);
  assert.deepEqual(choices.map((choice) => [choice.id, choice.variants.map((variant) => variant.operationId)]), [
    ["gpt-current", ["chat-completions", "responses-create"]], ["gpt-chat-only", ["chat-completions"]],
  ]);
  assert.equal(groupTextModels([chat, { ...chat, providerId: "xai" }]).length, 2);
});

test("OpenAIはResponsesを既定とし、対応する明示選択は保持、非対応方式は利用可能なものへ戻す", () => {
  const choices = groupTextModels(entries);
  assert.equal(chooseTextModelVariant(choices[0]).operationId, "responses-create");
  assert.equal(chooseTextModelVariant(choices[0], "chat-completions").operationId, "chat-completions");
  assert.equal(chooseTextModelVariant(choices[1], "responses-create").operationId, "chat-completions");
  assert.equal(resolveTextModelVariant(entries, { modelId: "missing", operationId: "chat-completions" }), responses);
  assert.equal(resolveTextModelVariant(entries, { modelId: chat.id, operationId: "chat-completions" }), chat);
  assert.equal(resolveTextModelVariant([], { modelId: "missing" }), undefined);
});

test("方式変更後の実行入力とJSON・URL・スキーマ・Streamを同じ方式に揃える", () => {
  const original = { modelId: chat.id, operationId: chat.operationId, message: "比較する同じメッセージ", maxOutputTokens: 8_192, stream: true };
  const selected = updateTextModelSelection(original, responses);
  assert.equal(selected.operationId, "responses-create"); assert.equal(selected.message, original.message); assert.equal(selected.stream, true);
  const responsePayload = buildTextGenerationRequest(responses, selected);
  assert.equal(responsePayload.stream, true); assert.equal(responsePayload.max_output_tokens, responses.maxOutputTokens);
  assert.ok("input" in responsePayload); assert.ok(!("messages" in responsePayload));
  assert.match(getTextGenerationEndpoint(responses, "https://gateway.example.test"), /\/responses$/);
  assert.match(JSON.stringify(getTextGenerationRequestSchema(responses)), /max_output_tokens/);
  assert.match(JSON.stringify(getTextGenerationResponseSchema(responses, true)), /response\./);
  const reverted = updateTextModelSelection(selected, chatOnly), chatPayload = buildTextGenerationRequest(chatOnly, reverted);
  assert.equal(reverted.operationId, "chat-completions"); assert.equal(reverted.message, original.message);
  assert.ok("messages" in chatPayload); assert.ok(!("input" in chatPayload));
  assert.match(getTextGenerationEndpoint(chatOnly, "https://gateway.example.test"), /\/chat\/completions$/);
});

test("モデル・方式の選択時は小さい前回値を引き継がず最大値にし、自動上限でも入力文を維持する", () => {
  const input = { modelId: chat.id, message: "keep", maxOutputTokens: 8_192, stream: false };
  assert.equal(updateTextModelSelection(input, { ...responses, maxOutputTokens: 4_096 }).maxOutputTokens, 4_096);
  const automatic = updateTextModelSelection(input, { ...responses, maxOutputTokens: null });
  assert.equal(automatic.maxOutputTokens, undefined); assert.equal(automatic.message, input.message);
  assert.equal(updateTextModelSelection(input, responses).maxOutputTokens, responses.maxOutputTokens);
  assert.equal(updateTextModelSelection({ ...input, maxOutputTokens: 256 }, { ...responses, maxOutputTokens: 128_000 }).maxOutputTokens, 128_000);
  assert.equal(updateTextModelSelection({ ...input, maxOutputTokens: Number.NaN }, responses).maxOutputTokens, responses.maxOutputTokens);
  assert.equal(updateTextModelSelection({ ...input, maxOutputTokens: undefined }, responses).maxOutputTokens, responses.maxOutputTokens);
});
