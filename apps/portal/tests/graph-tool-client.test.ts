import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  checkGraphToolConnection, executeGraphTool, GraphToolResponseError, isGraphToolLoginUrl, loadGraphToolKeys,
} from "../components/tools/graph-tool-client.ts";
import { canResumeGraphTool, createGraphToolStore } from "../components/tools/graph-tool-store.ts";
import { getGraphToolExample } from "../components/tools/graph-tool-examples.ts";
import { createGraphToolPoller } from "../components/tools/graph-tool-poller.ts";
import { graphToolProfileSchema, type GraphToolComplete, type GraphToolLogin } from "../lib/graph-tool-contracts.ts";

const originalFetch = globalThis.fetch;
const origin = "https://gateway.example.invalid";
const keyId = "portal-00000000-0000-4000-8000-000000000001";
const secret = "SYNTHETIC_PRIVATE_VALUE";
const login: GraphToolLogin = { status: "need_login", loginUrl: `${origin}/auth/connections/browser/launch#transactionId=example&launch=example`, expiresIn: 600 };
const completed: GraphToolComplete = { status: "complete", profile: { id: "example-user", displayName: "Example User", userPrincipalName: "user@example.invalid" }, durationMs: 12, sessionUpdatePending: false };
afterEach(() => { globalThis.fetch = originalFetch; });

/** 固定コードだけを公開し、合成秘密値や生の例外を表示しないことを確認します。 */
async function expectFailure(operation: () => Promise<unknown>, code: string) {
  await assert.rejects(operation, (error) => {
    assert.ok(error instanceof GraphToolResponseError);
    assert.equal(error.failure.error.code, code);
    assert.equal(error.message.includes(secret), false);
    return true;
  });
}

test("Graphキー候補はPortal固定URLのGETで取得し、秘密の余剰項目を拒否する", async () => {
  const key = { id: keyId, displayName: "Example", companyName: null, groupName: "ORG_Example" };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "/api/tools/graph/keys");
    assert.equal(options?.method, "GET");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.body, undefined);
    assert.equal(new Headers(options.headers).has("api-key"), false);
    return Response.json({ keys: [key] });
  };
  assert.deepEqual(await loadGraphToolKeys(new AbortController().signal), [key]);
  globalThis.fetch = async () => Response.json({ keys: [{ ...key, primaryKey: secret }] });
  await expectFailure(() => loadGraphToolKeys(new AbortController().signal), "GatewayResponseInvalid");
});

test("実行要求は台帳IDだけを送り、実プロフィールと認証案内を区別する", async () => {
  let received: RequestInit | undefined;
  globalThis.fetch = async (url, options) => { assert.equal(url, "/api/tools/graph/profile"); received = options; return Response.json(completed); };
  assert.deepEqual(await executeGraphTool(keyId, origin, new AbortController().signal), completed);
  assert.equal(received?.method, "POST");
  assert.deepEqual(JSON.parse(String(received.body)), { apiKeyId: keyId });
  assert.equal(new Headers(received.headers).has("api-key"), false);
  assert.equal(new Headers(received.headers).has("X-Core-Connection-Session"), false);
  globalThis.fetch = async () => Response.json(login, { status: 409 });
  assert.deepEqual(await executeGraphTool(keyId, origin, new AbortController().signal), login);
});

test("認証URLは設定済みGatewayのHTTPS認証画面だけを許可する", async () => {
  assert.equal(isGraphToolLoginUrl(login.loginUrl, origin), true);
  for (const url of [
    "https://other.invalid/auth/connections/browser/launch#x", `${origin}/other#x`, `${origin}/auth/connections/browser/launch`,
    `${origin}/auth/connections/browser/launch?return=https://other.invalid#x`, "http://gateway.example.invalid/auth/connections/browser/launch#x",
    "javascript:alert(1)", "https://name:password@gateway.example.invalid/auth/connections/browser/launch#x",
  ]) assert.equal(isGraphToolLoginUrl(url, origin), false);
  assert.equal(isGraphToolLoginUrl(login.loginUrl, null), false);
  globalThis.fetch = async () => Response.json({ ...login, loginUrl: "https://other.invalid/auth/connections/browser/launch#x" }, { status: 409 });
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "GatewayResponseInvalid");
});

