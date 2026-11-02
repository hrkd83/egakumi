import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import type { ApiKeyRecord, CreateApiKeyInput } from "../lib/api-keys/contracts.ts";
import { actor, otherActor, groupId, input, errorCode, fixture } from "./helpers/api-key-service-fixture.ts";

test("個人ではなくグループが所有し、別の所属メンバーも棚卸とキー表示を行える", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  assert.equal(key.groupId, groupId);
  assert.equal(key.state, "active");
  assert.equal(key.apimSubscriptionId, key.id, "通常発行の実APIM IDは生成した内部UUIDだけを使う");
  assert.equal("primaryKey" in key, false);
  assert.equal(app.calls.includes("secrets"), false);
  const overview = await app.service.overview(otherActor);
  assert.equal(overview.keys[0].id, key.id);
  app.repository.groups[0].displayName = "改名後のグループ";
  assert.equal((await app.service.overview(otherActor)).groups[0].displayName, "改名後のグループ");
  const revealed = await app.service.act(otherActor, key.id, "reveal");
  assert.deepEqual(revealed, { primaryKey: "secret-primary", secondaryKey: "secret-secondary" });
  const reviewed = await app.service.act(otherActor, key.id, "review") as ApiKeyRecord;
  assert.ok(reviewed.lastReviewedAt);
  assert.deepEqual(app.repository.auditEvents.map((event) => event.action), ["create", "reveal", "review"]);
  assert.equal(app.repository.auditEvents[1].actorId, otherActor.objectId);
});

test("本人接続用の実行資格情報は台帳IDと異なる実APIM購読IDを返し、取得のたびに認可する", async () => {
  const app = fixture();
  app.allowedApis.get("released-apis")!.add("graph-profile-v1");
  const record = await app.service.create(actor, input);
  const actualId = "actual-sample-connection-subscription";
  app.repository.records.set(record.id, { ...record, apimSubscriptionId: actualId });
  app.subscriptions.set(actualId, { ...app.subscriptions.get(record.id)!, id: actualId });
  app.subscriptions.delete(record.id);
  assert.deepEqual(await app.service.executionCredential(actor, record.id, "graph-profile-v1"), { subscriptionId: actualId, key: "secret-primary" });
  assert.equal(await app.service.executionKey(actor, record.id, "graph-profile-v1"), "secret-primary");
  assert.deepEqual(app.requestedSubscriptions.filter((call) => call.operation === "secrets").map((call) => call.id), [actualId, actualId]);
  app.members.delete(actor.objectId);
  const reads = app.requestedSubscriptions.length;
  await assert.rejects(app.service.executionCredential(actor, record.id, "graph-profile-v1"), errorCode("NotFound"));
  assert.equal(app.requestedSubscriptions.length, reads);
});

test("実行資格情報は停止購読・Graph非公開製品・異なるスコープから秘密を取得しない", async () => {
  for (const condition of ["suspended", "missing-api", "changed-scope"]) {
    const app = fixture();
    app.allowedApis.get("released-apis")!.add("graph-profile-v1");
    const record = await app.service.create(actor, input);
    if (condition === "suspended") app.subscriptions.get(record.id)!.state = "suspended";
    if (condition === "missing-api") app.allowedApis.get("released-apis")!.delete("graph-profile-v1");
    if (condition === "changed-scope") app.subscriptions.get(record.id)!.scope = "/apim/products/foreign-product";
    await assert.rejects(app.service.executionCredential(actor, record.id, "graph-profile-v1"), errorCode("NotFound"));
    assert.equal(app.calls.includes("secrets"), false);
  }
});

test("APIMの表示名は会社名、Portalの用途名は入力値のまま保持する", async () => {
  const app = fixture();
  app.repository.companyName = "株式会社サンプル";
  const record = await app.service.create(actor, input);
  assert.equal(app.subscriptions.get(record.apimSubscriptionId)?.displayName, "株式会社サンプル");
  assert.equal(record.displayName, input.displayName);
  assert.equal(record.companyName, "株式会社サンプル");
});

test("会社が取得できない発行予約から用途名を代用して新規APIM購読を作らない", async () => {
  for (const name of [null, "", "   "]) {
    const app = fixture(); app.repository.companyName = name;
    await assert.rejects(app.service.create(actor, input), errorCode("CompanyNotAvailable"));
    assert.equal(app.calls.includes("create"), false);
    assert.equal(app.calls.includes("secrets"), false);
    assert.equal([...app.repository.records.values()][0]?.state, "creating");
  }
});

