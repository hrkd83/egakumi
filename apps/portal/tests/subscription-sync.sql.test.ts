import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import { sql } from "kysely";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";
import { SqlSubscriptionSyncRepository } from "../lib/subscription-sync/repository.ts";

test("同期SQLの監査は失敗でも残り、アプリに監査削除を許さず、台帳登録後の誤削除を拒否する", localSqlTest, async (context) => {
  const { admin, runtime: app, name } = await createLocalSqlTestDatabase(context);
  const tenantId = randomUUID(), groupId = randomUUID(), id = `portal-${randomUUID()}`, actor = { tenantId, objectId: randomUUID() };
  await sql`INSERT dbo.portalApiKeyGroup(tenantId,groupId,displayName) VALUES(${tenantId},${groupId},N'ORG_Test')`.execute(admin);
  await sql`INSERT dbo.portalApiKeyProduct(tenantId,groupId,productId,displayName) VALUES(${tenantId},${groupId},'model-users',N'Model users')`.execute(admin);
  await sql`INSERT dbo.portalApiKey(id,tenantId,groupId,productId,displayName,state,createdByObjectId,idempotencyKey,apimSubscriptionId,creationSource)
    VALUES(${id},${tenantId},${groupId},'model-users',N'Test','active',NULL,${randomUUID()},'model-test','deployment')`.execute(admin);
  const repo = new SqlSubscriptionSyncRepository(app);
  assert.equal((await repo.list(tenantId))[0].apimSubscriptionId, "model-test");
  assert.equal((await repo.list(randomUUID())).length, 0);
  const entry = { planId: randomUUID(), apiKeyId: id, subscriptionId: "model-test", action: "update" as const,
    phase: "attempted" as const, code: null, snapshotHash: "a".repeat(64) };
  await assert.rejects(repo.locked(actor, id, async (context) => {
    assert.equal((await context.read())?.id, id);
    await context.audit(entry); throw new Error("remote interrupted");
  }));
  assert.equal((await repo.history(tenantId))[0].phase, "attempted", "例外でも監査は消えない");
  await assert.rejects(repo.locked(actor, id, (context) => context.audit(entry)), "同じ確認内容を再実行しない");
  await repo.locked(actor, "apim:unregistered", async (context) => {
    assert.equal(await context.read(), null);
    await context.audit({ ...entry, apiKeyId: null, subscriptionId: "unregistered", action: "delete-unregistered" });
  });
  await assert.rejects(repo.locked(actor, "apim:model-test", (context) => context.read()));
  await assert.rejects(repo.assertCanonical("sql-test.database.windows.net", name), "ローカルSQLをAzure SQLとして認めない");
  await assert.rejects(sql`DELETE FROM dbo.portalSubscriptionSyncAudit`.execute(app));
  await assert.rejects(sql`UPDATE dbo.portalSubscriptionSyncAudit SET code='changed'`.execute(app));
});
