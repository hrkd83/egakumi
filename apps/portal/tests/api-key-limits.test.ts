import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError, type ApiKeyActor, type ApiKeyGroup, type ApiKeyRecord } from "../lib/api-keys/contracts.ts";
import { apiKeyLimitProfileSchema, unlimitedApiKeyLimits, updateApiKeyLimitsSchema,
  type UpdateApiKeyLimits } from "../lib/api-keys/limit-contracts.ts";
import { handleApiKeyLimits, type ApiKeyLimitHttpDependencies } from "../lib/api-keys/limit-http.ts";
import { ApiKeyLimitService } from "../lib/api-keys/limit-service.ts";
import type { ApiKeyLimitRepository } from "../lib/api-keys/limit-repository.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const actor: ApiKeyActor = { tenantId, objectId: "22222222-2222-4222-8222-222222222222" };
const key: ApiKeyRecord = {
  id: "portal-33333333-3333-4333-8333-333333333333", groupId: "44444444-4444-4444-8444-444444444444",
  apimSubscriptionId: "portal-33333333-3333-4333-8333-333333333333",
  productId: "model-users", displayName: "Example application", state: "active", budgetMode: "unlimited", clientType:null,systemName:null,teamName:null,companyId:null,companyName:null,contactEmail:null,
  createdAt: "2026-10-30T00:00:00.000Z", updatedAt: "2026-10-30T00:00:00.000Z", lastReviewedAt: null,
};
const values: UpdateApiKeyLimits = { expectedVersion: 0, requestRate: { calls: 2500, renewalPeriodSeconds: 60 },
  tokensPerMinute: 100000, tokenQuota: { tokens: 10000000, period: "Monthly" } };
const errorCode = (code: string) => (error: unknown) => error instanceof ApiKeyError && error.code === code;

/** 台帳・Graph・設定の境界で、誰がどの情報を読み書きしたかを検証する依存です。 */
function fixture() {
  let record: ApiKeyRecord | null = { ...key };
  let groups: ApiKeyGroup[] = [{ id: key.groupId, displayName: "Group", directoryStatus: "active", products: [{ id: key.productId, displayName: "Models", maxSubscriptions: 5 }] }];
  let member = true, failure = false, memberChecks = 0;
  let current = unlimitedApiKeyLimits(), reads = 0, writes = 0;
  const limits: ApiKeyLimitRepository = {
    get: async () => { reads++; return current; },
    update: async (_actor, _record, input) => {
      writes++;
      if (input.expectedVersion !== current.version) throw new ApiKeyError("LimitVersionConflict", 409);
      const { expectedVersion, ...settings } = input;
      current = { ...settings, version: expectedVersion + 1, updatedAt: "2026-10-30T01:00:00.000Z" };
      return current;
    },
    getGatewayProfile: async () => current,
  };
  const service = new ApiKeyLimitService({ tenantId, limits,
    keys: { getKey: async () => record, getGroups: async () => groups, observeGroups: async () => undefined },
    directory: { checkGroups: async (_actor, ids) => new Map(ids.map((id) => [id, { id, displayName: "ORG_Test", securityEnabled: true,
      status: "active" as const, checkedAt: new Date().toISOString(), errorCode: null }])) },
    membership: { check: async () => { memberChecks++; if (failure) throw new ApiKeyError("AuthorizationUnavailable", 503);
      return new Set(member ? [key.groupId] : []); } },
  });
  return { service, get reads() { return reads; }, get writes() { return writes; }, get memberChecks() { return memberChecks; },
    set member(value: boolean) { member = value; }, set failure(value: boolean) { failure = value; },
    set record(value: ApiKeyRecord | null) { record = value; }, set groups(value: ApiKeyGroup[]) { groups = value; } };
}

