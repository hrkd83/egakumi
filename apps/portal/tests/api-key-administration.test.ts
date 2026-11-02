import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { ApiKeyAdministrationService } from "../lib/api-key-administration/service.ts";
import { handleApiKeyAdministration, type ApiKeyAdministrationHttpDependencies } from "../lib/api-key-administration/http.ts";
import type { ApiKeyAdministrationRepository } from "../lib/api-key-administration/repository.ts";
import type { AdministrationGroup } from "../lib/api-key-administration/contracts.ts";
import { readReconciliationDirectoryEnvironment } from "../scripts/reconcile-api-key-groups.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const actor = { tenantId, objectId: "22222222-2222-4222-8222-222222222222" };
const groupId = "33333333-3333-4333-8333-333333333333";
const platformGroupId = "44444444-4444-4444-8444-444444444444";
const origin = "https://portal.example.invalid";
const code = (value: string) => (error: unknown) => error instanceof ApiKeyError && error.code === value;
const group: AdministrationGroup = { id: groupId, displayName: "ORG_営業", directoryStatus: "unknown", lastCheckedAt: null,
  lastErrorCode: null, activeSubscriptions: 2, affectedSubscriptions: 2 };

/** 管理者判定・台帳の読取・Graph観測保存の順序と内容を、外部I/Oなしで調べます。 */
function fixture() {
  const calls: string[] = [];
  let admin = true; let directoryFails = false;
  const groups = [{ ...group }];
  const repository: ApiKeyAdministrationRepository = {
    getSettings: async () => ({ platformGroupId, groupNamePattern: "^ORG_" }), getCatalog: async () => [],
    listTrackedGroups: async () => { calls.push("dashboard"); return { groups, nextCursor: null }; },
    getSummary: async () => { calls.push("summary"); return { totalGroups: 1200, activeGroups: 1199, deletedGroups: 0,
      ineligibleGroups: 0, unknownGroups: 1, activeSubscriptions: 2400, affectedSubscriptions: 2,
      checkedAt: groups[0]!.lastCheckedAt, syncErrors: groups[0]!.lastErrorCode ? 1 : 0 }; },
    listGroupsToRefresh: async (_tenant, limit) => { calls.push("tracked"); assert.equal(limit, 100); return [groupId]; },
  };
  const service = new ApiKeyAdministrationService({ tenantId, repository,
    isPlatformAdmin: async () => { calls.push("membership"); if (directoryFails) throw new ApiKeyError("AuthorizationUnavailable", 503); return admin; },
    readGroups: async (ids) => { calls.push("graph"); assert.deepEqual(ids, [groupId]); return new Map(); },
    saveGroups: async (tenant, observations) => {
      calls.push("observe"); assert.equal(tenant, tenantId); assert.equal(observations.length, 1);
      const observation = observations[0]!;
      assert.equal(observation.id, groupId); assert.equal(observation.status, "unknown");
      groups[0]!.lastCheckedAt = observation.checkedAt; groups[0]!.lastErrorCode = observation.errorCode;
    }, now: () => Date.parse("2026-10-30T00:00:00Z"),
  });
  const dependencies: ApiKeyAdministrationHttpDependencies = { readActor: async () => actor,
    readOrigin: () => origin, getService: () => service };
  return { calls, service, dependencies, repository, setAdmin: (value: boolean) => { admin = value; },
    failDirectory: () => { directoryFails = true; } };
}

test("管理グループ所属を確認する前にダッシュボード台帳を照会しない", async () => {
  const f = fixture();
  f.setAdmin(false);
  assert.equal(await f.service.isAdmin(actor), false);
  await assert.rejects(f.service.overview(actor), code("AccessDenied"));
  await assert.rejects(f.service.refresh(actor), code("AccessDenied"));
  assert.deepEqual(f.calls, ["membership", "membership", "membership"]);
});

test("所属確認のGraph障害は503であり、権限なしや空の成功一覧に置き換えない", async () => {
  const f = fixture(); f.failDirectory();
  await assert.rejects(f.service.isAdmin(actor), code("AuthorizationUnavailable"));
  await assert.rejects(f.service.overview(actor), code("AuthorizationUnavailable"));
  assert.equal(f.calls.includes("dashboard"), false);
});

test("登録済みIDだけを再確認し、応答欠落はunknownとして保存してから結果を返す", async () => {
  const f = fixture();
  const result = await f.service.refresh(actor);
  assert.deepEqual(f.calls, ["membership", "tracked", "graph", "observe", "dashboard", "summary"]);
  assert.equal(result.syncErrors, 1);
  assert.equal(result.checkedAt, "2026-10-30T00:00:00.000Z");
  assert.equal(result.groups[0]!.directoryStatus, "unknown");
  assert.equal(result.summary.totalGroups, 1200);
  assert.equal(result.groups.length, 1);
});

