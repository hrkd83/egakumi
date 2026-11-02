import assert from "node:assert/strict";
import { test } from "node:test";
import { ModelResponseError, readModelResponse } from "../lib/model-response.ts";
import { chatModel as model } from "./helpers/model-fixtures.ts";
import type { TextGenerationModel } from "../lib/model-types.ts";
import { MODEL_MAX_EVENT_BYTES } from "../lib/model-request.ts";



/** 通信境界を1バイトまで分割し、UTF-8やSSEの区切りが分割される状況を再現します。 */
function streamResponse(value: string, chunkSize = 1): Response {
  const bytes = new TextEncoder().encode(value);
  let offset = 0;
  return new Response(new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= bytes.byteLength) { controller.close(); return; }
      controller.enqueue(bytes.slice(offset, offset + chunkSize));
      offset += chunkSize;
    },
  }), { headers: { "Content-Type": "text/event-stream;charset=utf-8", "apim-request-id": "safe-id" } });
}

/** 任意の上流エラー文が公開エラーへ含まれないことを確認します。 */
async function expectFailure(response: Response, code = "GenerationFailed", selectedModel = model) {
  await assert.rejects(readModelResponse(response, selectedModel), (error) => {
    assert.ok(error instanceof ModelResponseError);
    assert.equal(error.failure.error.code, code);
    assert.equal(error.message, code);
    return true;
  });
}

test("非ストリーム応答は実際のJSONを保持し、表示用本文とトークン数を返す", async () => {
  const rawResponse = { model: "test-model", choices: [{ message: { content: "OK" } }] };
  const result = await readModelResponse(Response.json({
    text: "OK", model: "test-model", durationMs: 32, rawResponse,
    usage: { inputTokens: 10, outputTokens: 2, totalTokens: 12 }, requestId: "safe-id", finishReason: "stop",
  }), model);
  assert.deepEqual(result.rawResponse, rawResponse);
  assert.equal(result.text, "OK");
  assert.equal(result.durationMs, 32);
  assert.deepEqual(result.usage, { inputTokens: 10, outputTokens: 2, totalTokens: 12 });
});

test("Chat SSEの分割UTF-8・CRLF・使用量だけのチャンク・DONEを処理する", async () => {
  const updates: string[] = [];
  const body = [
    ": keepalive",
    "",
    'data: {"object":"chat.completion.chunk","model":"test-model","choices":[{"index":0,"delta":{"content":"こんにちは"},"finish_reason":null}]}',
    "",
    'data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"。"},"finish_reason":"stop"}]}',
    "",
    'data: {"object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}',
    "", "data: [DONE]", "", "",
  ].join("\r\n");
  const result = await readModelResponse(streamResponse(body), model, { onStreamUpdate: (current) => updates.push(current.text) });
  assert.equal(result.text, "こんにちは。");
  assert.equal(result.finishReason, "stop");
  assert.equal(result.requestId, "safe-id");
  assert.deepEqual(result.usage, { inputTokens: 5, outputTokens: 2, totalTokens: 7 });
  assert.ok(updates.includes("こんにちは"));
  assert.equal((result.rawResponse.events as unknown[]).length, 4);
});

test("Responses SSEはevent名と本文を残し、完了応答のモデル・使用量を反映する", async () => {
  const responsesModel: TextGenerationModel = { ...model, operationId: "responses-create", outputTokenParameter: "max_output_tokens" };
  const body = [
    'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"答え"}\n\n',
    'event: response.completed\ndata: {"type":"response.completed","response":{"model":"test-model-version","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"答えです"}]}],"usage":{"input_tokens":5,"output_tokens":3,"total_tokens":8}}}\n\n',
  ].join("");
  const result = await readModelResponse(streamResponse(body, 3), responsesModel);
  assert.equal(result.text, "答えです");
  assert.equal(result.model, "test-model-version");
  assert.equal(result.finishReason, "completed");
  assert.deepEqual(result.usage, { inputTokens: 5, outputTokens: 3, totalTokens: 8 });
  assert.equal((result.rawResponse.events as { event: string }[])[0].event, "response.output_text.delta");
});

test("同じモデルのResponses選択では完成メッセージを採用し、Chat専用のDONEを完了とみなさない", async () => {
  const selected: TextGenerationModel = { ...model, operationId: "responses-create", path: "/models/openai/v1/responses", profileId: "response", outputTokenParameter: "max_output_tokens" };
  const final = { model: model.id, status: "completed", output: [{ type: "reasoning", summary: [] }, { type: "message", content: [{ type: "output_text", text: "最終の回答" }, { type: "refusal", refusal: "補足" }] }] };
  const body = `event: response.output_text.delta\ndata: ${JSON.stringify({ type: "response.output_text.delta", delta: "途中の文章" })}\n\nevent: response.completed\ndata: ${JSON.stringify({ type: "response.completed", response: final })}\n\n`;
  const result = await readModelResponse(streamResponse(body, 5), selected);
  assert.equal(result.model, model.id); assert.equal(result.text, "最終の回答\n補足"); assert.equal(result.finishReason, "completed");
  assert.deepEqual((result.rawResponse.events as { data: { response?: unknown } }[]).at(-1)?.data.response, final);
  await expectFailure(streamResponse('data: {"choices":[{"delta":{"content":"別のAPI"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'), "GenerationFailed", selected);
});

