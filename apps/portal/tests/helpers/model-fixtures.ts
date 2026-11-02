import type { TextGenerationModel } from "../../lib/model-types.ts";

/** モデルの追加・廃止から独立して、生成方式と入力上限を検証する合成定義です。 */
export const chatModel: TextGenerationModel = {
  id: "test-model", label: "Test chat model", provider: "Example", providerId: "openai",
  apiId: "openai-inference-v1", operationId: "chat-completions", path: "/models/openai/v1/chat/completions",
  profileId: "chat", maxRequestBytes: 32768, maxOutputTokens: 512, outputTokenParameter: "max_completion_tokens",
};

/** 同じChat APIでも出力トークンのパラメーターが異なる形式です。 */
export const compatibleModel: TextGenerationModel = {
  ...chatModel, id: "test-compatible-model", label: "Test compatible model", providerId: "deepseek",
  profileId: "compatible", outputTokenParameter: "max_tokens",
};

/** 入力本文と終端イベントがChat APIと異なるResponses形式です。 */
export const responsesModel: TextGenerationModel = {
  ...chatModel, id: "test-responses-model", label: "Test responses model", operationId: "responses-create",
  path: "/models/openai/v1/responses", profileId: "responses", maxOutputTokens: 8192, outputTokenParameter: "max_output_tokens",
};

/** 対応する三種類の出力パラメーターを一度ずつ含む最小のモデル集合です。 */
export const textGenerationModels = [chatModel, compatibleModel, responsesModel];
