import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { sql } from "kysely";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";
import { configureApiKeyGroups } from "../scripts/api-key-group-config.ts";
import { configureApiKeyAdministration } from "../scripts/api-key-administration-config.ts";
import { apiKeySubscriptionImportSchema, importApiKeySubscriptions, stableApiKeyImportId } from "../scripts/api-key-subscription-import.ts";
import { bootstrapApiKeySubscriptions } from "../scripts/bootstrap-api-key-subscriptions.ts";
import { migrateAuth } from "../scripts/auth-migrations.ts";

test("実SQLへの既存購読登録は一度だけで、移管・制限変更・削除を再配備でも保持する", localSqlTest, async (context) => {
  const { SqlApiKeyRepository } = await import("../lib/api-keys/repository.ts");
  const { ApiKeyError } = await import("../lib/api-keys/contracts.ts");
  const { admin: database } = await createLocalSqlTestDatabase(context, false);
  const tenantId = randomUUID(), sourceId = randomUUID(), targetId = randomUUID(), actorId = randomUUID();
  const resource = `/subscriptions/${randomUUID()}/resourceGroups/rg-import-test/providers/Microsoft.ApiManagement/service/apim-import-test`;
  const productId = "model-users", actualId = `import-test-${randomUUID()}`;
  await configureApiKeyGroups(database, tenantId, [sourceId, targetId].map((groupId) => ({ groupId,
    products: [{ productId, maxSubscriptions: 4 }] })));
  await configureApiKeyAdministration(database, { tenantId, platformGroupId: sourceId, groupNamePattern: "^ORG_",
    products: [{ productId, displayName: "Models", maxSubscriptions: 4 }] });
  const manifest = apiKeySubscriptionImportSchema.parse({ tenantId, platformGroupId: sourceId, apimResourceId: resource,
    subscriptions: [{ subscriptionId: actualId, productId, displayName: "既存の基盤管理テスト", state: "active", scope: `${resource}/products/${productId}` }] });
  const first = await importApiKeySubscriptions(database, manifest, resource);
  assert.equal(first.imported, 1); assert.equal(first.skipped, 0);
  const id = stableApiKeyImportId({ ...manifest, subscriptionId: actualId });
  const initial = (await sql<{ id: string; apimSubscriptionId: string; source: string; creator: string | null; budgetMode: string;
    limitRows: number; userAudits: number; importAudits: number }>`SELECT id,apimSubscriptionId,creationSource AS source,createdByObjectId AS creator,budgetMode,
    (SELECT COUNT(*) FROM dbo.portalApiKeyLimit) AS limitRows,(SELECT COUNT(*) FROM dbo.portalApiKeyAudit) AS userAudits,
    (SELECT COUNT(*) FROM dbo.portalApiKeyImportAudit) AS importAudits FROM dbo.portalApiKey WHERE id=${id}`.execute(database)).rows[0];
  assert.deepEqual(initial, { id, apimSubscriptionId: actualId, source: "deployment", creator: null, budgetMode: "unlimited",
    limitRows: 0, userAudits: 0, importAudits: 1 });
  await sql`UPDATE dbo.portalApiKey SET groupId=${targetId},state='suspended',lastReviewedAt=SYSUTCDATETIME()
    WHERE tenantId=${tenantId} AND id=${id}`.execute(database);
  await sql`INSERT INTO dbo.portalApiKeyLimit(tenantId,apiKeyId,version,tokensPerMinute,updatedByObjectId)
    VALUES(${tenantId},${id},1,321,${actorId})`.execute(database);
  const before = (await sql`SELECT * FROM dbo.portalApiKey WHERE id=${id}`.execute(database)).rows[0];
  const changed = { ...manifest, subscriptions: [{ ...manifest.subscriptions[0]!, subscriptionId: actualId.toUpperCase(), displayName: "上書きしない", state: "active" as const }] };
  assert.equal((await importApiKeySubscriptions(database, changed, resource)).skipped, 1);
  assert.deepEqual((await sql`SELECT * FROM dbo.portalApiKey WHERE id=${id}`.execute(database)).rows[0], before);
  await sql`UPDATE dbo.portalApiKey SET state='deleted' WHERE id=${id}`.execute(database);
  const bootstrap = await bootstrapApiKeySubscriptions(database, { tenantId, platformGroupId: sourceId, apimResourceId: resource,
    subscriptions: [{ subscriptionId: actualId, productId, displayName: "再構築でも復活しない" }] }, {
    scopeFor: () => { throw new Error("must not contact APIM"); }, get: async () => { throw new Error("must not contact APIM"); },
    create: async () => { throw new Error("must not contact APIM"); },
  });
  assert.equal(bootstrap.skipped, 1); assert.equal(bootstrap.created, 0);
  await migrateAuth(database);
  const preserved = (await sql<{ groupId: string; state: string; tpm: number; importAudits: number; runId: string; manifestHash: string }>`
    SELECT k.groupId,k.state,l.tokensPerMinute AS tpm,a.runId,a.manifestHash,
      (SELECT COUNT(*) FROM dbo.portalApiKeyImportAudit) AS importAudits FROM dbo.portalApiKey k
    JOIN dbo.portalApiKeyLimit l ON l.apiKeyId=k.id AND l.tenantId=k.tenantId
    JOIN dbo.portalApiKeyImportAudit a ON a.apiKeyId=k.id AND a.tenantId=k.tenantId WHERE k.id=${id}`.execute(database)).rows[0];
  assert.deepEqual(preserved, { groupId: targetId, state: "deleted", tpm: 321, importAudits: 1, runId: first.runId, manifestHash: first.manifestHash });
  await sql`UPDATE dbo.portalApiKeyProduct SET maxSubscriptions=1 WHERE tenantId=${tenantId} AND groupId=${sourceId}`.execute(database);
  await assert.rejects(importApiKeySubscriptions(database, { ...manifest, subscriptions: ["a", "b"].map((suffix) => ({
    ...manifest.subscriptions[0]!, subscriptionId: `new-${suffix}` })) }, resource));
  assert.equal((await sql<{ count: number }>`SELECT COUNT(*) AS count FROM dbo.portalApiKey`.execute(database)).rows[0]!.count, 1);
  assert.equal((await sql<{ count: number }>`SELECT COUNT(*) AS count FROM dbo.portalApiKeyImportAudit`.execute(database)).rows[0]!.count, 1);

  const repository = new SqlApiKeyRepository(database), actor = { tenantId, objectId: actorId };
  await repository.observeGroups(tenantId, [{ id: sourceId, displayName: "ORG_Source", securityEnabled: true,
    status: "active", checkedAt: new Date().toISOString(), errorCode: null }]);
  // 通常製品の初期化と通常発行が同じ枠を競合することを調べます。
  const bootstrapProductId = "released-apis";
  const usage = { clientType: "custom-system" as const, companyId: randomUUID(), contactEmail: "import-team@example.invalid" };
  await sql`INSERT dbo.portalCompany(tenantId,id,name,nameKey)
    VALUES(${tenantId},${usage.companyId},N'Import test company',N'import test company')`.execute(database);
  await configureApiKeyGroups(database, tenantId, [{ groupId: sourceId,
    products: [{ productId, maxSubscriptions: 1 }, { productId: bootstrapProductId, maxSubscriptions: 1 }] },
    { groupId: targetId, products: [{ productId, maxSubscriptions: 4 }] }]);
  await configureApiKeyAdministration(database, { tenantId, platformGroupId: sourceId, groupNamePattern: "^ORG_",
    products: [{ productId: bootstrapProductId, displayName: "Released APIs", maxSubscriptions: 1 }] });
  const intent = (subscriptionId: string) => ({ tenantId, platformGroupId: sourceId, apimResourceId: resource,
    subscriptions: [{ subscriptionId, productId: bootstrapProductId, displayName: "Concurrent bootstrap" }] });

  // HTTPの間に通常発行が割り込んでも、creating予約が既に枠を使用しています。
  const concurrentId = `concurrent-${randomUUID()}`;
  let startCreate: () => void = () => undefined, finishCreate: () => void = () => undefined;
  const createStarted = new Promise<void>((resolve) => { startCreate = resolve; });
  const allowCreated = new Promise<void>((resolve) => { finishCreate = resolve; });
  type RemoteSubscription = { id: string; displayName: string; scope: string; state: string };
  let remote: RemoteSubscription | null = null, createdCount = 0;
  const creating = bootstrapApiKeySubscriptions(database, intent(concurrentId), {
    scopeFor: () => `${resource}/products/${bootstrapProductId}`, get: async () => remote,
    create: async (actualId, _product, displayName) => {
      createdCount++; startCreate(); await allowCreated;
      remote = { id: actualId, displayName, scope: `${resource}/products/${bootstrapProductId}`, state: "active" }; return remote;
    },
  });
  const concurrentKeyId = stableApiKeyImportId({ tenantId, apimResourceId: resource, subscriptionId: concurrentId });
  let deleted = false;
  let deleting: Promise<void> | undefined;
  try {
    await Promise.race([createStarted, creating.then(() => { throw new Error("作成ゲートへ到達せず終了しました"); })]);
    assert.equal((await repository.getKey(tenantId, concurrentKeyId))?.state, "creating");
    await assert.rejects(repository.reserve(actor, { ...usage, groupId: sourceId, productId: bootstrapProductId, displayName: "UI request", idempotencyKey: randomUUID() }),
      (error: unknown) => error instanceof ApiKeyError && error.code === "SubscriptionLimitReached");
    const availability = (await sql<{ available: number }>`SELECT APPLOCK_TEST('public',${`portal-api-key:${tenantId}:${concurrentKeyId}`},'Exclusive','Session') AS available`.execute(database)).rows[0];
    assert.equal(availability?.available, 0, "HTTP中も同じキーの管理操作をSession排他で保護する");
    deleting = repository.withLockedKey(actor, concurrentKeyId, async (context) => {
      await context.updateState("deleting"); remote = null; await context.updateState("deleted"); deleted = true;
    });
    assert.equal(deleted, false);
    finishCreate();
    await Promise.all([creating, deleting]);
  } finally {
    finishCreate();
    await Promise.allSettled([creating, ...(deleting ? [deleting] : [])]);
  }
  assert.equal(createdCount, 1); assert.equal(remote, null); assert.equal(deleted, true);
  assert.equal((await repository.getKey(tenantId, concurrentKeyId))?.state, "deleted");
  const noResurrection = await bootstrapApiKeySubscriptions(database, intent(concurrentId), {
    scopeFor: () => { throw new Error("deleted must not call APIM"); }, get: async () => { throw new Error("deleted must not call APIM"); },
    create: async () => { throw new Error("deleted must not recreate APIM"); },
  });
  assert.equal(noResurrection.skipped, 1); assert.equal(noResurrection.created, 0);

  // 応答を失っても予約はロールバックせず、再実行で実在を確認して確定します。
  const retryId = `retry-${randomUUID()}`; let retryRemote: RemoteSubscription | null = null, retryCreates = 0;
  const retryApim = { scopeFor: () => `${resource}/products/${bootstrapProductId}`, get: async () => retryRemote,
    create: async (actualId: string, _product: string, displayName: string) => {
      retryCreates++; retryRemote = { id: actualId, displayName, scope: `${resource}/products/${bootstrapProductId}`, state: "active" };
      throw new ApiKeyError("UpstreamUnavailable", 503);
    } };
  await assert.rejects(bootstrapApiKeySubscriptions(database, intent(retryId), retryApim));
  const retryKeyId = stableApiKeyImportId({ tenantId, apimResourceId: resource, subscriptionId: retryId });
  assert.equal((await repository.getKey(tenantId, retryKeyId))?.state, "creating");
  const resumed = await bootstrapApiKeySubscriptions(database, intent(retryId), retryApim);
  assert.equal(resumed.imported, 1); assert.equal(resumed.created, 0); assert.equal(retryCreates, 1);
  assert.equal((await repository.getKey(tenantId, retryKeyId))?.state, "active");
  await repository.withLockedKey(actor, retryKeyId, async (context) => { await context.updateState("deleting"); await context.updateState("deleted"); });

  // 二つの初期化が重なっても、一つの予約・APIM購読・登録監査だけを作ります。
  const parallelId = `parallel-${randomUUID()}`; let parallelRemote: RemoteSubscription | null = null, parallelCreates = 0;
  const parallelApim = { scopeFor: () => `${resource}/products/${bootstrapProductId}`, get: async () => parallelRemote,
    create: async (actualId: string, _product: string, displayName: string) => {
      parallelCreates++; parallelRemote = { id: actualId, displayName, scope: `${resource}/products/${bootstrapProductId}`, state: "active" }; return parallelRemote;
    } };
  const parallel = await Promise.all([bootstrapApiKeySubscriptions(database, intent(parallelId), parallelApim),
    bootstrapApiKeySubscriptions(database, intent(parallelId), parallelApim)]);
  assert.equal(parallelCreates, 1); assert.equal(parallel.reduce((sum, value) => sum + value.imported, 0), 1);
  const parallelKeyId = stableApiKeyImportId({ tenantId, apimResourceId: resource, subscriptionId: parallelId });
  assert.equal((await sql<{ count: number }>`SELECT COUNT(*) AS count FROM dbo.portalApiKeyImportAudit WHERE apiKeyId=${parallelKeyId}`.execute(database)).rows[0]!.count, 1);
});
