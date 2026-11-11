import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { handleDocumentAnalysis, type DocumentAnalysisDependencies } from "../lib/document-analysis-gateway.ts";
import { MAX_DOCUMENT_BYTES, MAX_DOCUMENT_RESULT_BYTES } from "../lib/document-analysis-contracts.ts";

const actor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const apiKeyId = "portal-33333333-3333-4333-8333-333333333333";
const secret = "document-private-key-must-not-leak";
const origin = "https://portal.example.invalid";
const gateway = "https://gateway.example.invalid";
const pdf = new TextEncoder().encode("%PDF-1.7\nTest transport fixture");
const native = { analyzeResult: { apiVersion: "2024-11-30", modelId: "prebuilt-read", content: "Document text", pages: [{ pageNumber: 1 }] }, requestId: "native-id" };

/** multipartはPortal専用の形で送り、公開APIへの転送は試験側で確認します。 */
function request(options: { model?: string; type?: string; bytes?: Uint8Array; fields?: Record<string, unknown>; headers?: Record<string, string>; duplicate?: boolean; signal?: AbortSignal; search?: string } = {}): Request {
  const form = new FormData();
  form.append("input", JSON.stringify({ model: options.model ?? "prebuilt-read", apiKeyId, ...options.fields }));
  form.append("file", new Blob([new Uint8Array(options.bytes ?? pdf).buffer], { type: options.type ?? "application/pdf" }), "input.pdf");
  if (options.duplicate) form.append("file", new Blob([pdf]), "second.pdf");
  return new Request(`${origin}/api/tools/documents${options.search ?? ""}`, { method: "POST", headers: { origin, ...options.headers }, body: form, signal: options.signal });
}

/** 実際の入力検証・認可境界を通し、Azure通信だけを差し替えます。 */
function dependencies(overrides: Partial<DocumentAnalysisDependencies> = {}): DocumentAnalysisDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => origin, readGatewayEnvironment: async () => ({ origin: gateway }), resolveSubscriptionKey: async () => secret,
    fetch: async () => Response.json(native, { headers: { "apim-request-id": "document-request-1" } }), ...overrides };
}

test("本人と選択キーの現権限を確認し、raw fileだけを固定Gatewayへ1回送信する", async () => {
  let calls = 0; const grants: unknown[] = [];
  const response = await handleDocumentAnalysis(request(), dependencies({ resolveSubscriptionKey: async (...args) => { grants.push(args); return secret; }, fetch: async (url, init) => {
    calls++; assert.equal(String(url), `${gateway}/tools/documents/v1/analyze?model=prebuilt-read`);
    assert.equal(init?.method, "POST"); assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    assert.equal(new Headers(init?.headers).get("api-key"), secret); assert.equal(new Headers(init?.headers).get("Content-Type"), "application/pdf");
    assert.deepEqual(new Uint8Array(init?.body as ArrayBuffer), pdf);
    return Response.json(native, { headers: { "apim-request-id": "document-request-1", "Operation-Location": "https://private.invalid/job" } });
  } }));
  assert.equal(response.status, 200); assert.equal(calls, 1); assert.deepEqual(grants, [[actor, apiKeyId, "document-analysis-v1"]]);
  assert.equal(response.headers.get("operation-location"), null); assert.equal(response.headers.get("cache-control"), "no-store");
  const output = await response.json(); assert.equal(output.rawResponse.analyzeResult.content, "Document text"); assert.equal("content" in output, false); assert.deepEqual(output.rawResponse, native); assert.equal(output.requestId, "document-request-1");
  assert.equal(JSON.stringify(output).includes(secret), false);
});

test("クロスサイト・未認証・任意URL・追加パラメーターは上流通信を行わない", async () => {
  let calls = 0; const base = dependencies({ fetch: async () => { calls++; throw new Error("No call"); } });
  assert.equal((await handleDocumentAnalysis(request({ headers: { origin: "https://untrusted.invalid" } }), base)).status, 403);
  assert.equal((await handleDocumentAnalysis(request({ headers: { "sec-fetch-site": "cross-site" } }), base)).status, 403);
  assert.equal((await handleDocumentAnalysis(request(), { ...base, readSession: async () => null })).status, 401);
  assert.equal((await handleDocumentAnalysis(request(), { ...base, readSession: async () => { throw new Error(secret); } })).status, 503);
  for (const fields of [{ url: "https://untrusted.invalid" }, { pages: "1" }, { apiKey: secret }]) assert.equal((await handleDocumentAnalysis(request({ fields }), base)).status, 400);
  assert.equal((await handleDocumentAnalysis(request({ search: "?model=custom" }), base)).status, 400);
  assert.equal(calls, 0);
});

test("ファイル実体・形式・重複・サイズ・モデルの不正はキー取得前に拒否する", async () => {
  let grants = 0; const base = dependencies({ resolveSubscriptionKey: async () => { grants++; return secret; } });
  const attempts = [request({ type: "image/svg+xml" }), request({ bytes: new Uint8Array([0x42, 0x4d]) }), request({ duplicate: true }), request({ model: "custom" })];
  for (const attempt of attempts) assert.ok([400, 413].includes((await handleDocumentAnalysis(attempt, base)).status));
  assert.equal(grants, 0);
  const maximum = new Uint8Array(2 * 1_048_576); maximum.set(pdf);
  assert.equal((await handleDocumentAnalysis(request({ bytes: maximum }), base)).status, 200);
});

