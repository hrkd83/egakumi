import assert from "node:assert/strict";
import { test } from "node:test";
import { handleDocumentAnalysis, handleDocumentAnalysisStatus, type DocumentAnalysisDependencies } from "../lib/document-analysis-gateway.ts";
import { sealDocumentOperation, openDocumentOperation } from "../lib/document-operation-handle.ts";
import { waitForDocumentAnalysis } from "../lib/document-analysis-client.ts";

const actor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const apiKeyId = "portal-33333333-3333-4333-8333-333333333333";
const secret = "synthetic-document-operation-envelope-key";
const subscription = "synthetic-subscription-execution-key";
const portal = "https://portal.example.invalid";
const gateway = "https://gateway.example.invalid";
const state = { operationId: "44444444-4444-4444-8444-444444444444", model: "prebuilt-read" as const, statusPath: "/tools/documents/v1/operations/44444444-4444-4444-8444-444444444444", ticket: "synthetic-ticket.synthetic-signature", expiresAt: new Date(Date.now() + 86_400_000).toISOString().replace("Z", "0000Z"), startedAt: Date.now() };
const native = { analyzeResult: { apiVersion: "2024-11-30", modelId: state.model, content: "Complete document", pages: [{ pageNumber: 1 }] } };

/** 同じ利用者と選択購読だけを許す、実通信を置き換える境界です。 */
function dependencies(overrides: Partial<DocumentAnalysisDependencies> = {}): DocumentAnalysisDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => portal, readGatewayEnvironment: async () => ({ origin: gateway }), readHandleSecret: () => secret, resolveSubscriptionKey: async () => subscription, ...overrides };
}

/** ブラウザーは元ファイルを受付時にだけ送信します。 */
function upload(): Request {
  const form = new FormData(); form.append("input", JSON.stringify({ model: state.model, apiKeyId }));
  const file = new Uint8Array(2 * 1_048_576); file.set(new TextEncoder().encode("%PDF-1.7"));
  form.append("file", new Blob([file], { type: "application/pdf" }), "input.pdf");
  return new Request(`${portal}/api/tools/documents`, { method: "POST", headers: { origin: portal }, body: form });
}

/** 状態確認はURLを入力させず、封印した参照を同一オリジンへPOSTします。 */
function poll(handle: string, id = apiKeyId): Request {
  return new Request(`${portal}/api/tools/documents/status`, { method: "POST", headers: { origin: portal, "Content-Type": "application/json" }, body: JSON.stringify({ apiKeyId: id, handle }) });
}

/** raw fileの転送でも、メタデータ以外をURLへ含めません。 */
function rawUpload(body: ReadableStream<Uint8Array>, headers: Record<string, string> = {}): Request {
  const init: RequestInit & { duplex: "half" } = { method: "POST", duplex: "half", headers: { origin: portal, "Content-Type": "application/pdf", ...headers }, body };
  return new Request(`${portal}/api/tools/documents?${new URLSearchParams({ model: state.model, apiKeyId })}`, init);
}

test("文書参照は本人・選択購読・署名・保持期限を検証し、別利用者や改ざんを拒否する", () => {
  const handle = sealDocumentOperation(state, actor, apiKeyId, secret);
  assert.deepEqual(openDocumentOperation(handle, actor, apiKeyId, secret), state);
  assert.equal(handle.includes(state.ticket), false);
  assert.throws(() => openDocumentOperation(handle, { ...actor, objectId: "55555555-5555-4555-8555-555555555555" }, apiKeyId, secret));
  assert.throws(() => openDocumentOperation(handle, actor, "portal-66666666-6666-4666-8666-666666666666", secret));
  assert.throws(() => openDocumentOperation(handle.slice(0, -3) + "aaa", actor, apiKeyId, secret));
  assert.throws(() => openDocumentOperation(handle, actor, apiKeyId, secret, Date.parse(state.expiresAt)));
});

test("2MiB文書を1回受付し、本人参照で状態確認後に完全な結果を取得する", async () => {
  let calls = 0; let grants = 0;
  const deps = dependencies({ resolveSubscriptionKey: async () => { grants++; return subscription; }, fetch: async (url, init) => {
    calls++;
    if (calls === 1) {
      assert.equal(init?.method, "POST"); assert.equal((init?.body as ArrayBuffer).byteLength, 2 * 1_048_576);
      const job = { operationId: state.operationId, model: state.model, statusPath: state.statusPath, ticket: state.ticket, expiresAt: state.expiresAt };
      return Response.json({ ...job, status: "running" }, { status: 202, headers: { "Retry-After": "3" } });
    }
    assert.equal(String(url), `${gateway}${state.statusPath}`); assert.equal(init?.method, "GET");
    assert.equal(new Headers(init?.headers).get("X-Core-Document-Ticket"), state.ticket);
    return calls === 2 ? Response.json({ operationId: state.operationId, model: state.model, status: "running" }, { status: 202 }) : Response.json(native);
  } });
  const response = await handleDocumentAnalysis(upload(), deps);
  assert.equal(response.status, 202); const pending = await response.json();
  assert.equal(pending.retryAfterSeconds, 3); assert.equal("ticket" in pending, false); assert.equal("statusPath" in pending, false);
  assert.equal(JSON.stringify(pending).includes(subscription), false);
  assert.equal((await handleDocumentAnalysisStatus(poll(pending.handle), deps)).status, 202);
  const complete = await handleDocumentAnalysisStatus(poll(pending.handle), deps);
  assert.equal(complete.status, 200); assert.equal((await complete.json()).rawResponse.analyzeResult.content, "Complete document");
  assert.equal(calls, 3); assert.equal(grants, 3);
  const denied = await handleDocumentAnalysisStatus(poll(pending.handle), { ...deps, readSession: async () => ({ ...actor, objectId: "77777777-7777-4777-8777-777777777777" }) });
  assert.equal(denied.status, 403); assert.equal(calls, 3);
});

