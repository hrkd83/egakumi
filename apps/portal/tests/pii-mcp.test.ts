import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { PII_API, PII_SAMPLE } from "../lib/pii-contract.ts";
import { PII_MCP, getPiiMcpEndpoint } from "../lib/pii-mcp-contract.ts";
import { handlePiiMcp } from "../lib/pii-mcp-gateway.ts";
import type { PiiGatewayDependencies } from "../lib/pii-gateway.ts";
import { readPiiMcpMessage } from "../lib/pii-mcp-transport.ts";
import { buildPiiMcpCall, getPiiMcpIntegrationExample } from "../lib/pii-mcp-integration.ts";

const key = "test-only-pii-mcp-subscription-key", session = "session-hidden-from-browser-12345";
const input = { ...PII_SAMPLE, apiKeyId: "portal-11111111-1111-4111-8111-111111111111" };
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
const result = { redactedText: "サンプル担当者は****です。", language: "ja", detectedCount: 1, modelVersion: PII_API.modelVersion, warningCodes: [], requestId: "f3bdd15e-f64e-4f4f-8f30-04ecb45219e4" };
const tool = { name: PII_MCP.toolName, inputSchema: { type: "object", properties: { text: { type: "string" }, language: { type: "string" } }, required: ["text", "language"], additionalProperties: false } };

/** 認証済みPortalからの要求を作り、任意の上流URLは受け付けません。 */
function request(value: unknown = input, headers: Record<string, string> = {}, signal?: AbortSignal) {
  return new Request("https://portal.example.com/api/tools/pii-mcp", { method: "POST", headers: { origin: "https://portal.example.com", "content-type": "application/json", ...headers }, body: JSON.stringify(value), signal });
}
/** 実際のMCP初期化順序とメッセージを記録する、通信境界の代替です。 */
function dependencies(overrides: Partial<PiiGatewayDependencies> = {}, sessionId: string | null = session) {
  const messages: Record<string, unknown>[] = [];
  const fetcher: typeof fetch = async (_url, options) => {
    if (options?.method === "DELETE") { messages.push({ method: "DELETE" }); return new Response(null, { status: 204 }); }
    const message = JSON.parse(String(options?.body)); messages.push(message);
    const reply = (value: unknown) => Response.json({ jsonrpc: "2.0", id: message.id, result: value });
    if (message.method === "initialize") return Response.json({ jsonrpc: "2.0", id: message.id, result: { protocolVersion: "2025-03-26", capabilities: { tools: { listChanged: true } } } }, { headers: sessionId ? { "Mcp-Session-Id": sessionId } : {} });
    if (message.method === "notifications/initialized" || message.method === "notifications/cancelled") return new Response(null, { status: 202 });
    if (message.method === "tools/list") return reply({ tools: [tool] });
    if (message.method === "tools/call") return reply({ content: [{ type: "text", text: JSON.stringify(result) }] });
    throw new Error("Unexpected MCP request");
  };
  return { messages, fallback: fetcher, deps: { readSession: async () => actor, readPortalOrigin: () => "https://portal.example.com", readGatewayEnvironment: async () => ({ origin: "https://gateway.example.com" }), resolveSubscriptionKey: async () => key, fetch: fetcher, ...overrides } satisfies PiiGatewayDependencies };
}

test("MCP接続先とツール名は正本から生成される値に一致する", () => {
  const api = JSON.parse(readFileSync(new URL("../../gateway/apim/apis/pii-v1.json", import.meta.url), "utf8"));
  assert.equal(PII_MCP.apiId, api.mcp.name); assert.equal(PII_MCP.path, `/${api.mcp.path}/mcp`); assert.equal(PII_MCP.toolName, api.mcp.operationNames[0]);
  assert.equal(getPiiMcpEndpoint("https://gateway.example.com"), "https://gateway.example.com/tools/pii-v1-mcp/mcp");
});

