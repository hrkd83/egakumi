import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError, type ApiKeyActor, type ApiKeyRecord } from "../lib/api-keys/contracts.ts";
import type { PrivateState, SharedStateStore, StateTransaction } from "../lib/connection-session/client.ts";
import { ConnectionSessionError } from "../lib/connection-session/client.ts";
import { getGraphToolEnvironment, handleGraphToolConnection, handleGraphToolKeys, handleGraphToolProfile, type GraphToolDependencies } from "../lib/graph-tool-server.ts";

const portal = "https://portal.example.invalid";
const origin = "https://gateway.example.invalid";
const owner: ApiKeyActor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const keyId = "portal-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const groupId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const sid = "d".repeat(64), current = `cs2.${sid}.${"e".repeat(64)}`, candidate = `cs2.${sid}.${"f".repeat(64)}`;
const transactionId = "a".repeat(64), pollSecret = "b".repeat(64), secretKey = "SYNTHETIC_SECRET_KEY_NEVER_DISPLAY";
const loginUrl = `${origin}/auth/connections/browser/launch#transactionId=${transactionId}&launch=${"c".repeat(64)}`;
const auth = { protocolVersion: 2, status: "need_login", transactionId, pollSecret, provisionalConnectionSession: current, loginUrl, expiresIn: 600 };
const stamp = Math.floor(Date.now() / 1000);
const metadata = (generation = 1) => ({ protocolVersion: 2, sessionId: sid, generation, idleExpiresAt: stamp + 86400,
  absoluteExpiresAt: stamp + 2592000, nextRotationAt: stamp + 86400, expiresAt: stamp + 86400 });
const profile = { id: owner.objectId, userPrincipalName: "person@example.invalid", displayName: "検証利用者", "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users(id,userPrincipalName,displayName)/$entity" };

/** 秘密の保管先だけを置き換え、実SDKの版数比較・本人別bindingをそのまま検証します。 */
class TestStore implements SharedStateStore {
  states = new Map<string, PrivateState>();
  reads = 0;
  async withExclusive<T>(binding: string, action: (transaction: StateTransaction) => Promise<T>): Promise<T> {
    this.reads++;
    return action({ read: async () => structuredClone(this.states.get(binding)), save: async (expected, state) => {
      assert.equal(expected, this.states.get(binding)?.revision ?? 0);
      assert.equal(state.revision, expected + 1);
      this.states.set(binding, structuredClone(state));
    } });
  }
  state(): PrivateState { assert.equal(this.states.size, 1); return [...this.states.values()][0]; }
}

/** APIMの応答だけを合成し、呼ばれた固定URL・秘密ヘッダー・要求回数を観測します。 */
function setup(replies: (Response | Error)[] = []) {
  const store = new TestStore();
  const calls: { url: string; init: RequestInit }[] = [];
  let allowed = true;
  const credentialCalls: unknown[][] = [];
  const dependencies: GraphToolDependencies = {
    readActor: async () => owner, readPortalOrigin: () => portal, readGateway: () => ({ origin, profile: "graph-delegated" }), readStore: () => store,
    resolveCredential: async (...args) => { credentialCalls.push(args); if (!allowed) throw new ApiKeyError("NotFound", 404);
      return { key: secretKey, subscriptionId: "actual-apim-subscription" }; },
    fetch: (async (url, init) => { calls.push({ url: String(url), init: init! }); const next = replies.shift(); if (next instanceof Error) throw next;
      assert.ok(next, "用意した通信以外は発生しない"); return next; }) as typeof fetch,
  };
  return { store, calls, dependencies, credentialCalls, set allowed(value: boolean) { allowed = value; } };
}

/** ブラウザーが送れる入力を、台帳IDだけに限定したBFF要求です。 */
function request(kind = "profile", value: unknown = { apiKeyId: keyId }, headers: HeadersInit = {}): Request {
  return new Request(`${portal}/api/tools/graph/${kind}`, { method: "POST", headers: { origin: portal, "sec-fetch-site": "same-origin", "content-type": "application/json", ...headers }, body: JSON.stringify(value) });
}

/** 公開結果から、セッション・ポーリング秘密・購読キーが漏れないことをまとめて確認します。 */
async function body(response: Response): Promise<Record<string, unknown>> {
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  const text = await response.text();
  for (const secret of [current, candidate, pollSecret, secretKey]) assert.equal(text.includes(secret), false);
  for (const name of response.headers.keys()) assert.equal(name.startsWith("x-core-connection"), false);
  return JSON.parse(text);
}

