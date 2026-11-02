import assert from "node:assert/strict";
import test from "node:test";
import { getTextGenerationModels } from "../lib/model-catalog.ts";
import { buildTextGenerationRequest, getTextGenerationRequestSchema } from "../lib/model-integration.ts";
import { getMinimumTextOutputTokens, textGenerationExecutionSchema } from "../lib/model-request.ts";
import { chatModel, responsesModel } from "./helpers/model-fixtures.ts";
import { handleTextGeneration } from "../lib/model-gateway.ts";

test("Responsesの16未満を拒否し、Chatの1とモデル最大の既定値を維持する", () => {
  for (const value of [1, 15]) {
    assert.throws(() => buildTextGenerationRequest(responsesModel, { message: "Say OK", maxOutputTokens: value, stream: false }), RangeError);
    assert.equal(textGenerationExecutionSchema.safeParse({ apiKeyId: "portal-11111111-1111-4111-8111-111111111111", modelId: responsesModel.id, operationId: "responses-create", message: "Say OK", maxOutputTokens: value, stream: false }).success, false);
  }
  assert.equal(buildTextGenerationRequest(responsesModel, { message: "Say OK", maxOutputTokens: 16, stream: false }).max_output_tokens, 16);
  assert.equal(buildTextGenerationRequest(chatModel, { message: "Say OK", maxOutputTokens: 1, stream: false }).max_completion_tokens, 1);
  assert.equal(buildTextGenerationRequest(responsesModel, { message: "Say OK", maxOutputTokens: responsesModel.maxOutputTokens, stream: false }).max_output_tokens, responsesModel.maxOutputTokens);
});

test("全公開Responsesモデルのフォームと部分スキーマがAPI正本の下限16を使う", () => {
  for (const model of getTextGenerationModels()) {
    const minimum = model.operationId === "responses-create" ? 16 : 1;
    assert.equal(getMinimumTextOutputTokens(model), minimum);
    if (model.maxOutputTokens !== null) {
      const schema = getTextGenerationRequestSchema(model) as { properties: Record<string, { minimum: number }> };
      assert.equal(schema.properties[model.outputTokenParameter].minimum, minimum);
    }
  }
});

test("方式を省略した不正なResponses入力もキー取得・上流送信より前に拒否する", async () => {
  let secretReads = 0;
  let upstreamCalls = 0;
  const response = await handleTextGeneration(new Request("https://portal.example.invalid/api/models/generate", {
    method: "POST", headers: { Origin: "https://portal.example.invalid", "Content-Type": "application/json" },
    body: JSON.stringify({ apiKeyId: "portal-11111111-1111-4111-8111-111111111111", modelId: responsesModel.id, message: "Say OK", maxOutputTokens: 1, stream: false }),
  }), {
    readSession: async () => ({ tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" }),
    readPortalOrigin: () => "https://portal.example.invalid", readModels: () => [responsesModel],
    readGatewayEnvironment: async () => ({ origin: "https://gateway.example.invalid" }),
    resolveSubscriptionKey: async () => { secretReads++; return "unused-test-subscription-key"; },
    fetch: async () => { upstreamCalls++; return Response.json({}); },
  });
  assert.equal(response.status, 400);
  assert.equal(secretReads, 0);
  assert.equal(upstreamCalls, 0);
});