test("Portalは202だけを再確認し、元文書を再送せず完了まで待つ", async () => {
  const pending = { operationId: state.operationId, model: state.model, status: "running", handle: sealDocumentOperation(state, actor, apiKeyId, secret), expiresAt: state.expiresAt, retryAfterSeconds: 2 };
  let polls = 0;
  const result = await waitForDocumentAnalysis(Response.json(pending, { status: 202 }), state.model, apiKeyId, new AbortController().signal, {
    wait: async () => undefined,
    fetch: async (url, init) => {
      assert.equal(url, "/api/tools/documents/status"); assert.deepEqual(JSON.parse(String(init?.body)), { apiKeyId, handle: pending.handle }); polls++;
      return polls === 1 ? Response.json(pending, { status: 202 }) : Response.json({ model: state.model, durationMs: 1000, rawResponse: native });
    },
  });
  assert.equal(result.content, "Complete document"); assert.equal(polls, 2);
});

test("状態確認の一時障害と429では同じ参照だけを再確認し、元ファイルを再送しない", async () => {
  const pending = { operationId: state.operationId, model: state.model, status: "running", handle: sealDocumentOperation(state, actor, apiKeyId, secret), expiresAt: state.expiresAt, retryAfterSeconds: 2 };
  const waits: number[] = []; let calls = 0;
  const result = await waitForDocumentAnalysis(Response.json(pending, { status: 202 }), state.model, apiKeyId, new AbortController().signal, {
    wait: async (milliseconds) => { waits.push(milliseconds); },
    fetch: async (url, init) => {
      assert.equal(url, "/api/tools/documents/status"); assert.equal(JSON.parse(String(init?.body)).handle, pending.handle);
      if (++calls === 1) throw new Error("Synthetic connection failure");
      if (calls === 2) return new Response(null, { status: 429, headers: { "Retry-After": "7" } });
      return Response.json({ model: state.model, durationMs: 1000, rawResponse: native });
    },
  });
  assert.equal(result.content, "Complete document"); assert.deepEqual(waits, [2000, 4000, 7000]);
});

test("待機中の中止は状態確認のタイマーを停止し、受付済み参照を残す", async () => {
  const pending = { operationId: state.operationId, model: state.model, status: "running", handle: sealDocumentOperation(state, actor, apiKeyId, secret), expiresAt: state.expiresAt, retryAfterSeconds: 2 };
  const controller = new AbortController(); let retained = ""; let calls = 0;
  const run = waitForDocumentAnalysis(Response.json(pending, { status: 202 }), state.model, apiKeyId, controller.signal, { onPending: (job) => { retained = job.handle; queueMicrotask(() => controller.abort()); }, fetch: async () => { calls++; throw new Error("No poll after cancellation"); } });
  await assert.rejects(run); assert.equal(retained, pending.handle); assert.equal(calls, 0);
});

test("raw文書は本人・購読を確認してから逐次転送し、7200秒の提供元RetryAfterも保持する", async () => {
  let granted = false; let produced = 0; let forwarded = 0;
  const packet = new Uint8Array(1_048_576); packet.set(new TextEncoder().encode("%PDF-1.7"));
  const stream = new ReadableStream<Uint8Array>({ pull(controller) { assert.equal(granted, true); if (++produced <= 4) controller.enqueue(packet); else controller.close(); } }, { highWaterMark: 0 });
  const response = await handleDocumentAnalysis(rawUpload(stream), dependencies({
    resolveSubscriptionKey: async () => { granted = true; return subscription; },
    fetch: async (_url, init) => {
      assert.equal((init as RequestInit & { duplex: string }).duplex, "half");
      assert.ok(init?.body instanceof ReadableStream);
      const reader = init.body.getReader();
      while (true) { const next = await reader.read(); if (next.done) break; forwarded += next.value.length; }
      const job = { operationId: state.operationId, model: state.model, statusPath: state.statusPath, ticket: state.ticket, expiresAt: state.expiresAt };
      return Response.json({ ...job, status: "running" }, { status: 202, headers: { "Retry-After": "7200" } });
    },
  }));
  assert.equal(response.status, 202); assert.equal(forwarded, 4 * 1_048_576);
  assert.equal((await response.json()).retryAfterSeconds, 7200);
});

test("500MB超過は同じ小さなチャンクを使った逐次計測でも停止し、本文全体を集約しない", async () => {
  const packet = new Uint8Array(1_000_000); packet.set(new TextEncoder().encode("%PDF-1.7"));
  let produced = 0; let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({ pull(controller) { produced++; controller.enqueue(packet); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
  const response = await handleDocumentAnalysis(rawUpload(stream), dependencies({ fetch: async (_url, init) => {
    const reader = (init!.body as ReadableStream<Uint8Array>).getReader();
    while (!(await reader.read()).done) { /* 同じ1MBチャンクを逐次計測し、保持しません。 */ }
    throw new Error("An oversized upload must fail before completion");
  } }));
  assert.equal(response.status, 413); assert.equal(produced, 501); assert.equal(cancelled, true);
});
