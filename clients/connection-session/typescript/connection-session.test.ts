import assert from "node:assert/strict";
import test from "node:test";
import { ConnectionSessionClient, ConnectionSessionError } from "./connection-session.ts";
import type { PrivateState, SharedStateStore } from "./connection-session.ts";

const origin = "https://gateway.example.com";
const sid = "d".repeat(64);
const current = `cs2.${sid}.${"e".repeat(64)}`;
const candidate = `cs2.${sid}.${"1".repeat(64)}`;
const principal = { tenantId: "11111111-2222-4333-8444-555555555555", ownerId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" };
const metadata = (generation = 1) => ({ protocolVersion: 2 as const, sessionId: sid, generation, idleExpiresAt: 2000000000,
  absoluteExpiresAt: 2100000000, nextRotationAt: 1900000000 + generation * 86400, expiresAt: 2000000000 });
const rotation = { rotationId: "f".repeat(64), candidateSession: candidate, fromGeneration: 1, generation: 2, expiresAt: 2000000300 };
const authentication = { protocolVersion: 2, status: "need_login", transactionId: "a".repeat(64), pollSecret: "b".repeat(64),
  provisionalConnectionSession: current, expiresIn: 600,
  loginUrl: `${origin}/auth/connections/browser/launch#transactionId=${"a".repeat(64)}&launch=${"c".repeat(64)}` };

/** 実通信を行わないテストだけで使うメモリ保存先。本番用の共有DB実装とは別です。 */
class TestStore implements SharedStateStore {
  state?: PrivateState;
  bindings: string[] = [];
  async withExclusive<T>(binding: string, action: Parameters<SharedStateStore["withExclusive"]>[1]): Promise<T> {
    this.bindings.push(binding);
    return await action({ read: async () => structuredClone(this.state), save: async (revision, next) => {
      assert.equal(revision, this.state?.revision ?? 0);
      assert.equal(next.revision, revision + 1);
      this.state = structuredClone(next);
    } }) as T;
  }
}

function setup(replies: (Response | Error)[], saved = true) {
  const store = new TestStore();
  if (saved) store.state = { revision: 0, status: "complete", current, generation: 1, sessionId: sid, metadata: metadata() };
  const requests: { url: string; init: RequestInit }[] = [];
  const mockFetch = async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(input), init: init! });
    if (String(input).endsWith("/session/commit")) assert.equal(store.state?.pending?.candidateSession, candidate);
    const next = replies.shift();
    if (next instanceof Error) throw next;
    assert.ok(next);
    return next;
  };
  const client = new ConnectionSessionClient({ origin, apiKey: "private-key", subscriptionId: "subscription-1", profile: "graph-delegated",
    principal, store, fetch: mockFetch as typeof fetch });
  return { client, store, requests };
}

function updatedResponse(status = 200) {
  return Response.json({ id: "same-business-body" }, { status, headers: { "X-Core-Connection-Update": candidate,
    "X-Core-Connection-Update-Id": rotation.rotationId, "X-Core-Connection-Update-From-Generation": "1",
    "X-Core-Connection-Update-Generation": "2", "X-Core-Connection-Update-Expires-At": "2000000300" } });
}

test("完了確認の既知の期限切れだけは保管中の秘密を破棄する", async () => {
  for (const status of [409, 410]) {
    const { client, store } = setup([Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 }),
      Response.json({ error: { code: "ConnectionNotAuthorized" } }, { status })], false);
    await client.get();
    await assert.rejects(client.poll(), (error: unknown) => error instanceof ConnectionSessionError && error.code === "AuthenticationTransactionExpired");
    assert.equal(store.state?.status, "signed_out");
    assert.equal(store.state?.current, "");
    assert.equal(store.state?.authentication, undefined);
  }
});

test("完了確認の権限拒否・不明競合・一時障害を期限切れとして秘密破棄しない", async () => {
  for (const status of [403, 409, 503]) {
    const { client, store } = setup([Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 }),
      Response.json({ error: { code: "UnexpectedFailure" } }, { status })], false);
    await client.get();
    await assert.rejects(client.poll(), (error: unknown) => error instanceof ConnectionSessionError && error.code === "AuthenticationStatusFailed");
    assert.equal(store.state?.status, "pending");
    assert.equal(store.state?.authentication?.pollSecret, authentication.pollSecret);
  }
});

test("成功済み業務は再送せず、保存後に確定し、秘密ヘッダーを画面へ流さない", async () => {
  const { client, requests, store } = setup([updatedResponse(), Response.json(metadata(2))]);
  const result = await client.get();
  assert.equal(result.kind, "response");
  if (result.kind !== "response") return;
  assert.deepEqual(await result.response.json(), { id: "same-business-body" });
  assert.equal(result.response.headers.has("X-Core-Connection-Update"), false);
  for (const key of result.response.headers.keys()) assert.equal(key.toLowerCase().startsWith("x-core-connection-"), false);
  assert.equal(store.state?.current, candidate);
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST"]);
});