test("すべての制限は未設定が既定で、呼び出し元どうしで初期値を共有しない", () => {
  const one = unlimitedApiKeyLimits(), two = unlimitedApiKeyLimits();
  assert.deepEqual(one, { version: 0, requestRate: null, tokensPerMinute: null, tokenQuota: null, updatedAt: null });
  assert.notEqual(one, two);
  assert.ok(apiKeyLimitProfileSchema.safeParse(one).success);
});

test("各項目を独立して有効化でき、APIMの動的設定範囲だけを受け付ける", () => {
  assert.ok(updateApiKeyLimitsSchema.safeParse(values).success);
  for (const invalid of [
    { ...values, expectedVersion: -1 }, { ...values, expectedVersion: 0.5 },
    { ...values, tokensPerMinute: 0 }, { ...values, tokensPerMinute: 2_147_483_648 },
    { ...values, requestRate: { calls: 1_000_001, renewalPeriodSeconds: 60 } },
    { ...values, requestRate: { calls: 1, renewalPeriodSeconds: 301 } },
    { ...values, requestRate: { calls: 1, renewalPeriodSeconds: 0 } },
    { ...values, tokenQuota: { tokens: 5, period: "monthly" } },
    { ...values, tokenQuota: { tokens: -1, period: "Monthly" } },
    { ...values, ownerId: actor.objectId }, { ...values, bandwidth: 100 },
  ]) assert.equal(updateApiKeyLimitsSchema.safeParse(invalid).success, false);
  assert.ok(updateApiKeyLimitsSchema.safeParse({ ...values, requestRate: null, tokenQuota: null }).success);
  assert.equal(updateApiKeyLimitsSchema.safeParse({ expectedVersion: 0 }).success, false);
});

test("所有グループの現メンバーが読取・更新・無制限への復帰を行い、古い版は上書きしない", async () => {
  const app = fixture();
  assert.deepEqual(await app.service.get(actor, key.id), unlimitedApiKeyLimits());
  const stored = await app.service.update(actor, key.id, values);
  assert.equal(stored.version, 1);
  assert.equal(stored.requestRate?.calls, 2500);
  await assert.rejects(app.service.update(actor, key.id, values), errorCode("LimitVersionConflict"));
  const reset = await app.service.update(actor, key.id, { expectedVersion: 1, requestRate: null, tokensPerMinute: null, tokenQuota: null });
  assert.equal(reset.version, 2);
  assert.equal(reset.tokenQuota, null);
  assert.equal(app.memberChecks, 4);
});

test("退職・脱退・所属照会失敗・承認取り消しでは制限の情報取得も更新も行わない", async () => {
  const app = fixture();
  app.member = false;
  await assert.rejects(app.service.get(actor, key.id), errorCode("NotFound"));
  await assert.rejects(app.service.update(actor, key.id, values), errorCode("NotFound"));
  app.failure = true;
  await assert.rejects(app.service.get(actor, key.id), errorCode("AuthorizationUnavailable"));
  app.failure = false; app.member = true; app.groups = [];
  await assert.rejects(app.service.update(actor, key.id, values), errorCode("NotFound"));
  assert.equal(app.reads, 0); assert.equal(app.writes, 0);
});

test("Graph取得後にSQLの現在規則で対象外となった観測を、古いactiveとして使わない", async () => {
  const app = fixture();
  app.groups = [{ id: key.groupId, displayName: "Group", directoryStatus: "ineligible",
    products: [{ id: key.productId, displayName: "Models", maxSubscriptions: 5 }] }];
  await assert.rejects(app.service.get(actor, key.id), errorCode("NotFound"));
  await assert.rejects(app.service.update(actor, key.id, values), errorCode("NotFound"));
  assert.equal(app.reads, 0); assert.equal(app.writes, 0);
});

