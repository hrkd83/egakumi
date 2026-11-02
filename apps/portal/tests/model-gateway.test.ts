import assert from "node:assert/strict";
import { test } from "node:test";
import { chatModel, compatibleModel, responsesModel, textGenerationModels } from "./helpers/model-fixtures.ts";
import { getModelGatewayEnvironment, handleTextGeneration } from "../lib/model-gateway.ts";
import type { ModelGenerationDependencies } from "../lib/model-gateway.ts";
import { textGenerationInputSchema, MODEL_MAX_UPSTREAM_JSON_BYTES } from "../lib/model-request.ts";
import { readModelResponse } from "../lib/model-response.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { SAMPLE_TEXT_PROMPT, getTextGenerationHeaders } from "../lib/model-integration.ts";

const portalOrigin = "https://portal.example.invalid";
const gatewayOrigin = "https://gateway.example.invalid";
const secret = "test-only-subscription-key";
const selectedKeyId = "portal-11111111-1111-4111-8111-111111111111";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const defaultInput = { message: SAMPLE_TEXT_PROMPT, modelId: chatModel.id, maxOutputTokens: 128, stream: false };

/** 同一オリジンのブラウザーから送られる生成リクエストを用意します。 */
function request(input: unknown = defaultInput, headers: Record<string, string> = {}): Request {
  return new Request(`${portalOrigin}/api/models/generate`, {
    method: "POST", headers: { Origin: portalOrigin, "Content-Type": "application/json", ...headers },
    body: JSON.stringify(input && typeof input === "object" ? { apiKeyId: selectedKeyId, ...input } : input),
  });
}

/** 秘密を含まない固定応答で通信だけを置き換えます。 */
function dependencies(overrides: Partial<ModelGenerationDependencies> = {}): ModelGenerationDependencies {
  return {
    readSession: async () => actor, readPortalOrigin: () => portalOrigin,
    readGatewayEnvironment: async () => ({ origin: gatewayOrigin }),
    resolveSubscriptionKey: async () => secret,
    readModels: () => textGenerationModels,
    fetch: async () => Response.json({
      model: chatModel.id, choices: [{ message: { content: "OK" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 },
    }, { headers: { "apim-request-id": "11111111-1111-4111-8111-111111111111" } }),
    ...overrides,
  };
}

test("生成入力は文章を受け付け、任意の接続先・秘密・未知の設定は拒否する", () => {
  assert.ok(textGenerationInputSchema.safeParse(defaultInput).success);
  for (const operationId of ["responses-create", "chat-completions"]) {
    assert.ok(textGenerationInputSchema.safeParse({ ...defaultInput, operationId }).success);
  }
  for (const operationId of [null, "", "https://other.invalid", "../responses", "unknown-operation"]) {
    assert.equal(textGenerationInputSchema.safeParse({ ...defaultInput, operationId }).success, false);
  }
  for (const extra of [{ url: "https://other.invalid" }, { apiKey: "not-allowed" }, { temperature: 1 }, { prompt: "other message" }, { systemPrompt: "other instructions" }]) {
    assert.equal(textGenerationInputSchema.safeParse({ ...defaultInput, ...extra }).success, false);
  }
  for (const maxOutputTokens of [0, Number.MAX_SAFE_INTEGER + 1, 1.5, "128"]) assert.equal(textGenerationInputSchema.safeParse({ ...defaultInput, maxOutputTokens }).success, false);
  for (const stream of [undefined, "true", 1, null]) assert.equal(textGenerationInputSchema.safeParse({ ...defaultInput, stream }).success, false);
});

test("同じ実モデル名でも明示したAPI形式・profile・トークン項目を一貫して使う", async () => {
  const responseVariant = { ...responsesModel, id: chatModel.id };
  for (const model of [chatModel, responseVariant]) {
    let requests = 0, resolutions = 0;
    const result = await handleTextGeneration(request({ ...defaultInput, operationId: model.operationId }), dependencies({
      readModels: () => [responseVariant, chatModel],
      resolveSubscriptionKey: async (principal, id, apiId) => {
        resolutions++; assert.deepEqual(principal, actor); assert.equal(id, selectedKeyId); assert.equal(apiId, model.apiId); return secret;
      },
      fetch: async (url, options) => {
        requests++;
        assert.equal(String(url), `${gatewayOrigin}${model.path}`);
        assert.equal(new Headers(options?.headers).get("X-Egakumi-Profile"), model.profileId);
        const body = JSON.parse(String(options?.body));
        assert.equal(body.model, chatModel.id);
        assert.equal(body[model.outputTokenParameter], defaultInput.maxOutputTokens);
        assert.equal("operationId" in body, false);
        assert.equal("apiKeyId" in body, false);
        if (model.operationId === "responses-create") {
          assert.equal(body.input, SAMPLE_TEXT_PROMPT); assert.equal(body.store, false); assert.equal("messages" in body, false);
          return Response.json({ model: model.id, status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: "Responses" }] }] });
        }
        assert.deepEqual(body.messages, [{ role: "user", content: SAMPLE_TEXT_PROMPT }]); assert.equal("input" in body, false);
        return Response.json({ model: model.id, choices: [{ message: { content: "Chat" } }] });
      },
    }));
    assert.equal(result.status, 200); assert.equal(requests, 1); assert.equal(resolutions, 1);
    assert.equal((await result.json()).text, model.operationId === "responses-create" ? "Responses" : "Chat");
  }
});