test("初期化→通知→一覧→ツールの順で実行しMCPセッションをブラウザーへ漏らさない", async () => {
  const setup = dependencies(); let calls = 0;
  const response = await handlePiiMcp(request(), { ...setup.deps, resolveSubscriptionKey: async (who, id, apiId) => { assert.deepEqual(who, actor); assert.equal(id, input.apiKeyId); assert.equal(apiId, "pii-v1-mcp"); return key; }, fetch: async (url, options) => {
    calls++; assert.equal(String(url), getPiiMcpEndpoint("https://gateway.example.com")); assert.equal(options?.redirect, "error");
    if (options?.method === "POST") {
      const payload = JSON.parse(String(options.body));
      if (payload.id !== undefined) assert.equal(Number.isSafeInteger(payload.id), true, "APIMの生成MCPへは数値IDで送信する");
    }
    const headers = new Headers(options?.headers); assert.equal(headers.get("api-key"), key);
    if (calls > 1) { assert.equal(headers.get("Mcp-Session-Id"), session); assert.equal(headers.get("MCP-Protocol-Version"), "2025-03-26"); }
    return setup.fallback(url, options);
  } });
  assert.equal(response.status, 200); assert.equal(response.headers.get("Mcp-Session-Id"), null);
  const data = await response.json(); assert.deepEqual(data.result, result); assert.equal(data.mcp.protocolVersion, "2025-03-26");
  assert.deepEqual(setup.messages.map((item) => item.method), ["initialize", "notifications/initialized", "tools/list", "tools/call", "DELETE"]);
  const raw = JSON.stringify(data); assert.equal(raw.includes(key), false); assert.equal(raw.includes(session), false);
});

test("ステートレスサーバーにも接続しstructuredContentだけの結果を解釈する", async () => {
  const setup = dependencies({}, null);
  const response = await handlePiiMcp(request(), { ...setup.deps, fetch: async (url, options) => {
    const message = JSON.parse(String(options?.body));
    if (message.method === "tools/call") return Response.json({ jsonrpc: "2.0", id: message.id, result: { structuredContent: result, isError: false } });
    return setup.fallback(url, options);
  } });
  assert.equal(response.status, 200); assert.deepEqual((await response.json()).result, result); assert.equal(setup.messages.some((message) => message.method === "DELETE"), false);
});

test("JSON-RPCのID不一致・エラー・通知だけのJSONを成功にしない", async () => {
  for (const value of [{ jsonrpc: "2.0", id: "other", result }, { jsonrpc: "2.0", id: "expected", error: { message: key } }, { jsonrpc: "2.0", method: "notifications/message", params: {} }]) await assert.rejects(readPiiMcpMessage(Response.json(value), "expected", new AbortController().signal));
});

test("SSE通知と複数data行・分割UTF8を扱い応答到着後にストリームを止める", async () => {
  let cancelled = false;
  const raw = ': keepalive\r\nevent: message\r\ndata: {"jsonrpc":"2.0","method":"notifications/message"}\r\n\r\ndata: {"jsonrpc":"2.0","id":"expected",\r\ndata: "result":{"value":"日本語"}}\r\n\r\n';
  const bytes = new TextEncoder().encode(raw);
  const stream = new ReadableStream<Uint8Array>({ start(controller) { for (let offset = 0; offset < bytes.length; offset += 3) controller.enqueue(bytes.slice(offset, offset + 3)); }, cancel() { cancelled = true; } });
  assert.deepEqual(await readPiiMcpMessage(new Response(stream, { headers: { "Content-Type": "text/event-stream" } }), "expected", new AbortController().signal), { value: "日本語" }); assert.equal(cancelled, true);
});

test("未完了SSE・不正media・過大応答を拒否する", async () => {
  assert.deepEqual(await readPiiMcpMessage(new Response('data: {"jsonrpc":"2.0","id":3,"result":{"ok":true}}\r\r', { headers: { "content-type": "text/event-stream" } }), 3, new AbortController().signal), { ok: true });
  await assert.rejects(readPiiMcpMessage(new Response('data: {"jsonrpc":"2.0","id":"expected","result":{}}', { headers: { "content-type": "text/event-stream" } }), "expected", new AbortController().signal));
  await assert.rejects(readPiiMcpMessage(new Response("hello", { headers: { "content-type": "text/plain" } }), "expected", new AbortController().signal));
  await assert.rejects(readPiiMcpMessage(new Response("x".repeat(262145), { headers: { "content-type": "application/json" } }), "expected", new AbortController().signal));
});