/** 認証なしのAPI応答です。取引と接続セッションは異なる乱数を使っています。 */
function challenge(): Response { return Response.json({ error: { code: "ConnectionRequired" }, authentication: auth }, { status: 409 }); }
/** 完了確認はメタデータと検証した本人だけを返します。 */
function completion(actor: ApiKeyActor = owner): Response { return Response.json({ ...metadata(), status: "complete", tenantId: actor.tenantId, ownerId: actor.objectId }); }

test("業務APIから認証案内を取得し、完了確認後だけ本人プロフィールへ進む", async () => {
  const app = setup([challenge(), Response.json({ status: "pending" }), completion(), Response.json(profile, { headers: { "apim-request-id": "test-request-1" } })]);
  const first = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(first.status, 409);
  assert.deepEqual(await body(first), { status: "need_login", loginUrl, expiresIn: 600 });
  assert.equal(app.store.state().status, "pending");
  assert.deepEqual(await body(await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies)), { status: "pending" });
  assert.equal(app.calls.length, 2, "ポーリングはGraphを呼び直さない");
  assert.deepEqual(await body(await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies)), { status: "complete" });
  const result = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(result.status, 200);
  const resultBody = await body(result);
  assert.deepEqual(resultBody.profile, profile);
  assert.equal(resultBody.sessionUpdatePending, false);
  assert.equal(resultBody.requestId, "test-request-1");
  assert.deepEqual(app.calls.map((call) => new URL(call.url).pathname), ["/tools/graph/v1/me", "/auth/connections/status", "/auth/connections/status", "/tools/graph/v1/me"]);
  assert.equal(new Headers(app.calls[0].init.headers).get("X-Core-Connection-Session"), null);
  assert.equal(new Headers(app.calls[3].init.headers).get("X-Core-Connection-Session"), current);
  assert.equal(new Headers(app.calls[1].init.headers).get("X-Core-Connection-Poll"), pollSecret);
  for (const call of app.calls) assert.equal(new Headers(call.init.headers).get("api-key"), secretKey);
  for (const args of app.credentialCalls) assert.deepEqual(args, [owner, keyId, "graph-profile-v1"]);
});

test("並行画面の再実行は保存済み認証取引を共有し、二重の接続を作らない", async () => {
  const app = setup([challenge()]);
  await handleGraphToolProfile(request(), app.dependencies);
  const second = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(second.status, 409);
  assert.equal((await body(second)).loginUrl, loginUrl);
  assert.equal(app.calls.length, 1);
});

test("認証中にキー停止・グループ脱退が起きたら、完了確認前にも再認可して通信しない", async () => {
  const app = setup([challenge()]);
  await handleGraphToolProfile(request(), app.dependencies);
  const readsBefore = app.store.reads;
  app.allowed = false;
  for (const operation of [() => handleGraphToolProfile(request(), app.dependencies), () => handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies)]) {
    const result = await operation(); assert.equal(result.status, 403); assert.deepEqual(await body(result), { error: { code: "ApiKeyUnavailable" } });
  }
  assert.equal(app.calls.length, 1);
  assert.equal(app.store.reads, readsBefore, "認可の失敗後は保存済み本人接続を読まない");
});

test("Microsoftで別アカウントを認証しても、Portal本人へ接続セッションを転用しない", async () => {
  const app = setup([challenge(), completion({ ...owner, objectId: "33333333-3333-4333-8333-333333333333" }), challenge()]);
  await handleGraphToolProfile(request(), app.dependencies);
  const result = await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies);
  assert.equal(result.status, 403);
  assert.deepEqual(await body(result), { error: { code: "ConnectionIdentityMismatch" } });
  assert.equal(app.store.state().status, "signed_out");
  assert.equal(app.store.state().authentication, undefined);
  assert.equal((await handleGraphToolProfile(request(), app.dependencies)).status, 409);
  assert.equal(app.calls[2].url, `${origin}/tools/graph/v1/me`);
  assert.equal(new Headers(app.calls[2].init.headers).has("X-Core-Connection-Session"), false);
});

test("署名検証済み本人と実購読IDごとに保存先が分かれ、台帳IDの付け替えで共有されない", async () => {
  const app = setup([challenge(), challenge(), challenge()]);
  await handleGraphToolProfile(request(), app.dependencies);
  await handleGraphToolProfile(request(), { ...app.dependencies, readActor: async () => ({ ...owner, objectId: "33333333-3333-4333-8333-333333333333" }) });
  await handleGraphToolProfile(request(), { ...app.dependencies, resolveCredential: async () => ({ key: secretKey, subscriptionId: "different-real-subscription" }) });
  assert.equal(app.store.states.size, 3);
});

