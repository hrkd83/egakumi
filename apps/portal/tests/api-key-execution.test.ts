import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { actor, groupId, productId, input, errorCode, fixture } from "./helpers/api-key-service-fixture.ts";

test("実行候補は現在所属するグループのactive購読と対象APIの実製品所属で絞る", async () => {
  const app = fixture();
  const first = await app.service.create(actor, input);
  const second = await app.service.create(actor, { ...input, idempotencyKey: randomUUID() });
  const paused = await app.service.create(actor, { ...input, idempotencyKey: randomUUID() });
  const unrelated = await app.service.create(actor, { ...input, idempotencyKey: randomUUID() });
  const hiddenGroupId = "81111111-1111-4111-8111-111111111111";
  app.repository.groups.push({ id: hiddenGroupId, displayName: "Hidden", products: [{ id: productId, displayName: "Released", maxSubscriptions: 5 }] });
  app.excludedGroupMemberships.add(hiddenGroupId);
  app.repository.records.set(`portal-${randomUUID()}`, { ...first, id: `portal-${randomUUID()}`, groupId: hiddenGroupId });
  app.repository.records.set(paused.id, { ...paused, state: "suspended" });
  app.repository.groups[0].products.push({ id: "pii-users", displayName: "PII", maxSubscriptions: 5 });
  app.repository.records.set(unrelated.id, { ...unrelated, productId: "pii-users" });
  app.calls.length = 0;
  const choices = await app.service.executionChoices(actor, "openai-inference-v1");
  assert.deepEqual(choices.keys.map((key) => key.id), [first.id, second.id]);
  assert.deepEqual(choices.groups.map((group) => group.id), [groupId]);
  assert.equal(app.calls.filter((call) => call.startsWith(`product-api:${productId}:`)).length, 1);
  assert.equal(app.calls.filter((call) => call === "secrets" || call === "get").length, 0);
  assert.deepEqual(await app.service.executionChoices(actor, "pii-v1"), { keys: [], groups: [] });
});

test("実行用主キーは直前に所属とAPIMの状態・API所属を確認し、reveal監査や状態更新をしない", async () => {
  const app = fixture();
  const record = await app.service.create(actor, input);
  app.repository.rejectAdditionalConnectionsWhileLocked = true;
  app.calls.length = 0;
  const audit = [...app.repository.auditEvents];
  assert.equal(await app.service.executionKey(actor, record.id, "openai-inference-v1"), "secret-primary");
  assert.deepEqual(app.calls, ["get", `product-api:${productId}:openai-inference-v1`, "secrets"]);
  assert.deepEqual(app.repository.auditEvents, audit);
  assert.equal(app.repository.records.get(record.id)?.state, "active");
  app.members.delete(actor.objectId);
  const before = [...app.calls];
  await assert.rejects(app.service.executionKey(actor, record.id, "openai-inference-v1"), errorCode("NotFound"));
  assert.deepEqual(app.calls, before);
});

test("実行候補表示後に停止・製品移動・API所属解除が起きた場合は主キーを取得しない", async () => {
  for (const change of ["local-state", "remote-state", "remote-scope", "remote-id", "missing", "api-removed"] as const) {
    const app = fixture();
    const record = await app.service.create(actor, input);
    assert.equal((await app.service.executionChoices(actor, "openai-inference-v1")).keys.length, 1);
    if (change === "local-state") app.repository.records.set(record.id, { ...record, state: "suspended" });
    if (change === "remote-state") app.subscriptions.get(record.id)!.state = "suspended";
    if (change === "remote-scope") app.subscriptions.get(record.id)!.scope = "/apim/products/foreign";
    if (change === "remote-id") app.subscriptions.get(record.id)!.id = "foreign";
    if (change === "missing") app.subscriptions.delete(record.id);
    if (change === "api-removed") app.allowedApis.get(productId)!.clear();
    app.calls.length = 0;
    await assert.rejects(app.service.executionKey(actor, record.id, "openai-inference-v1"), errorCode("NotFound"));
    assert.equal(app.calls.includes("secrets"), false, change);
  }
});

test("実行キーは台帳の内部IDから既存検証購読を利用し、不正IDやGraph障害では秘密を取得しない", async () => {
  const app = fixture();
  const record = await app.service.create(actor, input);
  const remote = app.subscriptions.get(record.id)!;
  const importedId = "verification-model-users";
  app.subscriptions.delete(record.id); app.subscriptions.set(importedId, { ...remote, id: importedId });
  app.repository.records.set(record.id, { ...record, apimSubscriptionId: importedId });
  assert.equal(await app.service.executionKey(actor, record.id, "openai-inference-v1"), "secret-primary");
  app.calls.length = 0;
  for (const [id, api] of [[importedId, "openai-inference-v1"], [record.id, "../foreign"], ["master", "openai-inference-v1"], [`portal-${randomUUID()}`, "openai-inference-v1"]]) {
    await assert.rejects(app.service.executionKey(actor, id, api), errorCode("NotFound"));
  }
  app.failGraph = true;
  await assert.rejects(app.service.executionKey(actor, record.id, "openai-inference-v1"), errorCode("AuthorizationUnavailable"));
  assert.equal(app.calls.includes("secrets"), false);
});