test("他オリジン・未認証・不正入力・利用できない購読でMCPへ接続しない", async () => {
  let calls = 0; const setup = dependencies({ fetch: async () => { calls++; throw new Error("Must not send"); } });
  assert.equal((await handlePiiMcp(request(input, { origin: "https://attacker.example" }), setup.deps)).status, 403);
  assert.equal((await handlePiiMcp(request(), { ...setup.deps, readSession: async () => null })).status, 401);
  assert.equal((await handlePiiMcp(request({ ...input, url: "https://attacker.example" }), setup.deps)).status, 400);
  assert.equal((await handlePiiMcp(request(), { ...setup.deps, resolveSubscriptionKey: async () => { throw new ApiKeyError("AccessDenied", 403); } })).status, 403);
  assert.equal(calls, 0);
});

test("未対応プロトコル・不正ツール・秘密反射・isErrorを成功にしない", async () => {
  for (const variant of ["protocol", "tool", "secret", "tool-error", "wrapped-error"]) {
    const setup = dependencies({}, null);
    const response = await handlePiiMcp(request(), { ...setup.deps, fetch: async (url, options) => {
      const message = JSON.parse(String(options?.body));
      if (variant === "protocol" && message.method === "initialize") return Response.json({ jsonrpc: "2.0", id: message.id, result: { protocolVersion: "2099-01-01", capabilities: { tools: {} } } });
      if (variant === "tool" && message.method === "tools/list") return Response.json({ jsonrpc: "2.0", id: message.id, result: { tools: [{ ...tool, name: "other" }] } });
      if (message.method === "tools/call") return Response.json({ jsonrpc: "2.0", id: message.id, result: { isError: variant === "tool-error", content: [{ type: "text", text: JSON.stringify(variant === "wrapped-error" ? { error: { code: "PiiUpstreamFailure" }, requestId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" } : { ...result, redactedText: variant === "secret" ? key : result.redactedText }) }] } });
      return setup.fallback(url, options);
    } });
    assert.equal(response.status, 502); assert.equal((await response.text()).includes(key), false);
  }
});

test("tools/callのタイムアウトは再送せず取消通知を出して終了する", async () => {
  let calls = 0; const setup = dependencies(); const keepAlive = setTimeout(() => undefined, 1000);
  try {
    const response = await handlePiiMcp(request(), { ...setup.deps, timeoutMs: 20, fetch: async (url, options) => {
      if (options?.method === "POST" && JSON.parse(String(options.body)).method === "tools/call") {
        calls++; return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(': waiting\n\n')); } }), { headers: { "content-type": "text/event-stream" } });
      }
      return setup.fallback(url, options);
    } });
    assert.equal(response.status, 504); assert.equal(calls, 1); assert.equal(setup.messages.some((message) => message.method === "notifications/cancelled"), true); assert.equal(setup.messages.at(-1)?.method, "DELETE");
  } finally { clearTimeout(keepAlive); }
});

test("引用符付きの購読キーと内部sessionも、復号済みPII結果へ反射させない", async () => {
  const credential = 'synthetic-mcp-"key"-\\-value', internalSession = 'synthetic-mcp-"session"-\\-value';
  for (const reflected of [credential, internalSession]) {
    const setup = dependencies({ resolveSubscriptionKey: async () => credential }, internalSession);
    let calls = 0;
    const response = await handlePiiMcp(request(), { ...setup.deps, fetch: async (url, options) => {
      if (options?.method === "POST") {
        const message = JSON.parse(String(options.body));
        if (message.method === "tools/call") {
          calls++;
          return Response.json({ jsonrpc: "2.0", id: message.id, result: { content: [{ type: "text", text: JSON.stringify({ ...result, redactedText: reflected }) }] } });
        }
      }
      return setup.fallback(url, options);
    } });
    assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
    assert.equal(calls, 1); assert.equal(setup.messages.at(-1)?.method, "DELETE");
  }
});

test("MCP例は正しい呼び出し封筒と3種のクライアントを提供する", () => {
  assert.deepEqual(buildPiiMcpCall(PII_SAMPLE).params.arguments, PII_SAMPLE);
  assert.equal(Number.isInteger(buildPiiMcpCall(PII_SAMPLE).id), true);
  for (const language of ["typescript", "python", "curl"] as const) {
    const source = getPiiMcpIntegrationExample(language, PII_SAMPLE, "https://gateway.example.com");
    assert.match(source, /GATEWAY_API_KEY/); assert.match(source, /pii_redact_text/); assert.match(source, /tools\/pii-v1-mcp\/mcp/);
  }
});
