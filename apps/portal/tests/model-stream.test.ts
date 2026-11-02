import assert from "node:assert/strict";
import { test } from "node:test";
import { chatModel, responsesModel, textGenerationModels } from "./helpers/model-fixtures.ts";
import { handleTextGeneration } from "../lib/model-gateway.ts";
import type { ModelGenerationDependencies } from "../lib/model-gateway.ts";
import { SAMPLE_TEXT_PROMPT } from "../lib/model-integration.ts";
import { MODEL_MAX_EVENT_BYTES } from "../lib/model-request.ts";
import { readModelResponse } from "../lib/model-response.ts";

const origin = "https://portal.example.invalid";
const encoder = new TextEncoder();
const identifier = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

/** プロバイダーから返るChat Completionsのチャンクを作ります。 */
function chat(content: string, finish: string | null = null): string {
  return `data: ${JSON.stringify({ id: "chat-test", object: "chat.completion.chunk", model: chatModel.id, choices: [{ index: 0, delta: { content }, finish_reason: finish }] })}\r\n\r\n`;
}

/** Responsesのevent行とJSONのtypeを一致させたイベントを作ります。 */
function responseEvent(type: string, fields: Record<string, unknown> = {}): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
}

/** 認証済みの同一オリジン要求として、実際のAPIM中継ハンドラーを呼びます。 */
async function generate(body: ReadableStream<Uint8Array>, options: Partial<ModelGenerationDependencies> & { modelId?: string; operationId?: "chat-completions" | "responses-create"; requestSignal?: AbortSignal; contentType?: string } = {}): Promise<Response> {
  return handleTextGeneration(new Request(`${origin}/api/models/generate`, {
    method: "POST", headers: { origin, "content-type": "application/json" }, signal: options.requestSignal,
    body: JSON.stringify({ message: SAMPLE_TEXT_PROMPT, modelId: options.modelId ?? chatModel.id, ...(options.operationId ? { operationId: options.operationId } : {}), maxOutputTokens: 128, stream: true, apiKeyId: "portal-11111111-1111-4111-8111-111111111111" }),
  }), {
    readSession: async () => ({ tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" }), readPortalOrigin: () => origin,
    readGatewayEnvironment: async () => ({ origin: "https://gateway.example.invalid" }),
    resolveSubscriptionKey: async () => "only-test-key-value",
    readModels: () => textGenerationModels,
    fetch: async (_url, init) => {
      const request = JSON.parse(String(init?.body));
      assert.equal(new Headers(init?.headers).get("Accept"), "text/event-stream");
      assert.equal(request.stream, true);
      assert.equal(request.messages?.[0]?.content ?? request.input, SAMPLE_TEXT_PROMPT);
      return new Response(body, { headers: { "content-type": options.contentType ?? "text/event-stream", "apim-request-id": identifier } });
    },
    ...options,
  });
}

/** 文字の途中も含め、小さなバイト列へ分割した上流ストリームを用意します。 */
function fragment(value: string, width = 7): ReadableStream<Uint8Array> {
  const bytes = encoder.encode(value);
  return new ReadableStream({ start(controller) {
    for (let index = 0; index < bytes.length; index += width) controller.enqueue(bytes.slice(index, index + width));
    controller.close();
  } });
}

test("Chat SSEの日本語・CRLF・分割チャンクと使用量イベントを変えずに中継する", async () => {
  const usage = `data: ${JSON.stringify({ object: "chat.completion.chunk", model: chatModel.id, choices: [], usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 } })}\r\n\r\n`;
  const raw = chat("日本語") + chat("", "stop") + usage + "data: [DONE]\r\n\r\n";
  const result = await generate(fragment(raw));
  assert.equal(result.status, 200);
  assert.match(result.headers.get("content-type") ?? "", /^text\/event-stream/);
  assert.equal(result.headers.get("apim-request-id"), identifier);
  assert.equal(await result.text(), raw);
});

test("Azureの空モデル入力フィルター判定を保持し、後続の生成チャンクを中継する", async () => {
  const metadata = { choices: [], created: 0, id: "", model: "", object: "", prompt_filter_results: [{
    prompt_index: 0, content_filter_results: {
      hate: { filtered: false, severity: "safe" }, self_harm: { filtered: false, severity: "safe" },
      sexual: { filtered: false, severity: "safe" }, violence: { filtered: false, severity: "safe" },
      indirect_attack: { detected: false, filtered: false }, jailbreak: { detected: false, filtered: false },
    },
  }] };
  const annotation = `data: ${JSON.stringify(metadata)}\n\n`;
  const raw = annotation + chat("動作確認") + chat("", "stop") + "data: [DONE]\n\n";
  assert.equal(await (await generate(fragment(raw))).text(), raw);
  const metadataOnly = await (await generate(fragment(annotation + "data: [DONE]\n\n"))).text();
  assert.match(metadataOnly, /event: portal.error/);
  const invalid = { ...metadata, error: { message: "private-upstream-diagnostic" } };
  const rejected = await (await generate(fragment(`data: ${JSON.stringify(invalid)}\n\n`))).text();
  assert.match(rejected, /GenerationFailed/);
  assert.doesNotMatch(rejected, /private-upstream-diagnostic/);
});

test("上流完了を待たず最初のSSEイベントを渡し、購読終了時に上流を中止する", async () => {
  let upstreamController: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  let upstreamSignal: AbortSignal | undefined;
  const upstream = new ReadableStream<Uint8Array>({ start(controller) { upstreamController = controller; }, cancel() { cancelled = true; } });
  const response = await generate(upstream, { fetch: async (_url, init) => {
    upstreamSignal = init?.signal ?? undefined;
    return new Response(upstream, { headers: { "content-type": "text/event-stream" } });
  } });
  upstreamController!.enqueue(encoder.encode(chat("first")));
  const reader = response.body!.getReader();
  const first = await reader.read();
  assert.equal(new TextDecoder().decode(first.value), chat("first"));
  assert.equal(first.done, false);
  await reader.cancel();
  assert.equal(cancelled, true);
  assert.equal(upstreamSignal?.aborted, true);
});

test("Responsesの複数イベントとcompletedをそのまま返し、incompleteも正常な出力として扱う", async () => {
  for (const terminal of ["completed", "incomplete"]) {
    const raw = responseEvent("response.created", { response: { id: "response-test", status: "in_progress" } })
      + responseEvent("response.output_text.delta", { item_id: "message-test", output_index: 0, content_index: 0, delta: "OK", sequence_number: 1 })
      + responseEvent(`response.${terminal}`, { response: { id: "response-test", model: responsesModel.id, status: terminal, output: [{ type: "message", content: [{ type: "output_text", text: "OK" }] }] } });
    const result = await generate(fragment(raw), { modelId: responsesModel.id });
    assert.equal(await result.text(), raw);
  }
});

test("同じモデルIDのResponsesを明示選択してから中継・ブラウザー終端まで一貫して処理する", async () => {
  const selected = { ...responsesModel, id: chatModel.id };
  const raw = responseEvent("response.created", { response: { status: "in_progress" } })
    + responseEvent("response.output_text.delta", { delta: "選択したResponses API" })
    + responseEvent("response.completed", { response: { model: selected.id, status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: "選択したResponses API" }] }], usage: { input_tokens: 10, output_tokens: 4, total_tokens: 14 } } });
  let calls = 0;
  const response = await generate(fragment(raw, 5), { modelId: selected.id, operationId: "responses-create", readModels: () => [chatModel, selected], fetch: async (url, init) => {
    calls++; assert.equal(String(url), "https://gateway.example.invalid/models/openai/v1/responses");
    const body = JSON.parse(String(init?.body)); assert.equal(body.store, false); assert.equal(body.input, SAMPLE_TEXT_PROMPT); assert.equal(body.max_output_tokens, 128); assert.equal("messages" in body, false);
    assert.equal(new Headers(init?.headers).get("X-Egakumi-Profile"), selected.profileId);
    return new Response(fragment(raw, 5), { headers: { "content-type": "text/event-stream" } });
  } });
  assert.equal(response.status, 200);
  const result = await readModelResponse(response, selected);
  assert.equal(result.text, "選択したResponses API"); assert.equal(result.finishReason, "completed"); assert.equal(calls, 1);
  assert.deepEqual(result.usage, { inputTokens: 10, outputTokens: 4, totalTokens: 14 });
});