test("形式省略時はカタログ順序に依存せずOpenAI Responsesを優先し、未対応ならChatを使う", async () => {
  const responseVariant = { ...responsesModel, id: chatModel.id };
  for (const models of [[chatModel, responseVariant], [responseVariant, chatModel]]) {
    let requests = 0;
    const result = await handleTextGeneration(request(), dependencies({ readModels: () => models, fetch: async (url) => {
      requests++; assert.equal(String(url), `${gatewayOrigin}${responseVariant.path}`);
      return Response.json({ model: chatModel.id, status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: "Responses" }] }] });
    } }));
    assert.equal(result.status, 200); assert.equal(requests, 1); assert.equal((await result.json()).text, "Responses");
  }
  const chatOnly = await handleTextGeneration(request(), dependencies({ readModels: () => [chatModel], fetch: async (url) => {
    assert.equal(String(url), `${gatewayOrigin}${chatModel.path}`);
    return Response.json({ model: chatModel.id, choices: [{ message: { content: "Chat" } }] });
  } }));
  assert.equal(chatOnly.status, 200);
});

test("未対応のモデルとAPI形式の組合せや重複した定義は認可・上流通信の前に拒否する", async () => {
  let requests = 0, resolutions = 0;
  const denied = dependencies({
    resolveSubscriptionKey: async () => { resolutions++; return secret; },
    fetch: async () => { requests++; throw new Error("must not fetch"); },
  });
  for (const input of [
    { ...defaultInput, operationId: "responses-create" },
    { ...defaultInput, modelId: responsesModel.id, operationId: "chat-completions" },
    { ...defaultInput, modelId: compatibleModel.id, operationId: "responses-create" },
  ]) {
    const result = await handleTextGeneration(request(input), denied);
    assert.equal(result.status, 400); assert.deepEqual(await result.json(), { error: { code: "ModelUnavailable" } });
  }
  const duplicated = await handleTextGeneration(request({ ...defaultInput, operationId: "chat-completions" }), {
    ...denied, readModels: () => [chatModel, { ...chatModel }],
  });
  assert.equal(duplicated.status, 400); assert.equal(requests, 0); assert.equal(resolutions, 0);
});