test("他テナント・管理外ID・削除済み・作成中のキーの設定を操作しない", async () => {
  const app = fixture();
  await assert.rejects(app.service.get({ ...actor, tenantId: actor.objectId }, key.id), errorCode("AccessDenied"));
  await assert.rejects(app.service.get(actor, "master"), errorCode("NotFound"));
  app.record = null;
  await assert.rejects(app.service.get(actor, key.id), errorCode("NotFound"));
  for (const state of ["creating", "deleting", "deleted"] as const) {
    app.record = { ...key, state };
    await assert.rejects(app.service.update(actor, key.id, values), errorCode(state === "creating" ? "SubscriptionNotReady" : "NotFound"));
  }
  app.record = { ...key, state: "suspended" };
  assert.equal((await app.service.get(actor, key.id)).version, 0);
  assert.equal(app.writes, 0);
});

const origin = "https://portal.example.invalid";
/** 実際のHTTP分岐に、外部通信のない操作だけを差し込みます。 */
function httpFixture() {
  const calls: unknown[][] = [];
  const dependencies: ApiKeyLimitHttpDependencies = {
    readActor: async () => actor, readOrigin: () => origin,
    getService: () => ({ get: async (...args) => { calls.push(["get", ...args]); return unlimitedApiKeyLimits(); },
      update: async (...args) => { calls.push(["update", ...args]); return { ...unlimitedApiKeyLimits(), version: 1 }; } }),
  };
  return { dependencies, calls };
}
/** ブラウザーと同じOriginでプロファイル全体を更新する要求を作ります。 */
function put(body: unknown = values, headers: Record<string, string> = {}): Request {
  return new Request(`${origin}/api/api-keys/${key.id}/limits`, { method: "PUT",
    headers: { Origin: origin, "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
}

test("管理HTTPは認証済みGET/PUTを受け付け、全応答を非キャッシュにする", async () => {
  const { dependencies, calls } = httpFixture();
  const fetched = await handleApiKeyLimits(new Request(`${origin}/api/api-keys/${key.id}/limits`), dependencies, key.id);
  assert.equal(fetched.status, 200);
  assert.deepEqual(await fetched.json(), unlimitedApiKeyLimits());
  const saved = await handleApiKeyLimits(put(), dependencies, key.id);
  assert.equal(saved.status, 200);
  assert.equal(saved.headers.get("cache-control"), "no-store");
  assert.deepEqual(calls, [["get", actor, key.id], ["update", actor, key.id, values]]);
});

test("未認証・CSRF・不正ID・過大入力は設定サービスへ到達させない", async () => {
  const { dependencies, calls } = httpFixture();
  dependencies.readActor = async () => null;
  assert.equal((await handleApiKeyLimits(put(), dependencies, key.id)).status, 401);
  dependencies.readActor = async () => actor;
  for (const [request, id, status] of [
    [put(values, { Origin: "" }), key.id, 403],
    [put(values, { Origin: "https://foreign.invalid" }), key.id, 403],
    [put(values, { "Sec-Fetch-Site": "cross-site" }), key.id, 403],
    [put(), "master", 404],
    [put({ ...values, hidden: "x".repeat(9000) }), key.id, 400],
    [put({ ...values, ownerId: actor.objectId }), key.id, 400],
    [put(values, { "Content-Type": "text/plain" }), key.id, 400],
    [new Request(origin, { method: "POST" }), key.id, 405],
  ] as const) assert.equal((await handleApiKeyLimits(request, dependencies, id)).status, status);
  assert.equal(calls.length, 0);
});

test("版の競合は409を返し、内部例外の秘密情報は隠す", async () => {
  const { dependencies } = httpFixture();
  dependencies.getService = () => { throw new ApiKeyError("LimitVersionConflict", 409); };
  const conflict = await handleApiKeyLimits(put(), dependencies, key.id);
  assert.equal(conflict.status, 409);
  assert.deepEqual(await conflict.json(), { error: { code: "LimitVersionConflict" } });
  dependencies.getService = () => { throw new Error("password=private-test-value"); };
  const failure = await handleApiKeyLimits(put(), dependencies, key.id);
  assert.equal(failure.status, 503);
  assert.equal((await failure.text()).includes("private-test-value"), false);
});
