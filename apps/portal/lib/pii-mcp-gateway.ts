import { MAX_BUFFERED_BODY_BYTES, modelDeadline } from "./model-runtime-limits.ts";
import "server-only";
import { containsModelCredential } from "./model-credential.ts";
import { z } from "zod";
import { ApiKeyError } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getModelGatewayEnvironment } from "./model-gateway.ts";
import { readEmbeddingBody } from "./embedding-response.ts";
import type { PiiGatewayDependencies } from "./pii-gateway.ts";
import { buildPiiRequest, piiExecutionSchema, piiResponseSchema } from "./pii-contract.ts";
import { PII_MCP, PII_MCP_PROTOCOLS, getPiiMcpEndpoint, piiMcpResultSchema } from "./pii-mcp-contract.ts";
import { PiiMcpTransportError, readPiiMcpMessage, type PiiMcpMessage } from "./pii-mcp-transport.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const initializeSchema = z.object({ protocolVersion: z.enum(PII_MCP_PROTOCOLS), capabilities: z.object({ tools: z.object({}).passthrough() }).passthrough() });
const toolsSchema = z.object({ tools: z.array(z.object({ name: z.string().max(128), inputSchema: z.record(z.string(), z.unknown()) })).max(64), nextCursor: z.string().max(2048).optional() });
const argumentSchema = z.object({ type: z.literal("object"), properties: z.object({ text: z.object({ type: z.literal("string") }), language: z.object({ type: z.literal("string") }) }), required: z.array(z.string()) });
const toolResponseSchema = z.object({ content: z.array(z.object({ type: z.literal("text"), text: z.string().max(MAX_BUFFERED_BODY_BYTES) }).strip()).default([]), structuredContent: z.unknown().optional(), isError: z.boolean().optional() }).strip();

/** エラーの自由文・内部セッション・秘密のヘッダーを返さない固定応答です。 */
function failure(code: ModelGatewayErrorCode, status: number, details: Omit<TextGenerationFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details }, { status, headers });
}