test("完了確認は台帳IDと固定actionだけを送信し、取引や本人セッションを受け取らない", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "/api/tools/graph/connection");
    assert.deepEqual(JSON.parse(String(options?.body)), { apiKeyId: keyId, action: "status" });
    return Response.json({ status: "complete" });
  };
  assert.equal(await checkGraphToolConnection(keyId, new AbortController().signal), "complete");
  globalThis.fetch = async () => Response.json({ status: "complete", connectionSession: secret });
  await expectFailure(() => checkGraphToolConnection(keyId, new AbortController().signal), "GatewayResponseInvalid");
});

test("プロフィールは選択した公開項目を検査し、追加秘密値や過大な値を拒否する", async () => {
  assert.equal(graphToolProfileSchema.safeParse({ id: "user", displayName: null }).success, true);
  assert.equal(graphToolProfileSchema.safeParse({ id: "user" }).success, true);
  assert.equal(graphToolProfileSchema.safeParse({ id: "user", userPrincipalName: "x".repeat(321) }).success, false);
  globalThis.fetch = async () => Response.json({ ...completed, connectionSession: secret });
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "GatewayResponseInvalid");
  globalThis.fetch = async () => Response.json({ ...completed, profile: { ...completed.profile, accessToken: secret } });
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "GatewayResponseInvalid");
});

test("認可失敗はログインへ変換せず既知のエラーを維持し、不正本文は伏せる", async () => {
  globalThis.fetch = async () => Response.json({ error: { code: "ConnectionIdentityMismatch" }, requestId: "example-request" }, { status: 403 });
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "ConnectionIdentityMismatch");
  globalThis.fetch = async () => Response.json({ error: { code: secret } }, { status: 500 });
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "GatewayResponseInvalid");
  globalThis.fetch = async () => new Response(secret, { status: 401 });
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "AuthenticationRequired");
  globalThis.fetch = async () => { throw new Error(secret); };
  await expectFailure(() => executeGraphTool(keyId, origin, new AbortController().signal), "GatewayUnavailable");
});

test("パネルを閉じる際の中断は通信障害へ変換せず伝える", async () => {
  const controller = new AbortController();
  const aborted = new DOMException("Stopped", "AbortError");
  globalThis.fetch = async (_url, options) => { controller.abort(); assert.equal(options?.signal?.aborted, true); throw aborted; };
  await assert.rejects(executeGraphTool(keyId, origin, controller.signal), (error) => error === aborted);
});

test("別キーへの変更後に届く旧実行結果は採用しない", () => {
  const store = createGraphToolStore();
  store.getState().selectKey(keyId);
  const revision = store.getState().begin();
  store.getState().selectKey("portal-00000000-0000-4000-8000-000000000002");
  store.getState().receive(revision, completed, 1_000);
  store.getState().fail(revision, { error: { code: "GatewayUnavailable" } });
  assert.equal(store.getState().phase, "idle");
  assert.equal(store.getState().result, null);
  assert.equal(store.getState().failure, null);
});

test("認証待ちの期限は最初の応答から数え、完了確認だけでは延長しない", () => {
  const store = createGraphToolStore();
  store.getState().selectKey(keyId);
  const revision = store.getState().begin();
  store.getState().receive(revision, login, 1_000);
  assert.equal(store.getState().phase, "waiting");
  assert.equal(store.getState().deadline, 601_000);
  store.getState().markPollFailed(revision, true);
  store.getState().markPollFailed(revision, false);
  assert.equal(store.getState().deadline, 601_000);
  store.getState().expire(revision);
  assert.equal(store.getState().phase, "expired");
  assert.equal(store.getState().loginUrl, null);
  assert.equal(store.getState().deadline, null);
});

test("通常の完了では認証案内を消し、日次更新の保留を再認証扱いにしない", () => {
  const store = createGraphToolStore();
  const revision = store.getState().begin();
  store.getState().receive(revision, { ...completed, sessionUpdatePending: true }, 1_000);
  assert.equal(store.getState().phase, "complete");
  assert.equal(store.getState().result?.sessionUpdatePending, true);
  assert.equal(store.getState().loginUrl, null);
});

test("各言語のコードは秘密値を持たず、固定GETとバックエンド側の責務を示す", () => {
  for (const language of ["curl", "nextjs", "fastapi", "php", "dify"] as const) {
    const code = getGraphToolExample(language, origin);
    assert.ok(code.length > 400);
    assert.equal(code.includes(secret), false);
    if (language !== "dify") assert.equal(code.includes("/tools/graph/v1/me"), true);
  }
  const curl = getGraphToolExample("curl", origin);
  assert.ok(curl.includes("${API_KEY}"));
  assert.ok(curl.includes("${CONNECTION_SESSION}"));
  assert.ok(curl.includes("${POLL_SECRET}"));
});