test("APIMの会社表示名は100文字以内にし、長い名称でも文字を分断せず用途名は保持する", async () => {
  const app = fixture(); app.repository.companyName = "会".repeat(99) + "😀会社";
  const record = await app.service.create(actor, input);
  assert.equal(app.subscriptions.get(record.id)?.displayName, "会".repeat(99));
  assert.equal(record.companyName, app.repository.companyName);
  assert.equal(record.displayName, input.displayName);
});

test("発行結果不明の間に会社が改名されても未知の表示名を持つ購読を引き取らない", async () => {
  for (const changedName of ["別の表示名", "Test company"]) {
    const app = fixture(); app.interruptedCreate = true;
    await assert.rejects(app.service.create(actor, input), errorCode("UpstreamUnavailable"));
    const record = [...app.repository.records.values()][0]!;
    app.repository.records.set(record.id, { ...record, companyName: "改名後の会社" });
    app.subscriptions.get(record.id)!.displayName = changedName;
    app.interruptedCreate = false;
    await assert.rejects(app.service.create(actor, input), errorCode("UpstreamConflict"));
    assert.equal(app.calls.filter((call) => call === "create").length, 1);
    assert.equal(app.repository.records.get(record.id)?.state, "creating");
  }
});

test("発行と管理操作の排他中は同じSQL接続で承認を確認し、追加接続を待たない", async () => {
  const app = fixture();
  app.repository.rejectAdditionalConnectionsWhileLocked = true;
  const key = await app.service.create(actor, input);
  await app.service.act(actor, key.id, "review");
  await app.service.act(actor, key.id, "reveal");
  assert.deepEqual(app.repository.auditEvents.map((event) => event.action), ["create", "review", "reveal"]);
});

test("GraphのactiveがSQLの現在規則で対象外になった場合は発行も移管候補検索も許可しない", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  app.repository.storedDirectoryStatuses.set(groupId, "ineligible");
  const callsBefore = [...app.calls];
  await assert.rejects(app.service.create(actor, { ...input, idempotencyKey: randomUUID() }), errorCode("AccessDenied"));
  await assert.rejects(app.service.ownerTargets(actor, key.id, "Team"), errorCode("NotFound"));
  assert.deepEqual(app.calls, callsBefore);
});

test("退職やグループ脱退は次の操作へ即反映し、キー取得を行わない", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  app.members.delete(actor.objectId);
  const callsBefore = app.calls.length;
  for (const action of ["reveal", "review", "suspend", "resume", "delete"]) {
    await assert.rejects(app.service.act(actor, key.id, action), errorCode("NotFound"));
  }
  assert.equal(app.calls.length, callsBefore);
  assert.deepEqual(await app.service.overview(actor), { groups: [], keys: [] });
  // 発行者が脱退してもグループの別メンバーは引き継げます。
  assert.ok("primaryKey" in await app.service.act(otherActor, key.id, "reveal"));
});

test("所属チェックが失敗したときや製品承認が外れたときは閉じて失敗する", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  app.failGraph = true;
  const callsBefore = app.calls.length;
  await assert.rejects(app.service.act(actor, key.id, "reveal"), errorCode("AuthorizationUnavailable"));
  assert.equal(app.calls.length, callsBefore);
  app.failGraph = false;
  app.repository.groups[0].products = [];
  await assert.rejects(app.service.act(actor, key.id, "delete"), errorCode("NotFound"));
  assert.equal(app.calls.length, callsBefore);
  await assert.rejects(app.service.create(actor, { ...input, productId: "unknown-product" } as CreateApiKeyInput), errorCode("InvalidInput"));
});

test("別テナント・共有購読・管理外ID・存在しないキーを操作しない", async () => {
  const app = fixture();
  await assert.rejects(app.service.overview({ ...actor, tenantId: otherActor.objectId }), errorCode("AccessDenied"));
  await assert.rejects(app.service.create({ ...actor, tenantId: otherActor.objectId }, input), errorCode("AccessDenied"));
  for (const id of ["master", "model-users", "portal-../foreign", `portal-${randomUUID()}`]) {
    await assert.rejects(app.service.act(actor, id, "reveal"), errorCode("NotFound"));
  }
  assert.equal(app.calls.length, 0);
  assert.equal(app.repository.reserveCalls, 0);
});

