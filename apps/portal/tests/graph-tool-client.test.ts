import assert from "node:assert/strict";
import { test } from "node:test";
import { executeGraphTool } from "../components/tools/graph-tool-client.ts";
import { createGraphToolStore } from "../components/tools/graph-tool-store.ts";

const response = { status: "complete" as const, profile: { id: "synthetic-graph-user" }, durationMs: 7 };

test("Graph試用のブラウザーは資格情報を送らず、固定BFFの検証済み応答を受け取る", async (context) => {
  const fetch = context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(url, "/api/tools/graph/profile");
    assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(String(init?.body)), { authentication: "bearer" });
    assert.equal(new Headers(init?.headers).has("Authorization"), false);
    return Response.json(response);
  });
  assert.deepEqual(await executeGraphTool({ authentication: "bearer" }, new AbortController().signal), response);
  assert.equal(fetch.mock.callCount(), 1);
});

test("認可の再接続が必要な応答は既知の公開コードを保持する", async (context) => {
  context.mock.method(globalThis, "fetch", async () => Response.json({ error: { code: "ApiAuthenticationRequired" } }, { status: 401 }));
  await assert.rejects(executeGraphTool({ authentication: "bearer" }, new AbortController().signal),
    { failure: { error: { code: "ApiAuthenticationRequired" } } });
});

test("API接続を解除するとGraph試用の結果は消える", () => {
  const state = createGraphToolStore();
  state.getState().begin();
  state.getState().receive(response);
  assert.equal(state.getState().result, response);
  state.getState().reset();
  assert.equal(state.getState().phase, "idle");
  assert.equal(state.getState().result, null);
});
