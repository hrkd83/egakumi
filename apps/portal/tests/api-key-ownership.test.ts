import assert from "node:assert/strict";
import { test } from "node:test";
import { actor, groupId, productId, input, errorCode, fixture } from "./helpers/api-key-service-fixture.ts";

test("移管先への未所属を表示し、所有元メンバーは直接移管できるが移管後の表示権限を失う", async () => {
  const app = fixture();
  const record = await app.service.create(actor, input);
  const targetId = "71111111-1111-4111-8111-111111111111";
  app.repository.groups.push({ id: targetId, displayName: "ORG_Target", products: [{ id: productId, displayName: "Models", maxSubscriptions: 5 }] });
  app.excludedGroupMemberships.add(targetId);
  const targets = await app.service.ownerTargets(actor, record.id, "ORG");
  assert.equal(targets.groups[0]?.id, targetId);
  assert.equal(targets.groups[0]?.isMember, false);
  const callsBefore = [...app.calls];
  const transferred = await app.service.transfer(actor, record.id, { targetGroupId: targetId, expectedSourceGroupId: groupId });
  assert.equal(transferred.groupId, targetId);
  assert.equal(transferred.id, record.id);
  assert.equal(transferred.productId, record.productId);
  assert.deepEqual(app.calls, callsBefore, "移管ではAPIMの更新・キー取得・再発行を行わない");
  assert.equal(app.subscriptions.get(record.id)?.state, "active");
  await assert.rejects(app.service.act(actor, record.id, "reveal"), errorCode("NotFound"));
  assert.equal((await app.service.overview(actor)).keys.length, 0);
  assert.equal(app.repository.auditEvents.at(-1)?.action, "transfer");
});

test("削除・対象外・不明なグループ、古い所有元、脱退した移管元を拒否する", async () => {
  const app = fixture(); const record = await app.service.create(actor, input);
  const targetId = "81111111-1111-4111-8111-111111111111";
  app.repository.groups.push({ id: targetId, displayName: "ORG_Target", products: [{ id: productId, displayName: "Models", maxSubscriptions: 1 }] });
  for (const state of ["deleted", "ineligible", "unknown"] as const) {
    app.directoryStates.set(targetId, state);
    await assert.rejects(app.service.transfer(actor, record.id, { targetGroupId: targetId, expectedSourceGroupId: groupId }),
      errorCode(state === "unknown" ? "AuthorizationUnavailable" : "TargetGroupNotAllowed"));
  }
  app.directoryStates.delete(targetId);
  await assert.rejects(app.service.transfer(actor, record.id, { targetGroupId: targetId, expectedSourceGroupId: targetId }), errorCode("OwnershipConflict"));
  app.members.delete(actor.objectId);
  await assert.rejects(app.service.ownerTargets(actor, record.id, "ORG"), errorCode("NotFound"));
  await assert.rejects(app.service.transfer(actor, record.id, { targetGroupId: targetId, expectedSourceGroupId: groupId }), errorCode("NotFound"));
  assert.equal(app.repository.records.get(record.id)?.groupId, groupId);
});

test("登録済み既存購読は内部UUIDで認可し、実APIM IDで表示・停止・再開・削除する", async () => {
  const app = fixture(), key = await app.service.create(actor, input);
  const actualId = "verification-model-users", targetId = "91111111-1111-4111-8111-111111111111";
  app.repository.records.set(key.id, { ...key, apimSubscriptionId: actualId });
  const remote = app.subscriptions.get(key.id)!;
  app.subscriptions.delete(key.id); app.subscriptions.set(actualId, { ...remote, id: actualId });
  app.repository.groups.push({ id: targetId, displayName: "ORG_Target", products: [{ id: productId, displayName: "Models", maxSubscriptions: 5 }] });
  app.calls.length = 0; app.requestedSubscriptions.length = 0;
  await assert.rejects(app.service.act(actor, actualId, "reveal"), errorCode("NotFound"));
  const transferred = await app.service.transfer(actor, key.id, { expectedSourceGroupId: groupId, targetGroupId: targetId });
  assert.equal(transferred.id, key.id); assert.equal(transferred.apimSubscriptionId, actualId);
  assert.equal(app.requestedSubscriptions.length, 0, "移管は実APIM IDやキーを更新しない");
  for (const action of ["reveal", "review", "suspend", "resume", "delete"]) await app.service.act(actor, key.id, action);
  assert.ok(app.requestedSubscriptions.every((request) => request.id === actualId));
  assert.ok(app.requestedSubscriptions.some((request) => request.operation === "secrets"));
  assert.ok(app.requestedSubscriptions.some((request) => request.operation === "delete"));
  assert.equal(app.repository.records.get(key.id)?.state, "deleted");
});