test("削除・無効化・グループ脱退によるキー拒否を固定失敗として返す", async () => {
  let calls = 0;
  const result = await handleDocumentAnalysis(request(), dependencies({ resolveSubscriptionKey: async () => { throw new ApiKeyError("NotFound", 404); }, fetch: async () => { calls++; throw new Error("No call"); } }));
  assert.equal(result.status, 403); assert.deepEqual(await result.json(), { error: { code: "ApiKeyUnavailable" } }); assert.equal(calls, 0);
});

test("上流エラー・HTML・秘密反射・モデル不一致は生本文や秘密を表示しない", async () => {
  const replies = [new Response(`<html>${secret}</html>`, { headers: { "Content-Type": "text/html" } }), Response.json({ ...native, analyzeResult: { ...native.analyzeResult, content: secret } }), Response.json({ ...native, analyzeResult: { ...native.analyzeResult, modelId: "prebuilt-layout" } }), Response.json({ error: { message: secret } }, { status: 500 })];
  for (const upstream of replies) {
    const result = await handleDocumentAnalysis(request(), dependencies({ fetch: async () => upstream }));
    assert.equal(result.status, 502); assert.equal((await result.text()).includes(secret), false);
  }
  const rate = await handleDocumentAnalysis(request(), dependencies({ fetch: async () => new Response(secret, { status: 429, headers: { "retry-after": "30" } }) }));
  assert.equal(rate.status, 429); assert.deepEqual(await rate.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 30 });
});

test("過大な解析結果は読み続けず拒否し、JSONエスケープされた秘密も返さない", async () => {
  const huge = await handleDocumentAnalysis(request(), dependencies({ fetch: async () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": String(MAX_DOCUMENT_RESULT_BYTES + 1) } }) }));
  assert.equal(huge.status, 502); assert.deepEqual(await huge.json(), { error: { code: "DocumentResultTooLarge" } });
  const escaped = JSON.stringify({ ...native, analyzeResult: { ...native.analyzeResult, content: secret } }).replace(secret, [...secret].map((char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`).join(""));
  const result = await handleDocumentAnalysis(request(), dependencies({ fetch: async () => new Response(escaped, { headers: { "Content-Type": "application/json" } }) }));
  assert.equal(result.status, 502); assert.equal((await result.text()).includes(secret), false);
});

test("利用者の停止とGatewayの解析タイムアウトを区別する", async () => {
  const controller = new AbortController(); controller.abort();
  assert.equal((await handleDocumentAnalysis(request({ signal: controller.signal }), dependencies())).status, 499);
  const timedOut = await handleDocumentAnalysis(request(), dependencies({ timeoutMs: 1, fetch: async (_url, init) => new Promise<Response>((_resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timeout fallback")), 100);
    init?.signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
  }) }));
  assert.equal(timedOut.status, 504); assert.deepEqual(await timedOut.json(), { error: { code: "DocumentAnalysisTimeout" } });
  const gatewayTimeout = await handleDocumentAnalysis(request(), dependencies({ fetch: async () => new Response(secret, { status: 504 }) }));
  assert.equal(gatewayTimeout.status, 504); assert.deepEqual(await gatewayTimeout.json(), { error: { code: "DocumentAnalysisTimeout" } });
});

test("Gatewayの文書固有エラーを固定コードへ対応させ、詳細本文を返さない", async () => {
  const cases = [
    ["DocumentInvalidInput", "InvalidRequest", 400], ["DocumentInputTooLarge", "DocumentTooLarge", 413],
    ["DocumentUnsupportedMediaType", "UnsupportedDocument", 415], ["DocumentRejected", "DocumentRejected", 422],
    ["DocumentAnalysisThrottled", "RateLimitExceeded", 429], ["DocumentAnalysisTimeout", "DocumentAnalysisTimeout", 504],
    ["DocumentAnalysisUnavailable", "DocumentAnalysisUnavailable", 503],
  ] as const;
  for (const [upstreamCode, code, status] of cases) {
    const result = await handleDocumentAnalysis(request(), dependencies({ fetch: async () => Response.json({ error: { code: upstreamCode, message: secret } }, { status }) }));
    assert.equal(result.status, status); assert.deepEqual(await result.json(), { error: { code }, ...(code === "RateLimitExceeded" ? { retryAfterSeconds: 2 } : {}) });
  }
});

test("抽出本文へ引用符付きキーがJSONエスケープされても公開しない", async () => {
  const credential = 'synthetic-document-"key"-\\-value';
  const raw = JSON.stringify({ ...native, analyzeResult: { ...native.analyzeResult, content: credential } }); assert.equal(raw.includes(credential), false);
  const response = await handleDocumentAnalysis(request(), dependencies({ resolveSubscriptionKey: async () => credential, fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) }));
  assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "DocumentAnalysisFailed" } });
});