test("確定応答が失われても業務成功を保持し、候補証明で確定だけを再送する", async () => {
  const { client, requests, store } = setup([updatedResponse(), new Error("SENTINEL_PRIVATE_FAILURE"), Response.json(metadata(2)), Response.json({ id: "next" })]);
  const first = await client.get();
  assert.equal(first.kind === "response" && first.sessionUpdatePending, true);
  assert.equal(store.state?.rotationCommitAttempted, true);
  await client.get();
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST", "POST", "GET"]);
  assert.equal(new Headers(requests[2].init.headers).get("X-Core-Connection-Session"), candidate);
});

test("更新確定待ちの間に元fetchの本文がabortされても、読み取り済み業務成功を失わない", async () => {
  const store = new TestStore();
  store.state = { revision: 0, status: "complete", current, generation: 1, sessionId: sid, metadata: metadata() };
  const source = new AbortController();
  let originalRead = false;
  const upstreamBody = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"id":"business-success"}'));
      source.signal.addEventListener("abort", () => controller.error(new DOMException("Timed out", "AbortError")));
    },
    pull(controller) { originalRead = true; controller.close(); },
  });
  let requests = 0;
  let consumedBeforeCommit = false;
  const client = new ConnectionSessionClient({ origin, apiKey: "private-key", subscriptionId: "subscription-1", profile: "graph-delegated", principal, store,
    fetch: (async () => {
      requests++;
      if (requests === 1) return new Response(upstreamBody, { headers: updatedResponse().headers });
      consumedBeforeCommit = originalRead;
      await new Promise((resolve) => setTimeout(resolve, 10));
      source.abort();
      throw new Error("commit timed out");
    }) as typeof fetch,
  });
  const result = await client.get();
  assert.equal(result.kind, "response");
  if (result.kind !== "response") return;
  assert.equal(result.sessionUpdatePending, true);
  assert.equal(consumedBeforeCommit, true, "確定通信より前に業務本文を読み終わる");
  assert.deepEqual(await result.response.json(), { id: "business-success" });
  assert.equal(requests, 2, "成功済みGraphは再送しない");
});

test("業務本文の64KiB上限を超えたら保持せず、大容量応答を無制限に蓄積しない", async () => {
  const { client, requests } = setup([new Response("x".repeat(65537))]);
  await assert.rejects(client.get(), (error) => error instanceof ConnectionSessionError && error.code === "ResponseTooLarge");
  assert.equal(requests.length, 1);
});

test("未確定候補の期限切れは現行値で確認し、候補だけを破棄して業務利用へ戻す", async () => {
  const { client, store, requests } = setup([updatedResponse(), new Error("lost"),
    Response.json({ error: { code: "ConnectionNotAuthorized" } }, { status: 403 }),
    Response.json({ error: { code: "SessionRotationExpired" } }, { status: 409 }), Response.json({ id: "next" })]);
  await client.get();
  await client.get();
  assert.equal(store.state?.pending, undefined);
  assert.equal(store.state?.current, current);
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST", "POST", "POST", "GET"]);
});

test("更新応答消失後にセッション自体が失効しても、両証明の明示拒否から新しい認証案内へ戻れる", async () => {
  const rejected = () => Response.json({ error: { code: "ConnectionNotAuthorized" } }, { status: 403 });
  const { client, store, requests } = setup([updatedResponse(), new Error("lost"), rejected(), rejected(),
    Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 })]);
  await client.get();
  assert.equal(store.state?.pending?.candidateSession, candidate);
  const result = await client.get();
  assert.equal(result.kind, "login");
  assert.equal(store.state?.status, "pending");
  assert.equal(store.state?.pending, undefined);
  assert.equal(new Headers(requests[4].init.headers).has("X-Core-Connection-Session"), false);
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST", "POST", "POST", "GET"]);
});

test("更新の503では失効を推測せず、保存した候補と現行証明を残して業務要求を止める", async () => {
  const { client, store, requests } = setup([updatedResponse(), new Error("lost"),
    Response.json({ error: { code: "ConnectionUnavailable" } }, { status: 503 })]);
  await client.get();
  await assert.rejects(client.get(), (error) => error instanceof ConnectionSessionError && error.code === "SessionCommitFailed");
  assert.equal(store.state?.status, "complete");
  assert.equal(store.state?.current, current);
  assert.equal(store.state?.pending?.candidateSession, candidate);
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST", "POST"]);
});