test("出力上限で終わるResponsesは途中の結果とincompleteを保持する", async () => {
  const responsesModel: TextGenerationModel = { ...model, operationId: "responses-create", outputTokenParameter: "max_output_tokens" };
  const response = streamResponse('data: {"type":"response.refusal.delta","delta":"回答"}\n\ndata: {"type":"response.incomplete","response":{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}}\n\n');
  const result = await readModelResponse(response, responsesModel);
  assert.equal(result.text, "回答");
  assert.equal(result.finishReason, "incomplete");
  assert.equal(result.usage, undefined);
});

test("正常な終端前の切断、未完のフレーム、不正UTF-8を成功として扱わない", async () => {
  await expectFailure(streamResponse('data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"stop"}]}\n\n'));
  await expectFailure(streamResponse('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]'));
  await expectFailure(streamResponse('data: [DONE]\n\n'));
  await expectFailure(new Response(new Uint8Array([0xc3]), { headers: { "content-type": "text/event-stream" } }));
});

test("HTTP失敗とSSEのportal.errorは固定コードだけを画面へ返す", async () => {
  await expectFailure(Response.json({ error: { code: "RateLimitExceeded", message: "private details" }, retryAfterSeconds: 2 }, { status: 429 }), "RateLimitExceeded");
  await expectFailure(streamResponse('event: portal.error\ndata: {"error":{"code":"GatewayTimeout","message":"private details"},"requestId":"safe-timeout-id"}\n\n'), "GatewayTimeout");
  await expectFailure(streamResponse('data: {"error":{"message":"private upstream details"}}\n\n'));
  await expectFailure(Response.json({ error: { code: "private-code" } }, { status: 502 }));
});

test("Responsesの失敗イベントと上限超過のフレームを拒否する", async () => {
  const responsesModel: TextGenerationModel = { ...model, operationId: "responses-create", outputTokenParameter: "max_output_tokens" };
  await expectFailure(streamResponse('event: response.failed\ndata: {"type":"response.failed","response":{"status":"failed","error":{"message":"private"}}}\n\n'), "GenerationFailed", responsesModel);
  await expectFailure(streamResponse(`data: ${"x".repeat(MODEL_MAX_EVENT_BYTES + 1)}\n\n`, 1_048_576));
});

test("頻繁なイベントの再描画を抑えても、終端では全イベントを通知する", async () => {
  const updates: number[] = [];
  const frames = Array.from({ length: 100 }, () => 'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}\n\n');
  frames.push('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n', "data: [DONE]\n\n");
  const result = await readModelResponse(streamResponse(frames.join(""), 65536), model, {
    onStreamUpdate: (current) => updates.push((current.rawResponse.events as unknown[]).length),
  });
  assert.equal(result.text, "a".repeat(100));
  assert.equal((result.rawResponse.events as unknown[]).length, 102);
  assert.equal(updates.at(-1), 102);
  assert.ok(updates.length < 25, "短い間隔のイベントをまとめて表示する");
});

test("失敗時にも、表示を抑制していた直前の正常イベントを保持する", async () => {
  const updates: string[] = [];
  const response = streamResponse([
    'data: {"choices":[{"delta":{"content":"前半"}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"後半"}}]}\n\n',
    'event: portal.error\ndata: {"error":{"code":"GatewayTimeout"}}\n\n',
  ].join(""), 65536);
  await assert.rejects(readModelResponse(response, model, { onStreamUpdate: (current) => updates.push(current.text) }), ModelResponseError);
  assert.equal(updates.at(-1), "前半後半");
});

test("Azureの先頭annotationフレームは保持し、空のモデル名で選択モデルを上書きしない", async () => {
  const updates: string[] = [];
  const annotation = { choices: [], object: "", model: "", prompt_filter_results: [{ prompt_index: 0, content_filter_results: {} }] };
  const response = streamResponse([
    `data: ${JSON.stringify(annotation)}\n\n`,
    'data: {"object":"chat.completion.chunk","model":"test-model-version","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n',
    "data: [DONE]\n\n",
  ].join(""), 65536);
  const result = await readModelResponse(response, model, { onStreamUpdate: (current) => updates.push(current.model) });
  assert.equal(updates[0], "test-model");
  assert.equal(result.model, "test-model-version");
  assert.equal(result.text, "OK");
  assert.deepEqual((result.rawResponse.events as { data: unknown }[])[0].data, annotation);
});