test("秘密の追加入力・任意接続先・クロスサイト要求では認可や上流通信をしない", async () => {
  const app = setup();
  for (const value of [{ apiKeyId: keyId, connectionSession: current }, { apiKeyId: keyId, upn: "forged@example.invalid" }, { apiKeyId: keyId, path: "https://attacker.invalid" }]) {
    const result = await handleGraphToolProfile(request("profile", value), app.dependencies); assert.equal(result.status, 400); await body(result);
  }
  for (const headers of [{ origin: "https://attacker.invalid" }, { "sec-fetch-site": "cross-site" }] as Record<string, string>[]) {
    assert.equal((await handleGraphToolProfile(request("profile", undefined, headers), app.dependencies)).status, 403);
  }
  assert.equal((await handleGraphToolProfile(request("profile", { apiKeyId: keyId, value: "x".repeat(5000) }), app.dependencies)).status, 400);
  assert.equal(app.credentialCalls.length, 0);
  assert.equal(app.store.reads, 0);
  assert.equal(app.calls.length, 0);
});

test("認証Cookieがなければキーも接続状態も取得せず、認証障害も固定の失敗へ変換する", async () => {
  const app = setup();
  assert.equal((await handleGraphToolProfile(request(), { ...app.dependencies, readActor: async () => null })).status, 401);
  const result = await handleGraphToolProfile(request(), { ...app.dependencies, readActor: async () => { throw new Error(secretKey); } });
  assert.equal(result.status, 503); assert.deepEqual(await body(result), { error: { code: "AuthenticationUnavailable" } });
  assert.equal(app.credentialCalls.length, 0);
});

test("共有保存先が使えないときはプロセスメモリへ代替せず、秘密を含まない失敗で止める", async () => {
  const app = setup();
  const result = await handleGraphToolProfile(request(), { ...app.dependencies, readStore: () => { throw new ConnectionSessionError("SessionStoreUnavailable", 503); } });
  assert.equal(result.status, 503); assert.deepEqual(await body(result), { error: { code: "ConnectionStateUnavailable" } });
  assert.equal(app.calls.length, 0);
});

test("権限不足と条件付きアクセス要求を分け、Microsoftへの再認証ループを作らない", async () => {
  for (const [upstream, expected] of [["Authorization_RequestDenied", "GraphAccessDenied"], ["ConnectionClaimsRequired", "GraphReauthenticationRequired"]]) {
    const app = setup([Response.json({ error: { code: upstream, message: secretKey }, privateValue: current }, { status: 403 })]);
    const result = await handleGraphToolProfile(request(), app.dependencies);
    assert.equal(result.status, 403); assert.deepEqual(await body(result), { error: { code: expected } });
    assert.equal(app.calls.length, 1);
  }
});

test("成功本文の余分な機微属性や別人IDは画面へ渡さない", async () => {
  for (const [upstream, code, status] of [[{ ...profile, accessToken: secretKey }, "GatewayResponseInvalid", 502], [{ ...profile, id: "other-user" }, "ConnectionIdentityMismatch", 403]] as const) {
    const app = setup([Response.json(upstream)]);
    const result = await handleGraphToolProfile(request(), app.dependencies);
    assert.equal(result.status, status); assert.deepEqual(await body(result), { error: { code } });
  }
});

test("取引期限切れは秘密を消去してexpiredを返し、次の業務APIで認証を取り直せる", async () => {
  const app = setup([challenge(), Response.json({ error: { code: "ConnectionNotAuthorized" }, private: pollSecret }, { status: 409 }), challenge()]);
  await handleGraphToolProfile(request(), app.dependencies);
  const result = await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies);
  assert.equal(result.status, 200); assert.deepEqual(await body(result), { status: "expired" });
  assert.equal(app.store.state().status, "signed_out");
  assert.equal(app.store.state().authentication, undefined);
  assert.equal((await handleGraphToolProfile(request(), app.dependencies)).status, 409);
});

test("取引の403・不明409・503は期限切れと決めつけず、再認証へ誘導しない", async () => {
  for (const status of [403, 409, 503]) {
    const app = setup([challenge(), Response.json({ error: { code: "UnrelatedFailure" } }, { status })]);
    await handleGraphToolProfile(request(), app.dependencies);
    const result = await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies);
    assert.equal(result.status, 503); assert.deepEqual(await body(result), { error: { code: "GatewayUnavailable" } });
    assert.equal(app.store.state().status, "pending");
  }
});