test("Responsesでevent名と本文typeが異なる場合やChat終端を返す場合は成功扱いしない", async () => {
  const mismatched = 'event: response.completed\ndata: {"type":"response.output_text.delta","delta":"誤った終端"}\n\n';
  for (const raw of [mismatched, "data: [DONE]\n\n"]) {
    const response = await generate(fragment(raw), { modelId: responsesModel.id });
    const output = await response.text(); assert.match(output, /event: portal.error/); assert.match(output, /GenerationFailed/); assert.doesNotMatch(output, /誤った終端/);
  }
});

test("Responses failedや任意のエラー本文を中継せず固定コードを返す", async () => {
  const result = await generate(fragment(responseEvent("response.failed", { response: { error: { message: "private-upstream-diagnostic" } } })), { modelId: responsesModel.id });
  const text = await result.text();
  assert.match(text, /event: portal.error/);
  assert.match(text, /GenerationFailed/);
  assert.doesNotMatch(text, /private-upstream-diagnostic/);
});

test("終端前の切断・不正JSON・偽のDONEは成功扱いにしない", async () => {
  for (const raw of [chat("partial"), "data: [DONE]\n\n", "data: not-json\n\n", 'data: {"error":{"message":"private-details"}}\n\n']) {
    const result = await generate(fragment(raw));
    const text = await result.text();
    assert.match(text, /event: portal.error/);
    assert.match(text, /GenerationFailed/);
    assert.doesNotMatch(text, /private-details/);
  }
});