/** 一回の試行の中で初期化・ツール確認・PII実行・切断を行い、MCPセッションを外へ出しません。 */
export async function handlePiiMcp(request: Request, dependencies: PiiGatewayDependencies): Promise<Response> {
  let portalOrigin: string;
  try { portalOrigin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== portalOrigin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  let actor;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  let input: ReturnType<typeof piiExecutionSchema.parse>;
  try { input = piiExecutionSchema.parse(JSON.parse(await readEmbeddingBody(request.body, 1_001_024, request.signal))); buildPiiRequest(input); } catch { return failure(request.signal.aborted ? "RequestCancelled" : "InvalidRequest", request.signal.aborted ? 499 : 400); }
  let endpoint: string;
  try { endpoint = getPiiMcpEndpoint((await (dependencies.readGatewayEnvironment ?? getModelGatewayEnvironment)()).origin); } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, PII_MCP.apiId);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = modelDeadline(dependencies.timeoutMs), signal = AbortSignal.any([request.signal, timeout]), fetcher = dependencies.fetch ?? globalThis.fetch;
  let session: string | null = null, protocol: typeof PII_MCP_PROTOCOLS[number] | null = null, currentId: number | null = null, requestId: string | undefined;
  const started = performance.now();
  /** セッションはサーバー内部だけで継承し、送信先を変えません。 */
  function requestHeaders() { return { "Content-Type": "application/json", Accept: "application/json, text/event-stream", "api-key": key, ...(session ? { "Mcp-Session-Id": session } : {}), ...(protocol ? { "MCP-Protocol-Version": protocol } : {}) }; }
  /** JSON-RPC要求と応答IDを対応付け、HTTPエラーの本文は破棄します。 */
  async function send(message: PiiMcpMessage): Promise<unknown> {
    currentId = message.id ?? null;
    const response = await fetcher(endpoint, { method: "POST", redirect: "error", cache: "no-store", signal, headers: requestHeaders(), body: JSON.stringify(message) });
    const id = response.headers.get("apim-request-id"); if (id && /^[a-zA-Z0-9_-]{1,128}$/.test(id)) requestId = id;
    if (!response.ok) {
      await response.body?.cancel(); const retry = Number(response.headers.get("retry-after"));
      throw new PiiMcpTransportError(response.status, Number.isInteger(retry) && retry > 0 && retry <= 3600 ? retry : undefined);
    }
    if (message.method === "initialize") {
      const received = response.headers.get("Mcp-Session-Id");
      if (received && !/^[\x21-\x7e]{1,2048}$/.test(received)) { await response.body?.cancel(); throw new PiiMcpTransportError(); }
      session = received;
    }
    if (message.id === undefined) {
      await response.body?.cancel();
      if (response.status !== 202 && response.status !== 204) throw new PiiMcpTransportError();
      return undefined;
    }
    const result = await readPiiMcpMessage(response, message.id, signal); currentId = null; return result;
  }
  try {
    // APIMの生成MCPは文字列IDを400にするため、接続内の要求IDを正整数で統一します。
    const initialized = initializeSchema.parse(await send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: PII_MCP.protocolVersion, capabilities: {}, clientInfo: { name: "egakumi-portal-pii", version: "1.0.0" } } }));
    protocol = initialized.protocolVersion;
    await send({ jsonrpc: "2.0", method: "notifications/initialized" });
    let tool: z.infer<typeof toolsSchema>["tools"][number] | undefined, cursor: string | undefined;
    for (let page = 0; page < 4; page++) {
      const result = toolsSchema.parse(await send({ jsonrpc: "2.0", id: 2 + page, method: "tools/list", ...(cursor ? { params: { cursor } } : {}) }));
      tool = result.tools.find((entry) => entry.name === PII_MCP.toolName); if (tool) break;
      cursor = result.nextCursor; if (!cursor) break;
    }
    if (!tool) throw new PiiMcpTransportError();
    // 意図しない変更で別の引数や追加権限を要求された場合は、推測して呼び出しません。
    const args = argumentSchema.parse(tool.inputSchema);
    if (args.required.length !== 2 || !args.required.includes("text") || !args.required.includes("language")) throw new PiiMcpTransportError();
    const rawTool = toolResponseSchema.parse(await send({ jsonrpc: "2.0", id: 10, method: "tools/call", params: { name: PII_MCP.toolName, arguments: buildPiiRequest(input) } }));
    if (rawTool.isError) throw new PiiMcpTransportError();
    const parsedStructured = rawTool.structuredContent === undefined ? undefined : piiResponseSchema.parse(rawTool.structuredContent);
    const textResults = rawTool.content.map((entry) => piiResponseSchema.parse(JSON.parse(entry.text)));
    const result = parsedStructured ?? textResults[0];
    if (!result || textResults.some((entry) => JSON.stringify(entry) !== JSON.stringify(result))) throw new PiiMcpTransportError();
    if (result.language !== input.language) throw new PiiMcpTransportError();
    const safeResponse = { jsonrpc: "2.0" as const, id: 10, result: { content: rawTool.content.map((entry) => ({ type: "text" as const, text: entry.text })), ...(parsedStructured ? { structuredContent: parsedStructured } : {}), ...(rawTool.isError === false ? { isError: false as const } : {}) } };
    const output = piiMcpResultSchema.parse({ result, durationMs: Math.round(performance.now() - started), mcp: { protocolVersion: protocol, toolName: PII_MCP.toolName, response: safeResponse } });
    const serialized = JSON.stringify(output);
    if (containsModelCredential(output, key, serialized) || session && containsModelCredential(output, session, serialized)) throw new PiiMcpTransportError();
    return new Response(serialized, { headers: { ...headers, "Content-Type": "application/json" } });
  } catch (error) {
    const details = requestId ? { requestId } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("GatewayTimeout", 504, details);
    if (error instanceof PiiMcpTransportError && error.status === 429) return failure("RateLimitExceeded", 429, { ...details, ...(error.retryAfterSeconds ? { retryAfterSeconds: error.retryAfterSeconds } : {}) });
    return failure(error instanceof PiiMcpTransportError && [401, 403].includes(error.status) ? "GatewayUnauthorized" : "GenerationFailed", 502, details);
  } finally {
    // 切断を中止と同一視せず、可能なら取消通知とセッション破棄を別要求で届けます。
    const cleanupSignal = AbortSignal.timeout(2000);
    try {
      if (currentId && protocol) { const cancelled = await fetcher(endpoint, { method: "POST", redirect: "error", cache: "no-store", signal: cleanupSignal, headers: requestHeaders(), body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/cancelled", params: { requestId: currentId } }) }); await cancelled.body?.cancel(); }
      if (session) { const closed = await fetcher(endpoint, { method: "DELETE", redirect: "error", cache: "no-store", signal: cleanupSignal, headers: requestHeaders() }); await closed.body?.cancel(); }
    } catch { /* 終了要求の失敗は秘密も結果も書き換えず、今回の一時状態を破棄します。 */ }
  }
}
