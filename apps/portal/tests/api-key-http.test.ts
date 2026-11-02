import assert from "node:assert/strict";
import { test } from "node:test";
import { handleApiKeys, type ApiKeyHttpDependencies } from "../lib/api-keys/http.ts";
import { ApiKeyError, type ApiKeyRecord } from "../lib/api-keys/contracts.ts";
import { safeReturnTo } from "../lib/auth-navigation.ts";
import { parsePortalSignIn } from "../lib/auth-routes.ts";

const origin = "https://portal.example.invalid";
const actor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const record: ApiKeyRecord = {
  id: "portal-33333333-3333-4333-8333-333333333333", groupId: "44444444-4444-4444-8444-444444444444",
  apimSubscriptionId: "portal-33333333-3333-4333-8333-333333333333",
  productId: "model-users", displayName: "Example application", state: "active",
  createdAt: "2026-10-30T00:00:00.000Z", updatedAt: "2026-10-30T00:00:00.000Z", lastReviewedAt: null,
  budgetMode: "unlimited", clientType:null,systemName:null,teamName:null,companyId:null,companyName:null,contactEmail:null,
};
const input = { groupId: record.groupId, clientType:"custom-system",companyId:"cccccccc-cccc-4ccc-8ccc-cccccccccccc",contactEmail:"team@example.invalid", displayName: record.displayName, idempotencyKey: "55555555-5555-4555-8555-555555555555" };

/** 外部I/Oだけを差し替え、HTTPの認証・入力・Origin検査を本番と共通で通します。 */
function fixture() {
  const calls: unknown[][] = [];
  const dependencies: ApiKeyHttpDependencies = {
    readActor: async () => actor,
    readOrigin: () => origin,
    getService: () => ({
      overview: async (...args) => { calls.push(["overview", ...args]); return { groups: [], keys: [record] }; },
      create: async (...args) => { calls.push(["create", ...args]); return record; },
      act: async (...args) => { calls.push(["act", ...args]); return args[2] === "reveal" ? { primaryKey: "test-primary", secondaryKey: "test-secondary" } : record; },
    }),
  };
  return { calls, dependencies };
}

/** ブラウザーと同じOriginを持つ、検証用のPOST要求です。 */
function post(body: unknown, headers: Record<string, string> = {}) {
  return new Request(`${origin}/api/api-keys`, {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json", ...headers }, body: JSON.stringify(body),
  });
}

test("未認証のキー管理は設定や管理APIに触れず拒否する", async () => {
  const { dependencies, calls } = fixture();
  dependencies.readActor = async () => null;
  dependencies.getService = () => { throw new Error("must not initialize"); };
  const response = await handleApiKeys(post(input), dependencies);
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { error: { code: "AuthenticationRequired" } });
  assert.deepEqual(calls, []);
});

test("Origin欠落・別サイトの操作を拒否し、キーを露出しない", async () => {
  for (const request of [post(input, { Origin: "https://foreign.invalid" }), post(input, { Origin: "" }), post({ action: "reveal" }, { "Sec-Fetch-Site": "cross-site" })]) {
    const { dependencies, calls } = fixture();
    const response = await handleApiKeys(request, dependencies, record.id);
    assert.equal(response.status, 403);
    assert.equal(calls.length, 0);
    assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  }
});

test("発行はサーバーで確認した本人だけを使い、所有グループと再試行IDを渡す", async () => {
  const { dependencies, calls } = fixture();
  const response = await handleApiKeys(post(input), dependencies);
  assert.equal(response.status, 201);
  assert.deepEqual(calls, [["create", actor, input]]);
  assert.deepEqual(await response.json(), record);
  const injected = await handleApiKeys(post({ ...input, ownerId: actor.objectId }), dependencies);
  assert.equal(injected.status, 400);
  assert.equal(calls.length, 1);
});

test("一覧は秘密値を含まない台帳を返し、明示操作でだけキーを取得する", async () => {
  const { dependencies, calls } = fixture();
  const response = await handleApiKeys(new Request(`${origin}/api/api-keys`), dependencies);
  assert.deepEqual(await response.json(), { groups: [], keys: [record] });
  const revealed = await handleApiKeys(post({ action: "reveal" }), dependencies, record.id);
  assert.deepEqual(await revealed.json(), { primaryKey: "test-primary", secondaryKey: "test-secondary" });
  assert.equal(revealed.headers.get("cache-control"), "no-store");
  assert.deepEqual(calls[1], ["act", actor, record.id, "reveal"]);
});

test("既存共有購読ID・不明な操作・過大本文を受け付けない", async () => {
  const { dependencies, calls } = fixture();
  for (const [request, id] of [
    [post({ action: "delete" }), "existing-shared-key"],
    [post({ action: "regenerate-admin" }), record.id],
    [post({ action: "reveal", groupId: "spoofed" }), record.id],
    [post({ ...input, displayName: "x".repeat(9000) }), undefined],
  ] as const) {
    const response = await handleApiKeys(request, dependencies, id);
    assert.ok([400, 404].includes(response.status));
  }
  assert.equal(calls.length, 0);
});

test("サービスの内部例外や秘密値を応答へ混入させない", async () => {
  const { dependencies } = fixture();
  dependencies.getService = () => { throw new Error("access_token=private-test-value"); };
  const response = await handleApiKeys(post(input), dependencies);
  assert.equal(response.status, 503);
  assert.equal((await response.text()).includes("private-test-value"), false);
  dependencies.getService = () => { throw new ApiKeyError("ConfigurationUnavailable", 503); };
  assert.deepEqual(await (await handleApiKeys(post(input), dependencies)).json(), { error: { code: "ConfigurationUnavailable" } });
});

test("APIキー管理も安全なログイン復帰先に含め、別サイトは拒否する", () => {
  assert.equal(safeReturnTo("/ja/api-keys", "ja"), "/ja/api-keys");
  assert.equal(safeReturnTo("https://foreign.invalid/api-keys", "ja"), "/ja/models");
  assert.ok(parsePortalSignIn({ provider: "microsoft", callbackURL: "/ja/api-keys", errorCallbackURL: "/ja/login?error=authentication_failed&next=%2Fja%2Fapi-keys" }));
});