test("stream要求にJSONを返す上流と未認証要求は中継しない", async () => {
  assert.equal((await generate(fragment("{}"), { contentType: "application/json" })).status, 502);
  assert.equal((await generate(fragment(chat("not sent")), { readSession: async () => null })).status, 401);
});

test("ストリーム中のタイムアウトと要求の中止は上流の読み取りも終える", async () => {
  let timeoutCancelled = false;
  const timeoutResponse = await generate(new ReadableStream({ cancel() { timeoutCancelled = true; } }), { timeoutMs: 10 });
  const keepAlive = setTimeout(() => undefined, 1000);
  try {
    assert.match(await timeoutResponse.text(), /GatewayTimeout/);
    assert.equal(timeoutCancelled, true);
  } finally { clearTimeout(keepAlive); }
  let requestCancelled = false;
  const abort = new AbortController();
  const response = await generate(new ReadableStream({ cancel() { requestCancelled = true; } }), { requestSignal: abort.signal });
  abort.abort();
  assert.match(await response.text(), /RequestCancelled/);
  assert.equal(requestCancelled, true);
});

test("一イベントのサイズ上限を越えたSSEを破棄する", async () => {
  const result = await generate(fragment(chat("a".repeat(MODEL_MAX_EVENT_BYTES)), 1_048_576));
  const text = await result.text();
  assert.match(text, /GenerationFailed/);
  assert.ok(text.length < 1000);
});

test("旧イベント上限を超える正常ストリームを中継し、ブラウザーが終端まで読み取る", async () => {
  const count = 20_000;
  const raw = chat("あ".repeat(400_000)) + Array.from({ length: count }, () => chat("a")).join("") + chat("", "stop") + "data: [DONE]\n\n";
  const response = await generate(fragment(raw, 131_072));
  assert.equal(response.status, 200);
  const result = await readModelResponse(response, chatModel);
  assert.equal(result.text, "あ".repeat(400_000) + "a".repeat(count));
  assert.equal((result.rawResponse.events as unknown[]).length, count + 3);
  assert.equal(result.finishReason, "stop");
});