test("同一オリジンと有効なセッションがない場合は生成を開始しない", async () => {
  let calls = 0;
  const denied = dependencies({ fetch: async () => { calls++; throw new Error("should not call"); } });
  const rejectedHeaders: Record<string, string>[] = [{ Origin: "https://other.invalid" }, { "sec-fetch-site": "cross-site" }];
  for (const headers of rejectedHeaders) {
    const result = await handleTextGeneration(request(defaultInput, headers), denied);
    assert.equal(result.status, 403);
  }
  const missingOrigin = request();
  missingOrigin.headers.delete("Origin");
  assert.equal((await handleTextGeneration(missingOrigin, denied)).status, 403);
  assert.equal((await handleTextGeneration(request(), { ...denied, readSession: async () => null })).status, 401);
  assert.equal((await handleTextGeneration(request(), { ...denied, readSession: async () => { throw new Error("private database details"); } })).status, 503);
  assert.equal(calls, 0);
});

test("入力した文章を変更せず送信し、動的モデルでは最大出力フィールドを省略する", async () => {
  const message = "入力の\"引用\"と改行\n日本語、'''、${example}、JSONをそのまま扱う。";
  const response = await handleTextGeneration(request({ ...defaultInput, message }), dependencies({ fetch: async (_url, options) => {
    assert.deepEqual(JSON.parse(String(options?.body)).messages, [{ role: "user", content: message }]);
    return Response.json({ model: chatModel.id, choices: [{ message: { content: "応答" }, finish_reason: "stop" }] });
  } }));
  assert.equal(response.status, 200);
  const dynamic = { ...chatModel, maxOutputTokens: null };
  const auto = await handleTextGeneration(request({ ...defaultInput, message, maxOutputTokens: undefined }), dependencies({ readModels: () => [dynamic], fetch: async (_url, options) => {
    const body = JSON.parse(String(options?.body)); assert.equal("max_completion_tokens" in body, false);
    return Response.json({ model: dynamic.id, choices: [{ message: { content: "応答" } }] });
  } }));
  assert.equal(auto.status, 200);
  assert.equal((await handleTextGeneration(request({ ...defaultInput, message: " " }), dependencies())).status, 400);
});

test("APIのモデル・出力上限と入力バイト数を送信前に検証する", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("should not call"); } });
  assert.equal((await handleTextGeneration(request({ ...defaultInput, modelId: "unknown-model" }), context)).status, 400);
  assert.equal((await handleTextGeneration(request({ ...defaultInput, maxOutputTokens: 513 }), context)).status, 400);
  assert.equal((await handleTextGeneration(request(defaultInput, { "Content-Type": "text/plain" }), context)).status, 415);
  assert.equal((await handleTextGeneration(request(defaultInput, { "Content-Length": "40000" }), context)).status, 413);
  assert.equal((await handleTextGeneration(request({ ...defaultInput, prompt: "あ".repeat(12000) }), context)).status, 400);
  assert.equal(calls, 0);
});

test("Chat Completionsは定義のパス・プロファイル・出力パラメーターでAPIMを一回だけ呼ぶ", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async (url, options) => {
    calls++;
    assert.equal(String(url), `${gatewayOrigin}/models/openai/v1/chat/completions`);
    assert.equal(options?.redirect, "error");
    assert.equal(options?.cache, "no-store");
    const headers = new Headers(options?.headers);
    const model = compatibleModel;
    assert.deepEqual(Object.fromEntries(headers), Object.fromEntries(new Headers({ ...getTextGenerationHeaders(model, false), "api-key": secret })));
    assert.equal(headers.get("api-key"), secret);
    assert.equal(headers.get("Accept"), "application/json");
    assert.equal(headers.get("X-Egakumi-Profile"), "compatible");
    assert.equal(headers.get("cookie"), null);
    assert.deepEqual(JSON.parse(String(options?.body)), {
      model: compatibleModel.id, messages: [{ role: "user", content: SAMPLE_TEXT_PROMPT }],
      max_tokens: 128, stream: false,
    });
    return dependencies().fetch!(url, options);
  } });
  const result = await handleTextGeneration(request({ ...defaultInput, modelId: compatibleModel.id }), context);
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("cache-control"), "no-store");
  const data = await result.json();
  assert.equal(data.text, "OK");
  assert.deepEqual(data.usage, { inputTokens: 10, outputTokens: 4, totalTokens: 14 });
  assert.equal(data.requestId, "11111111-1111-4111-8111-111111111111");
  assert.equal(typeof data.durationMs, "number");
  assert.equal(data.rawResponse.choices[0].message.content, "OK");
  assert.equal(JSON.stringify(data).includes(secret), false);
  assert.equal(calls, 1);
});

