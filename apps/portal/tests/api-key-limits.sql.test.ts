import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { sql } from "kysely";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { unlimitedApiKeyLimits } from "../lib/api-keys/limit-contracts.ts";
import { configureApiKeyGroups } from "../scripts/api-key-group-config.ts";
import { configureApiKeyAdministration } from "../scripts/api-key-administration-config.ts";

test("SQLの制限設定は既定無制限・版競合・監査・テナント分離・削除を同時に守る", localSqlTest, async (context) => {
  const { SqlApiKeyRepository } = await import("../lib/api-keys/repository.ts");
  const { SqlApiKeyLimitRepository } = await import("../lib/api-keys/limit-repository.ts");
  const { admin, runtime: application } = await createLocalSqlTestDatabase(context);
  const keys = new SqlApiKeyRepository(application), limits = new SqlApiKeyLimitRepository(application);
  const tenantId = randomUUID(), groupId = randomUUID(), actor = { tenantId, objectId: randomUUID() };
  const usage = { clientType: "custom-system" as const, companyId: randomUUID(), contactEmail: "limit-team@example.invalid" };
  await sql`INSERT dbo.portalCompany(tenantId,id,name,nameKey)
    VALUES(${tenantId},${usage.companyId},N'Limit test company',N'limit test company')`.execute(admin);
  await configureApiKeyGroups(admin, tenantId, [{ groupId,
    products: [{ productId: "released-apis", maxSubscriptions: 1 }] }]);
  await configureApiKeyAdministration(admin, { tenantId, platformGroupId: groupId, groupNamePattern: "^ORG_",
    products: [{ productId: "released-apis", displayName: "Released APIs", maxSubscriptions: 1 }] });
  await keys.observeGroups(tenantId, [{ id: groupId, displayName: "ORG_LimitTest", securityEnabled: true,
    status: "active", checkedAt: new Date().toISOString(), errorCode: null }]);
  const reserved = await keys.reserve(actor, { ...usage, groupId, productId: "released-apis", displayName: "limit-test", idempotencyKey: randomUUID() });
  const record = await keys.withLockedKey(actor, reserved.record.id, (context) => context.updateState("active"));
  assert.deepEqual(await limits.get(tenantId, record.id), unlimitedApiKeyLimits());
  assert.deepEqual(await limits.getGatewayProfile(tenantId, record.id), unlimitedApiKeyLimits());
  assert.equal(await limits.getGatewayProfile(randomUUID(), record.id), null);
  assert.equal(await limits.getGatewayProfile(tenantId, "existing-shared-key"), null);
  assert.equal(await limits.getGatewayProfile(tenantId, record.id + "-different"), null, "長い未管理IDを既存キーの長さへ切り詰めない");

  const value = { expectedVersion: 0, requestRate: { calls: 12000, renewalPeriodSeconds: 60 },
    tokensPerMinute: 250000, tokenQuota: { tokens: 5000000, period: "Monthly" as const } };
  const outcomes = await Promise.allSettled([limits.update(actor, record, value), limits.update(actor, record, value)]);
  assert.equal(outcomes.filter((result) => result.status === "fulfilled").length, 1);
  const rejected = outcomes.find((result) => result.status === "rejected");
  assert.ok(rejected?.status === "rejected" && rejected.reason instanceof ApiKeyError && rejected.reason.code === "LimitVersionConflict");
  const saved = await limits.get(tenantId, record.id);
  assert.equal(saved.version, 1);
  assert.equal(saved.requestRate?.calls, 12000);
  assert.deepEqual(await limits.getGatewayProfile(tenantId, record.id), saved);
  await assert.rejects(limits.update({ ...actor, tenantId: randomUUID() }, record, { ...value, expectedVersion: 1 }),
    (error: unknown) => error instanceof ApiKeyError && error.status === 404);

  // 無制限に戻す操作にも新しい版を与え、古い画面からの上書きを引き続き防ぎます。
  const cleared = await limits.update(actor, record, { expectedVersion: 1, requestRate: null, tokensPerMinute: null, tokenQuota: null });
  assert.equal(cleared.version, 2);
  assert.equal(cleared.requestRate, null);
  const audit = (await sql<{ amount: number }>`SELECT COUNT(*) AS amount FROM dbo.portalApiKeyLimitAudit
    WHERE tenantId=${tenantId} AND apiKeyId=${record.id}`.execute(admin)).rows[0];
  assert.equal(audit?.amount, 2, "設定変更と監査は同じトランザクションでのみ確定する");
  await assert.rejects(sql`UPDATE dbo.portalApiKeyLimit SET requestCalls=1, requestRenewalSeconds=NULL
    WHERE tenantId=${tenantId} AND apiKeyId=${record.id}`.execute(application));
  await assert.rejects(sql`DELETE FROM dbo.portalApiKeyLimitAudit WHERE tenantId=${tenantId}`.execute(application));

  await keys.withLockedKey(actor, record.id, (context) => context.updateState("suspended"));
  await assert.rejects(limits.getGatewayProfile(tenantId, record.id),
    (error: unknown) => error instanceof ApiKeyError && error.code === "SubscriptionNotReady");
  await keys.withLockedKey(actor, record.id, async (context) => {
    await context.updateState("deleting");
    await context.updateState("deleted");
  });
  await assert.rejects(limits.update(actor, record, { ...value, expectedVersion: 2 }),
    (error: unknown) => error instanceof ApiKeyError && error.status === 404);
  await assert.rejects(limits.getGatewayProfile(tenantId, record.id),
    (error: unknown) => error instanceof ApiKeyError && error.code === "SubscriptionNotReady");
});