test("定期照合は管理HTTP権限を偽造せず、観測の件数だけを返す", async () => {
  const f = fixture(); f.setAdmin(false);
  const result = await f.service.reconcileTrackedGroups();
  assert.deepEqual(f.calls, ["tracked", "graph", "observe"]);
  assert.deepEqual(result, { checkedGroups: 1, unknownGroups: 1, completedAt: "2026-10-30T00:00:00.000Z" });
});

test("管理アクセス確認はboolだけを返し、未認証ならサービスすら生成しない", async () => {
  const f = fixture(); f.setAdmin(false);
  const request = new Request(`${origin}/api/api-key-administration/access`);
  const response = await handleApiKeyAdministration(request, f.dependencies, true);
  assert.deepEqual(await response.json(), { isAdmin: false });
  assert.deepEqual(f.calls, ["membership"]);
  assert.equal(response.headers.get("cache-control"), "no-store");
  f.dependencies.readActor = async () => null;
  f.dependencies.getService = () => { throw new Error("must not initialize"); };
  assert.equal((await handleApiKeyAdministration(request, f.dependencies, true)).status, 401);
});

test("POSTのOrigin・本文サイズ・任意グループ指定を拒否してから照合処理を呼ぶ", async () => {
  const cases = [
    { body: '{"action":"refresh"}', origin: "https://outside.invalid" },
    { body: JSON.stringify({ action: "refresh", groupIds: [groupId] }), origin },
    { body: JSON.stringify({ action: "refresh", padding: "x".repeat(2000) }), origin },
  ];
  for (const value of cases) {
    const f = fixture();
    const response = await handleApiKeyAdministration(new Request(`${origin}/api/api-key-administration`, {
      method: "POST", headers: { Origin: value.origin, "Content-Type": "application/json" }, body: value.body,
    }), f.dependencies);
    assert.ok([400, 403].includes(response.status));
    assert.deepEqual(f.calls, []);
  }
});

test("管理GETの認可失敗を403、Graph障害を503で返し、内部例外は露出しない", async () => {
  const f = fixture(); f.setAdmin(false);
  const request = new Request(`${origin}/api/api-key-administration`);
  assert.equal((await handleApiKeyAdministration(request, f.dependencies)).status, 403);
  f.failDirectory();
  const response = await handleApiKeyAdministration(request, f.dependencies);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: { code: "AuthorizationUnavailable" } });
  assert.equal(f.calls.includes("dashboard"), false);
});

test("管理ページは1000グループ超でも取得でき、UUIDカーソルと全体集計を別に扱う", async () => {
  const f = fixture();
  let requestedCursor: string | undefined;
  f.repository.listTrackedGroups = async (_tenant, cursor) => {
    requestedCursor = cursor;
    return { groups: [group], nextCursor: platformGroupId };
  };
  const request = new Request(`${origin}/api/api-key-administration?cursor=${groupId}`);
  const response = await handleApiKeyAdministration(request, f.dependencies);
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.equal(requestedCursor, groupId);
  assert.equal(value.groups.length, 1);
  assert.equal(value.nextCursor, platformGroupId);
  assert.equal(value.summary.totalGroups, 1200);
  for (const query of ["cursor=bad", `cursor=${groupId}&cursor=${platformGroupId}`, `tenantId=${tenantId}`]) {
    assert.equal((await handleApiKeyAdministration(new Request(`${origin}/api/api-key-administration?${query}`), f.dependencies)).status, 400);
  }
});

test("更新後は初回ページに戻し、ページカーソルを観測対象の指定に流用しない", async () => {
  const f = fixture();
  let requestedCursor: string | undefined = "not-called";
  f.repository.listTrackedGroups = async (_tenant, cursor) => {
    requestedCursor = cursor;
    return { groups: [group], nextCursor: platformGroupId };
  };
  const response = await handleApiKeyAdministration(new Request(`${origin}/api/api-key-administration`, {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: '{"action":"refresh"}',
  }), f.dependencies);
  assert.equal(response.status, 200);
  assert.equal(requestedCursor, undefined);
  assert.ok(f.calls.includes("tracked"));
});

test("定期CLIの設定はGraph3項目だけで検証でき、Cookie署名鍵やPortalURLを要求しない", () => {
  const environment = { MICROSOFT_CLIENT_ID: actor.objectId, MICROSOFT_CLIENT_SECRET: "fixture-secret", MICROSOFT_TENANT_ID: tenantId };
  assert.equal(readReconciliationDirectoryEnvironment(environment).microsoftTenantId, tenantId);
  assert.throws(() => readReconciliationDirectoryEnvironment({ ...environment, MICROSOFT_CLIENT_ID: "bad" }),
    (error: unknown) => error instanceof ApiKeyError && !error.message.includes("fixture-secret"));
});