test("Responses専用モデルは状態を保存しない単発要求に変換する", async () => {
  const result = await handleTextGeneration(request({ ...defaultInput, modelId: responsesModel.id, maxOutputTokens: 8192 }), dependencies({
    fetch: async (url, options) => {
      assert.equal(String(url), `${gatewayOrigin}/models/openai/v1/responses`);
      assert.deepEqual(JSON.parse(String(options?.body)), {
        model: responsesModel.id, input: SAMPLE_TEXT_PROMPT, max_output_tokens: 8192, stream: false, store: false,
      });
      return Response.json({
        model: responsesModel.id, status: "completed", error: null,
        output: [{ type: "reasoning", summary: [] }, { type: "message", content: [{ type: "output_text", text: "OK" }] }],
        usage: { input_tokens: 5, output_tokens: 3, total_tokens: 8 },
      });
    },
  }));
  assert.equal(result.status, 200);
  const data = await result.json();
  assert.equal(data.text, "OK");
  assert.equal(data.finishReason, "completed");
  assert.deepEqual(data.usage, { inputTokens: 5, outputTokens: 3, totalTokens: 8 });
  assert.equal(data.rawResponse.status, "completed");
  assert.equal(data.rawResponse.error, null);
});

test("上流の拒否・本文・リダイレクトを公開せず、429の再試行秒数だけを返す", async () => {
  for (const status of [302, 400, 401, 403, 500]) {
    const result = await handleTextGeneration(request(), dependencies({
      fetch: async () => new Response(`secret=${secret}`, { status, headers: { Location: "https://unexpected.invalid", "apim-request-id": "request_123" } }),
    }));
    assert.equal(result.status, 502);
    assert.equal((await result.text()).includes(secret), false);
  }
  const rateLimited = await handleTextGeneration(request(), dependencies({
    fetch: async () => new Response("not forwarded", { status: 429, headers: { "Retry-After": "60" } }),
  }));
  assert.equal(rateLimited.status, 429);
  assert.deepEqual(await rateLimited.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 60 });
  const invalid = await handleTextGeneration(request(), dependencies({ fetch: async () => Response.json({ secret }) }));
  assert.equal(invalid.status, 502);
  assert.deepEqual(await invalid.json(), { error: { code: "GenerationFailed" } });
});

test("Responsesの未完了処理や失敗を通常の生成成功として返さない", async () => {
  for (const status of ["queued", "in_progress", "failed", "cancelled"]) {
    const result = await handleTextGeneration(request({ ...defaultInput, modelId: responsesModel.id }), dependencies({
      fetch: async () => Response.json({ model: responsesModel.id, status, output: [], error: null }),
    }));
    assert.equal(result.status, 502);
    assert.deepEqual(await result.json(), { error: { code: "GenerationFailed" } });
  }
  const partial = await handleTextGeneration(request({ ...defaultInput, modelId: responsesModel.id }), dependencies({
    fetch: async () => Response.json({ model: responsesModel.id, status: "incomplete", output: [], error: null, incomplete_details: { reason: "max_output_tokens" } }),
  }));
  assert.equal(partial.status, 200);
  assert.equal((await partial.json()).rawResponse.incomplete_details.reason, "max_output_tokens");
});

test("上流応答のサイズと処理時間を制限し、再試行しない", async () => {
  const huge = await handleTextGeneration(request(), dependencies({ fetch: async () => new Response("a".repeat(MODEL_MAX_UPSTREAM_JSON_BYTES + 1), { headers: { "Content-Type": "application/json" } }) }));
  assert.equal(huge.status, 502);
  let calls = 0;
  const timeout = await handleTextGeneration(request(), dependencies({
    timeoutMs: 10,
    fetch: async (_url, options) => {
      calls++;
      return new Promise((_resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("fallback timer")), 200);
        options?.signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new Error("aborted")); }, { once: true });
      });
    },
  }));
  assert.equal(timeout.status, 504);
  assert.equal(calls, 1);
});