test("日次更新の確定通信が失敗してもGraph成功を返し、次回は候補証明で確定だけを先に回収する", async () => {
  const updated = Response.json(profile, { headers: { "X-Core-Connection-Update": candidate, "X-Core-Connection-Update-Id": "9".repeat(64),
    "X-Core-Connection-Update-From-Generation": "1", "X-Core-Connection-Update-Generation": "2", "X-Core-Connection-Update-Expires-At": String(stamp + 600) } });
  const app = setup([challenge(), completion(), updated, new Error(secretKey), Response.json(metadata(2)), Response.json(profile)]);
  await handleGraphToolProfile(request(), app.dependencies);
  await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies);
  const first = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(first.status, 200); assert.equal((await body(first)).sessionUpdatePending, true);
  const second = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(second.status, 200); assert.equal((await body(second)).sessionUpdatePending, false);
  assert.equal(app.store.state().current, candidate);
  assert.equal(new Headers(app.calls[4].init.headers).get("X-Core-Connection-Session"), candidate);
  assert.equal(app.calls.filter((call) => new URL(call.url).pathname === "/tools/graph/v1/me").length, 3, "成功済みGraphを更新のために再送しない");
});

test("保存済み更新候補もセッション本体も失効した場合は、503を繰り返さず新しいMicrosoft認証へ案内する", async () => {
  const updated = Response.json(profile, { headers: { "X-Core-Connection-Update": candidate, "X-Core-Connection-Update-Id": "9".repeat(64),
    "X-Core-Connection-Update-From-Generation": "1", "X-Core-Connection-Update-Generation": "2", "X-Core-Connection-Update-Expires-At": String(stamp + 600) } });
  const rejected = () => Response.json({ error: { code: "ConnectionNotAuthorized" } }, { status: 403 });
  const app = setup([challenge(), completion(), updated, new Error("lost"), rejected(), rejected(), challenge()]);
  await handleGraphToolProfile(request(), app.dependencies);
  await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies);
  assert.equal((await handleGraphToolProfile(request(), app.dependencies)).status, 200);
  const result = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(result.status, 409);
  assert.equal((await body(result)).status, "need_login");
  assert.equal(app.store.state().status, "pending");
  assert.equal(app.store.state().pending, undefined);
  assert.equal(app.calls[6].url, `${origin}/tools/graph/v1/me`);
  assert.equal(new Headers(app.calls[6].init.headers).has("X-Core-Connection-Session"), false);
});

test("キー候補はGraph許可済みの表示属性だけで、台帳や秘密の追加フィールドを返さない", async () => {
  const app = setup();
  const key = { id: keyId, groupId, displayName: "テスト購読", companyName: "サンプル会社", state: "active", primaryKey: secretKey } as unknown as ApiKeyRecord;
  const calls: unknown[][] = [];
  const result = await handleGraphToolKeys(new Request(`${portal}/api/tools/graph/keys`), { ...app.dependencies,
    readChoices: async (...args) => { calls.push(args); return { keys: [key, { ...key, id: "portal-cccccccc-cccc-4ccc-8ccc-cccccccccccc", state: "suspended" }], groups: [{ id: groupId, displayName: "ORG_基盤", products: [] }] }; } });
  assert.deepEqual(await body(result), { keys: [{ id: keyId, displayName: "テスト購読", companyName: "サンプル会社", groupName: "ORG_基盤" }] });
  assert.deepEqual(calls, [[owner, "graph-profile-v1"]]);
});

test("サーバー接続設定はHTTPSの固定Originと論理プロファイルだけを許可する", () => {
  assert.deepEqual(getGraphToolEnvironment({ PORTAL_GATEWAY_URL: origin }), { origin, profile: "graph-delegated" });
  for (const url of ["", "http://gateway.example.invalid", `${origin}/custom`, `${origin}?key=secret`, "https://user:password@gateway.example.invalid"]) {
    assert.throws(() => getGraphToolEnvironment({ PORTAL_GATEWAY_URL: url }));
  }
  assert.throws(() => getGraphToolEnvironment({ PORTAL_GATEWAY_URL: origin, PORTAL_GRAPH_PROFILE: "../other" }));
});

test("本人確認後の12属性を通し、nullと無効アカウントのfalseをそのまま保持する", async () => {
  const expanded = { ...profile, givenName: "太郎", surname: "山田", jobTitle: "Engineer", companyName: "Example", department: "Development", employeeId: "00001234", officeLocation: null, mail: null, accountEnabled: false };
  const app = setup([challenge(), completion(), Response.json(expanded)]);
  await handleGraphToolProfile(request(), app.dependencies);
  await handleGraphToolConnection(request("connection", { apiKeyId: keyId, action: "status" }), app.dependencies);
  const result = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(result.status, 200);
  assert.deepEqual((await body(result)).profile, expanded);
});