test("確定送信前にプロセスが止まった候補でも、失効後の消去は最新の版数で行う", async () => {
  const { client, store, requests } = setup([
    Response.json({ error: { code: "ConnectionNotAuthorized" } }, { status: 403 }),
    Response.json({ error: { code: "ConnectionNotAuthorized" } }, { status: 403 }),
    Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 }),
  ]);
  store.state!.pending = rotation;
  assert.equal(store.state!.rotationCommitAttempted, undefined);
  const result = await client.get();
  assert.equal(result.kind, "login");
  assert.equal(store.state!.revision, 3, "試行フラグ・失効値の消去・新取引保存の順で比較更新する");
  assert.equal(new Headers(requests[0].init.headers).get("X-Core-Connection-Session"), current);
  assert.equal(new Headers(requests[1].init.headers).get("X-Core-Connection-Session"), candidate);
  assert.equal(new Headers(requests[2].init.headers).has("X-Core-Connection-Session"), false);
});

test("業務実行前の更新要求だけは、更新後に一度業務GETを再試行する", async () => {
  const { client, requests } = setup([Response.json({ error: { code: "SessionUpdateRequired" }, rotation }, { status: 409 }),
    Response.json(metadata(2)), Response.json({ id: "once" })]);
  assert.equal((await client.get()).kind, "response");
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST", "GET"]);
});

test("初回URLの返却前に仮値を保存し、完了の本人IDを検証する", async () => {
  const { client, store } = setup([Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 }),
    Response.json({ status: "complete", ...metadata(), ...principal })], false);
  const result = await client.get();
  assert.equal(result.kind, "login");
  assert.equal(store.state?.current, current);
  assert.equal(store.state?.status, "pending");
  assert.equal(JSON.stringify(result).includes(authentication.pollSecret), false);
  assert.equal(JSON.stringify(result).includes(current), false);
  assert.equal(await client.poll(), "complete");
  assert.equal(store.state?.current, current);
  assert.equal(store.state?.authentication, undefined);
});

test("異なる本人・外部URL・旧形式は拒否する", async () => {
  const { client, store } = setup([Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 }),
    Response.json({ status: "complete", ...metadata(), ...principal, ownerId: principal.tenantId })], false);
  await client.get();
  await assert.rejects(client.poll(), (error) => error instanceof ConnectionSessionError && error.code === "IdentityMismatch");
  assert.equal(store.state?.status, "signed_out");
  assert.equal(store.state?.authentication, undefined);
  await assert.rejects(client.get("https://other.example.com/me"), ConnectionSessionError);
});

test("下流エラーでも更新は処理し、元のHTTP状態と本文を維持する", async () => {
  const { client, store } = setup([updatedResponse(429), Response.json(metadata(2))]);
  const result = await client.get();
  assert.equal(result.kind === "response" && result.response.status, 429);
  assert.equal(store.state?.generation, 2);
});

test("最長期限を変更した更新応答は採用しない", async () => {
  const { client, store } = setup([updatedResponse(), Response.json({ ...metadata(2), absoluteExpiresAt: 2200000000 })]);
  const result = await client.get();
  assert.equal(result.kind === "response" && result.sessionUpdatePending, true);
  assert.equal(store.state?.current, current);
  assert.equal(store.state?.metadata?.absoluteExpiresAt, 2100000000);
});

test("期限切れの認証URLは使い回さず、セッションヘッダーなしの業務要求から再案内する", async () => {
  const challenge = () => Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 });
  const { client, store, requests } = setup([challenge(), challenge()], false);
  await client.get();
  store.state!.authenticationExpiresAt = Date.now() / 1000 - 1;
  assert.equal((await client.get()).kind, "login");
  assert.equal(requests.length, 2);
  assert.equal(new Headers(requests[1].init.headers).has("X-Core-Connection-Session"), false);
  assert.ok(store.state!.authenticationExpiresAt! > Date.now() / 1000);
});

test("認証待ちのポーリング期限が切れたら保存済み秘密を破棄する", async () => {
  const { client, store, requests } = setup([Response.json({ error: { code: "ConnectionRequired" }, authentication }, { status: 409 })], false);
  await client.get();
  store.state!.authenticationExpiresAt = Date.now() / 1000 - 1;
  await assert.rejects(client.poll(), (error) => error instanceof ConnectionSessionError && error.code === "AuthenticationTransactionExpired");
  assert.equal(requests.length, 1);
  assert.equal(store.state?.status, "signed_out");
  assert.equal(store.state?.current, "");
  assert.equal(store.state?.authentication, undefined);
});

