import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { sql } from "kysely";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { configureApiKeyGroups } from "../scripts/api-key-group-config.ts";
import { configureApiKeyAdministration } from "../scripts/api-key-administration-config.ts";

test("ローカルSQLで発行上限・冪等性・テナント境界・監査権限を検証する", localSqlTest, async (context) => {
  const { SqlApiKeyRepository } = await import("../lib/api-keys/repository.ts");
  const { admin, runtime: application } = await createLocalSqlTestDatabase(context);
  const repository = new SqlApiKeyRepository(application);
  const tenantId = randomUUID(), groupId = randomUUID(), actor = { tenantId, objectId: randomUUID() };
  const input = { groupId, productId: "released-apis" as const, displayName: "integration-test", idempotencyKey: randomUUID(),
    clientType: "custom-system" as const, companyId: randomUUID(), contactEmail: "integration-team@example.invalid" };
  await sql`INSERT dbo.portalCompany(tenantId,id,name,nameKey)
    VALUES(${tenantId},${input.companyId},N'Integration company',N'integration company')`.execute(admin);
  await configureApiKeyGroups(admin, tenantId, [{ groupId,
    products: [{ productId: input.productId, maxSubscriptions: 1 }] }]);
  await configureApiKeyAdministration(admin, { tenantId, platformGroupId: groupId, groupNamePattern: "^ORG_",
    products: [{ productId: input.productId, displayName: "Models", maxSubscriptions: 1 }] });
  assert.equal((await repository.getGroups(tenantId))[0]?.displayName, groupId, "未観測の名前は自己申告でなくObject IDの仮表示にする");
  await repository.observeGroups(tenantId, [{ id: groupId, displayName: "ORG_IntegrationTest", securityEnabled: true,
    status: "active", checkedAt: new Date().toISOString(), errorCode: null }]);
  const groups = await repository.getGroups(tenantId);
  assert.equal(groups[0]?.id, groupId);
  assert.equal(groups[0]?.products[0]?.maxSubscriptions, 1);
  await configureApiKeyGroups(admin, tenantId, [{ groupId,
    products: [{ productId: input.productId, maxSubscriptions: 1 }] }]);
  assert.deepEqual(await repository.getGroups(tenantId), groups, "再配備でGraph観測済みの名前・状態・確認時刻を上書きしない");
  const competing = await Promise.allSettled(Array.from({ length: 3 }, () => repository.reserve(actor,
    { ...input, idempotencyKey: randomUUID() })));
  const success = competing.filter((result) => result.status === "fulfilled");
  assert.equal(success.length, 1, "並行要求も発行枠を超えない");
  for (const result of competing.filter((value) => value.status === "rejected")) {
    assert.ok(result.reason instanceof ApiKeyError);
    assert.equal(result.reason.code, "SubscriptionLimitReached");
  }
  const record = success[0]!.value.record;
  assert.equal(record.budgetMode, "unlimited");
  assert.equal(await repository.getKey(randomUUID(), record.id), null);
  assert.deepEqual(await repository.listKeys(randomUUID(), [groupId]), []);
  assert.deepEqual(await repository.listKeys(tenantId, []), []);
  assert.equal((await repository.listKeys(tenantId, [groupId])).length, 1);

  // 実際のDBから同じ発行依頼IDを読み戻し、変更・再試行の扱いを検証します。
  const saved = (await sql<{ idempotencyKey: string }>`SELECT idempotencyKey FROM dbo.portalApiKey
    WHERE tenantId=${tenantId} AND id=${record.id}`.execute(admin)).rows[0]!;
  const repeated = await repository.reserve(actor, { ...input, idempotencyKey: saved.idempotencyKey });
  assert.equal(repeated.created, false);
  assert.equal(repeated.record.id, record.id);
  await assert.rejects(repository.reserve(actor, { ...input, idempotencyKey: saved.idempotencyKey, displayName: "changed" }),
    (error: unknown) => error instanceof ApiKeyError && error.code === "IdempotencyConflict");
  await repository.withLockedKey(actor, record.id, async (context) => {
    await context.updateState("active");
    await context.audit("create");
    await context.markReviewed();
    await context.audit("review");
  });
  assert.ok((await repository.getKey(tenantId, record.id))?.lastReviewedAt);
  await repository.withLockedKey(actor, record.id, async (context) => {
    await context.updateState("suspended");
    await context.audit("suspend");
  });

  const audit = (await sql<{ amount: number }>`SELECT COUNT(*) AS amount FROM dbo.portalApiKeyAudit WHERE tenantId=${tenantId}`.execute(admin)).rows[0];
  assert.equal(audit?.amount, 3);
  const permissions = (await sql<{ registryWrite: number; auditDelete: number; keyDelete: number }>`SELECT
    HAS_PERMS_BY_NAME('dbo.portalApiKeyGroup', 'OBJECT', 'UPDATE') AS registryWrite,
    HAS_PERMS_BY_NAME('dbo.portalApiKeyAudit', 'OBJECT', 'DELETE') AS auditDelete,
    HAS_PERMS_BY_NAME('dbo.portalApiKey', 'OBJECT', 'DELETE') AS keyDelete`.execute(application)).rows[0];
  assert.deepEqual(permissions, { registryWrite: 0, auditDelete: 0, keyDelete: 0 });
  await assert.rejects(sql`UPDATE dbo.portalApiKeyGroup SET enabled=0 WHERE tenantId=${tenantId}`.execute(application));

  await repository.withLockedKey(actor, record.id, async (context) => {
    await context.updateState("deleting");
  });
  await assert.rejects(repository.reserve(actor, { ...input, idempotencyKey: randomUUID() }),
    (error: unknown) => error instanceof ApiKeyError && error.code === "SubscriptionLimitReached",
    "削除処理中の購読も発行枠を占有する");
  await repository.withLockedKey(actor, record.id, async (context) => {
    await context.updateState("deleted");
    await context.audit("delete");
  });
  assert.deepEqual(await repository.listKeys(tenantId, [groupId]), []);
  assert.equal((await repository.getKey(tenantId, record.id))?.state, "deleted");
  assert.equal((await repository.reserve(actor, input)).created, true, "削除確定後にだけ発行枠を再利用する");
});