test("APIMの実スコープが変更された場合は表示・更新・削除を拒否する", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  app.subscriptions.get(key.id)!.scope = "/apim/apis";
  for (const action of ["reveal", "review", "suspend", "delete"]) {
    await assert.rejects(app.service.act(actor, key.id, action), errorCode("NotFound"));
  }
  assert.equal(app.calls.includes("secrets"), false);
  assert.equal(app.calls.includes("delete"), false);
  assert.equal(app.repository.auditEvents.length, 1);
});

test("同じ発行依頼は重複作成せず、上流の成功応答を失っても予約から再開できる", async () => {
  const app = fixture();
  app.interruptedCreate = true;
  await assert.rejects(app.service.create(actor, input), errorCode("UpstreamUnavailable"));
  assert.equal(app.repository.records.size, 1);
  assert.equal([...app.repository.records.values()][0].state, "creating");
  app.interruptedCreate = false;
  const key = await app.service.create(actor, input);
  assert.equal(key.state, "active");
  assert.equal((await app.service.create(actor, input)).id, key.id);
  assert.equal(app.calls.filter((call) => call === "create").length, 1);
  assert.deepEqual(app.repository.auditEvents.map((event) => event.action), ["create"]);
  await assert.rejects(app.service.create(actor, { ...input, displayName: "別システム" }), errorCode("IdempotencyConflict"));
});

test("停止・再開・削除の状態と監査を揃え、削除済みの秘密は取得できない", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  assert.equal((await app.service.act(actor, key.id, "suspend") as ApiKeyRecord).state, "suspended");
  assert.equal(app.subscriptions.get(key.id)!.state, "suspended");
  assert.equal((await app.service.act(otherActor, key.id, "resume") as ApiKeyRecord).state, "active");
  assert.equal((await app.service.act(actor, key.id, "delete") as ApiKeyRecord).state, "deleted");
  assert.equal((await app.service.act(actor, key.id, "delete") as ApiKeyRecord).state, "deleted");
  await assert.rejects(app.service.act(actor, key.id, "reveal"), errorCode("NotFound"));
  await assert.rejects(app.service.create(actor, input), errorCode("IdempotencyConflict"));
  assert.deepEqual((await app.service.overview(actor)).keys, []);
  assert.deepEqual(app.repository.auditEvents.map((event) => event.action), ["create", "suspend", "resume", "delete"]);
});

test("ETagが取得できない場合は無条件上書きや削除を行わない", async () => {
  const app = fixture();
  const key = await app.service.create(actor, input);
  delete app.subscriptions.get(key.id)!.etag;
  await assert.rejects(app.service.act(actor, key.id, "suspend"), errorCode("UpstreamUnavailable"));
  assert.equal(app.calls.includes("suspended"), false);
  await assert.rejects(app.service.act(actor, key.id, "delete"), errorCode("UpstreamUnavailable"));
  assert.equal(app.calls.includes("delete"), false);
});

test("発行が中断した予約もグループのメンバーが削除でき、孤立した発行枠を残さない", async () => {
  const app = fixture();
  app.interruptedCreate = true;
  await assert.rejects(app.service.create(actor, input), errorCode("UpstreamUnavailable"));
  const key = [...app.repository.records.values()][0];
  assert.equal(key.state, "creating");
  assert.equal((await app.service.act(otherActor, key.id, "delete") as ApiKeyRecord).state, "deleted");
  assert.equal(app.subscriptions.size, 0);
});

test("通常の発行予約を既存購読名へ付け替えても、既存IDの新規作成へ到達しない", async () => {
  const app = fixture(); app.interruptedCreate = true;
  await assert.rejects(app.service.create(actor, input), errorCode("UpstreamUnavailable"));
  const record = [...app.repository.records.values()][0]!;
  app.repository.records.set(record.id, { ...record, apimSubscriptionId: "verification-model-users" });
  const callsBefore = [...app.calls];
  await assert.rejects(app.service.create(actor, input), errorCode("UpstreamConflict"));
  assert.deepEqual(app.calls, callsBefore);
});