test("業務利用の新しい期限を保存し、古い応答では期限を巻き戻さない", async () => {
  const reply = (idle: number) => Response.json({ id: "ok" }, { headers: {
    "X-Core-Connection-Generation": "1", "X-Core-Connection-Idle-Expires-At": String(idle),
    "X-Core-Connection-Absolute-Expires-At": "2100000000", "X-Core-Connection-Rotate-After": "1900086400",
  } });
  const { client, store } = setup([reply(2000003600), reply(2000000000)]);
  const first = await client.get();
  assert.equal(store.state?.metadata?.idleExpiresAt, 2000003600);
  assert.equal(store.state?.metadata?.expiresAt, 2000003600);
  if (first.kind === "response") for (const key of first.response.headers.keys()) assert.equal(key.toLowerCase().startsWith("x-core-connection-"), false);
  await client.get();
  assert.equal(store.state?.metadata?.idleExpiresAt, 2000003600);
  assert.equal(store.state?.current, current);
});

test("業務利用の最長期限が変わったり期限ヘッダーが欠けたりした場合は保存しない", async () => {
  const cases: Record<string, string>[] = [ { "X-Core-Connection-Generation": "1" }, {
    "X-Core-Connection-Generation": "1", "X-Core-Connection-Idle-Expires-At": "2000000000",
    "X-Core-Connection-Absolute-Expires-At": "2200000000", "X-Core-Connection-Rotate-After": "1900086400",
  } ];
  for (const headers of cases) {
    const { client, store } = setup([Response.json({ id: "ok" }, { headers })]);
    await assert.rejects(client.get(), (error) => error instanceof ConnectionSessionError && error.code === "InvalidMetadata");
    assert.equal(store.state?.metadata?.absoluteExpiresAt, 2100000000);
  }
});

test("更新要求の再試行上限でも候補の秘密を業務応答へ流さない", async () => {
  const conflict = () => Response.json({ error: { code: "SessionUpdateRequired" }, rotation }, { status: 409 });
  const { client } = setup([conflict(), Response.json(metadata(2)), conflict()]);
  await assert.rejects(client.get(), (error) => error instanceof ConnectionSessionError && error.code === "SessionUpdateRequired");
});

test("ログアウト・全失効・接続解除はPOSTし、完了後にローカルの証明も削除する", async () => {
  for (const [operation, status] of [["logout", "signed_out"], ["logout-all", "signed_out_all"], ["disconnect", "disconnected"]] as const) {
    const { client, store, requests } = setup([Response.json({ status })]);
    if (operation === "logout") await client.logout();
    if (operation === "logout-all") await client.logoutAll();
    if (operation === "disconnect") assert.equal(await client.disconnect(), "disconnected");
    assert.equal(requests[0].url, `${origin}/auth/connections/${operation}`);
    assert.equal(requests[0].init.method, "POST");
    assert.equal(requests[0].init.body, undefined);
    assert.equal(new Headers(requests[0].init.headers).get("X-Core-Connection-Session"), current);
    assert.equal(store.state?.status, "signed_out");
    assert.equal(store.state?.current, "");
    assert.equal(store.state?.metadata, undefined);
  }
});

test("CM削除待ちの202でもSQL側の利用禁止は成立しているためローカルを消去する", async () => {
  const { client, store } = setup([Response.json({ status: "disconnect_pending" }, { status: 202 })]);
  assert.equal(await client.disconnect(), "disconnect_pending");
  assert.equal(store.state?.status, "signed_out");
  assert.equal(store.state?.current, "");
});

test("確定応答の消失後は候補で確定結果を回収し、旧値の猶予に依存せずログアウトする", async () => {
  const { client, store, requests } = setup([updatedResponse(), new Error("lost-commit-response"),
    Response.json(metadata(2)), Response.json({ status: "signed_out" })]);
  const first = await client.get();
  assert.equal(first.kind === "response" && first.sessionUpdatePending, true);
  assert.equal(store.state?.current, current);
  await client.logout();
  assert.deepEqual(requests.map((request) => request.init.method), ["GET", "POST", "POST", "POST"]);
  assert.equal(requests[2].url, `${origin}/auth/connections/session/commit`);
  assert.equal(new Headers(requests[2].init.headers).get("X-Core-Connection-Session"), candidate);
  assert.equal(requests[3].url, `${origin}/auth/connections/logout`);
  assert.equal(new Headers(requests[3].init.headers).get("X-Core-Connection-Session"), candidate);
  assert.equal(store.state?.status, "signed_out");
  assert.equal(store.state?.current, "");
  assert.equal(store.state?.pending, undefined);
});

test("失効APIの失敗を成功扱いせず、本人セッションがなければ全失効を要求しない", async () => {
  const { client, store } = setup([Response.json({ error: { code: "ConnectionUnavailable" } }, { status: 503 })]);
  await assert.rejects(client.logout(), (error) => error instanceof ConnectionSessionError && error.code === "SessionRevocationFailed");
  assert.equal(store.state?.current, current);
  const absent = setup([], false);
  await assert.rejects(absent.client.logoutAll(), (error) => error instanceof ConnectionSessionError && error.code === "NoActiveConnectionSession");
  assert.equal(absent.requests.length, 0);
});
