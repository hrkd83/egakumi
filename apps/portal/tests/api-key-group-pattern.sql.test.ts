import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test, type TestContext } from "node:test";
import { sql } from "kysely";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { SqlApiKeyRepository } from "../lib/api-keys/repository.ts";
import { SqlApiKeyLimitRepository } from "../lib/api-keys/limit-repository.ts";
import { configureApiKeyGroups } from "../scripts/api-key-group-config.ts";
import { configureApiKeyAdministration } from "../scripts/api-key-administration-config.ts";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";

/** 現在のスキーマで承認済みグループと有効なキーを作り、規則変更を検証します。 */
async function patternFixture(context: TestContext) {
  const { admin: database } = await createLocalSqlTestDatabase(context, false);
  const tenantId = randomUUID(), sourceId = randomUUID(), targetId = randomUUID();
  const actor = { tenantId, objectId: randomUUID() }, productId = "released-apis";
  const repository = new SqlApiKeyRepository(database), limits = new SqlApiKeyLimitRepository(database);
  const configurePattern = (groupNamePattern: string) => configureApiKeyAdministration(database, {
    tenantId, platformGroupId: sourceId, groupNamePattern,
    products: [{ productId, displayName: "Models", maxSubscriptions: 3 }],
  });
  await configureApiKeyGroups(database, tenantId, [sourceId, targetId].map((groupId) => ({ groupId,
    products: [{ productId, maxSubscriptions: 3 }] })));
  await configurePattern("^ORG_");
  await repository.observeGroups(tenantId, [sourceId, targetId].map((id) => ({ id,
    displayName: id === sourceId ? "ORG_Source" : "ORG_Target", securityEnabled: true,
    status: "active" as const, checkedAt: new Date().toISOString(), errorCode: null })));
  const usage = { clientType: "custom-system" as const, companyId: randomUUID(), contactEmail: "pattern-team@example.invalid" };
  await sql`INSERT dbo.portalCompany(tenantId,id,name,nameKey)
    VALUES(${tenantId},${usage.companyId},N'Pattern company',N'pattern company')`.execute(database);
  const reservation = await repository.reserve(actor, { ...usage, groupId: targetId, productId,
    displayName: "Pattern test", idempotencyKey: randomUUID() });
  const record = await repository.withLockedKey(actor, reservation.record.id, (locked) => locked.updateState("active"));
  const profile = await limits.update(actor, record, { expectedVersion: 0, requestRate: null, tokensPerMinute: 100, tokenQuota: null });
  return { tenantId, sourceId, targetId, actor, repository, limits, record, profile, configurePattern };
}

test("SQLのグループ登録と観測は現在の名前規則とセキュリティ属性で適格性を判定する", localSqlTest, async (context) => {
  const f = await patternFixture(context), candidateId = randomUUID();
  await f.configurePattern("^TEAM_[A-Z]+$");
  await f.repository.ensureRegistry(f.tenantId, [{ id: candidateId, displayName: "TEAM_ENGINEERS", securityEnabled: true }]);
  await assert.rejects(f.repository.ensureRegistry(f.tenantId, [{ id: randomUUID(), displayName: "ORG_Outside", securityEnabled: true }]),
    (error: unknown) => error instanceof ApiKeyError && error.code === "AccessDenied");
  await f.repository.observeGroups(f.tenantId, [{ id: candidateId, displayName: "TEAM_RENAMED", securityEnabled: true,
    status: "active", checkedAt: new Date().toISOString(), errorCode: null }]);
  assert.equal((await f.repository.getGroups(f.tenantId)).find((group) => group.id === candidateId)?.displayName, "TEAM_RENAMED");
  await f.repository.observeGroups(f.tenantId, [{ id: candidateId, displayName: "TEAM_RENAMED", securityEnabled: false,
    status: "active", checkedAt: new Date().toISOString(), errorCode: null }]);
  assert.equal((await f.repository.getGroups(f.tenantId)).find((group) => group.id === candidateId)?.directoryStatus, "ineligible");
  assert.deepEqual(await f.repository.getKey(f.tenantId, f.record.id), f.record);
  assert.deepEqual(await f.limits.get(f.tenantId, f.record.id), f.profile);
});

test("Graph確認後の規則変更でもSQL確定時に移管・制限変更・再確認を拒否する", localSqlTest, async (context) => {
  const f = await patternFixture(context);
  await assert.rejects(f.repository.transferOwnership(f.actor, f.record.id,
    { targetGroupId: f.sourceId, expectedSourceGroupId: f.targetId }, async () => { await f.configurePattern("^ORG_Target$"); }),
  (error: unknown) => error instanceof ApiKeyError && error.code === "TargetGroupNotAllowed");
  await f.configurePattern("^TEAM_");
  await assert.rejects(f.limits.update(f.actor, f.record, { expectedVersion: 1, requestRate: null, tokensPerMinute: 999, tokenQuota: null }),
    (error: unknown) => error instanceof ApiKeyError && error.code === "NotFound");
  const staleObservation = { id: f.targetId, displayName: "ORG_Target", securityEnabled: true,
    status: "active" as const, checkedAt: new Date().toISOString(), errorCode: null };
  await assert.rejects(f.repository.withLockedKey(f.actor, f.record.id, async (locked) => {
    await locked.confirmGroupApproval(staleObservation);
    await locked.markReviewed();
  }), (error: unknown) => error instanceof ApiKeyError && error.code === "NotFound");
  await f.repository.observeGroups(f.tenantId, [staleObservation]);
  assert.equal((await f.repository.getGroups(f.tenantId)).find((group) => group.id === f.targetId)?.directoryStatus, "ineligible");
  assert.deepEqual(await f.repository.getKey(f.tenantId, f.record.id), f.record);
  assert.deepEqual(await f.limits.get(f.tenantId, f.record.id), f.profile);
});