test("モデル最大出力を指定した大きな正常JSONをBFFとブラウザー読取の両方が保持する", async () => {
  const model = { ...chatModel, maxOutputTokens: 500_000 };
  const text = "あ".repeat(400_000); // UTF-8で1MiBを超える正常な応答です。
  let calls = 0;
  const response = await handleTextGeneration(request({ ...defaultInput, maxOutputTokens: 500_000 }), dependencies({ readModels: () => [model], fetch: async (_url, options) => {
    calls++; assert.equal(JSON.parse(String(options?.body)).max_completion_tokens, 500_000);
    return Response.json({ model: model.id, choices: [{ message: { content: text }, finish_reason: "stop" }] });
  } }));
  assert.equal(response.status, 200);
  const result = await readModelResponse(response, model);
  assert.equal(result.text, text); assert.equal(calls, 1);
  assert.equal((result.rawResponse.choices as { message: { content: string } }[])[0].message.content, text);
});

test("接続先はHTTPSオリジンに限定し、組み込みキーやキーファイルを読み込まない", async () => {
  assert.deepEqual(await getModelGatewayEnvironment({ PORTAL_GATEWAY_URL: gatewayOrigin }), { origin: gatewayOrigin });
  assert.deepEqual(await getModelGatewayEnvironment({ PORTAL_GATEWAY_URL: gatewayOrigin, PORTAL_GATEWAY_SUBSCRIPTION_KEY: secret,
    PORTAL_GATEWAY_SUBSCRIPTION_KEY_FILE: "missing-private-file" }), { origin: gatewayOrigin });
  for (const url of ["http://gateway.invalid", `${gatewayOrigin}/path`, `${gatewayOrigin}?api-key=secret`, "https://user:secret@gateway.invalid"])
    await assert.rejects(getModelGatewayEnvironment({ PORTAL_GATEWAY_URL: url }));
  await assert.rejects(getModelGatewayEnvironment({ PORTAL_GATEWAY_SUBSCRIPTION_KEY: secret }));
});

test("選択した台帳IDを認証済み本人とサーバー定義APIへ結び付け、キーは上流ヘッダーだけで使用する", async () => {
  let resolutions = 0;
  const result = await handleTextGeneration(request(), dependencies({
    resolveSubscriptionKey: async (principal, id, apiId) => {
      assert.deepEqual(principal, actor);
      assert.equal(id, selectedKeyId);
      assert.equal(apiId, "openai-inference-v1");
      resolutions++;
      return secret;
    },
    fetch: async (url, options) => {
      assert.equal(new Headers(options?.headers).get("api-key"), secret);
      assert.equal(String(options?.body).includes(selectedKeyId), false);
      return dependencies().fetch!(url, options);
    },
  }));
  assert.equal(result.status, 200);
  assert.equal(resolutions, 1);
  assert.equal((await result.text()).includes(secret), false);
});

test("キー未選択・台帳ID改変・認可失敗・確認障害では共通キーへ代替せずモデルを呼ばない", async () => {
  let resolutions = 0;
  let calls = 0;
  const context = dependencies({
    resolveSubscriptionKey: async () => { resolutions++; return secret; },
    fetch: async () => { calls++; throw new Error("should not call"); },
  });
  for (const apiKeyId of [undefined, "", "master", "model-users", "../other", "portal-invalid"]) {
    assert.equal((await handleTextGeneration(request({ ...defaultInput, apiKeyId }), context)).status, 400);
  }
  assert.equal(resolutions, 0);
  for (const status of [403, 404, 409, 503]) {
    const result = await handleTextGeneration(request(), { ...context,
      resolveSubscriptionKey: async () => { throw new ApiKeyError("private-authorization-error", status); },
    });
    assert.equal(result.status, status < 500 ? 403 : 503);
    assert.deepEqual(await result.json(), { error: { code: status < 500 ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable" } });
  }
  assert.equal(calls, 0);
});