test("手動の認証状況確認は実行中の照会を中断せず合流する", async () => {
  let calls = 0, signal: AbortSignal | undefined;
  let finish: ((value: "pending") => void) | undefined;
  const scheduled: (() => void)[] = [];
  const poller = createGraphToolPoller({
    deadline: 60_000, now: () => 1_000, visible: () => true,
    poll: async (current) => { calls++; signal = current; return new Promise((resolve) => { finish = resolve; }); },
    schedule: (callback) => { scheduled.push(callback); return callback; }, cancel: () => undefined,
    onComplete: () => assert.fail("Unexpected complete"), onExpired: () => assert.fail("Unexpected expiry"), onFailure: () => assert.fail("Unexpected failure"),
  });
  poller.requestNow(); poller.requestNow(); poller.requestNow();
  assert.equal(calls, 1);
  assert.equal(signal?.aborted, false);
  finish!("pending"); await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(scheduled.length, 1);
  poller.requestNow();
  assert.equal(calls, 2);
  poller.stop();
  assert.equal(signal?.aborted, true);
});

test("非表示タブでは完了確認を開始せず、表示復帰しても期限切れの取引を照会しない", () => {
  let visible = false, now = 1_000, calls = 0, expired = 0;
  const poller = createGraphToolPoller({
    deadline: 2_000, visible: () => visible, now: () => now,
    poll: async () => { calls++; return "pending"; },
    onExpired: () => { expired++; }, onComplete: () => assert.fail("Unexpected complete"), onFailure: () => assert.fail("Unexpected failure"),
  });
  poller.requestNow(); assert.equal(calls, 0);
  visible = true; now = 2_001;
  poller.requestNow(); poller.requestNow();
  assert.equal(calls, 0); assert.equal(expired, 1);
  poller.stop();
});

test("認証完了の自動再開は一度だけ行い、停止後の遅い応答を採用しない", async () => {
  let resumed = 0;
  let finish: ((status: "complete") => void) | undefined;
  const poller = createGraphToolPoller({
    deadline: 60_000, visible: () => true, now: () => 1_000,
    poll: async () => "complete", onComplete: () => { resumed++; }, onExpired: () => assert.fail("Unexpected expiry"), onFailure: () => assert.fail("Unexpected failure"),
  });
  poller.requestNow(); await new Promise<void>((resolve) => queueMicrotask(resolve));
  poller.requestNow(); assert.equal(resumed, 1); poller.stop();
  const stopped = createGraphToolPoller({
    deadline: 60_000, visible: () => true, now: () => 1_000,
    poll: async () => new Promise((resolve) => { finish = resolve; }),
    onComplete: () => { resumed++; }, onExpired: () => assert.fail("Unexpected expiry"), onFailure: () => assert.fail("Unexpected failure"),
  });
  stopped.requestNow(); stopped.stop(); finish!("complete");
  await new Promise<void>((resolve) => queueMicrotask(resolve));
  assert.equal(resumed, 1);
});

test("認証照会の完了直前にキーが変わっても、新しいキーの業務要求を自動実行しない", async () => {
  const store = createGraphToolStore();
  store.getState().selectKey(keyId);
  const revision = store.getState().begin();
  store.getState().receive(revision, login, 1_000);
  assert.equal(canResumeGraphTool(store.getState(), revision, keyId), true);
  let finish: ((status: "complete") => void) | undefined;
  let resumed = 0;
  const poller = createGraphToolPoller({
    deadline: 60_000, visible: () => true, now: () => 1_000,
    poll: async () => new Promise((resolve) => { finish = resolve; }),
    onComplete: () => { if (canResumeGraphTool(store.getState(), revision, keyId)) resumed++; },
    onExpired: () => assert.fail("Unexpected expiry"), onFailure: () => assert.fail("Unexpected failure"),
  });
  poller.requestNow();
  store.getState().selectKey("portal-00000000-0000-4000-8000-000000000002");
  finish!("complete");
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(resumed, 0);
  assert.equal(canResumeGraphTool({ phase: "waiting", revision, apiKeyId: "other-key" }, revision, keyId), false);
  assert.equal(canResumeGraphTool({ phase: "waiting", revision: revision + 1, apiKeyId: keyId }, revision, keyId), false);
  assert.equal(canResumeGraphTool({ phase: "expired", revision, apiKeyId: keyId }, revision, keyId), false);
  poller.stop();
});
